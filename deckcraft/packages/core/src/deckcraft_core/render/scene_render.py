"""Render scenes with Playwright's Chromium (seek-and-screenshot) and encode with ffmpeg.
RenderPool runs persistent Chromium worker threads with frame-range sharding and provable
static-frame deduplication. Colour is explicit: RGB -> BT.709 limited range, tagged (plan P8)."""
from __future__ import annotations
import base64
import hashlib
import json
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from ..platform import unthrottle, unthrottle_tree

ROOT = Path(__file__).resolve().parents[5]  # .../deckcraft
KIT = ROOT / "web" / "scene-kit"
THEMES = ROOT / "themes"


def load_theme(theme_id: str) -> dict:
    return json.loads((THEMES / f"{theme_id}.json").read_text(encoding="utf-8"))


def kit_hash() -> str:
    """Part of every cache key, so a scene-kit change invalidates cached frames automatically."""
    h = hashlib.sha256()
    for p in (KIT / "scene-kit.js", KIT / "vendor" / "gsap.min.js"):
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def spec_hash(scene: dict, theme: dict, orientation: str, duration: float, fps: int) -> str:
    blob = json.dumps([scene, theme, orientation, duration, fps, kit_hash()], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _page_html() -> str:
    gsap = (KIT / "vendor" / "gsap.min.js").read_text(encoding="utf-8")
    kit = (KIT / "scene-kit.js").read_text(encoding="utf-8")
    return f"<!doctype html><html><head><meta charset='utf-8'><script>{gsap}</script><script>{kit}</script></head><body></body></html>"


@dataclass
class LintResult:
    findings: list[dict]
    empty_ratio: float

    @property
    def errors(self) -> list[dict]:
        return [f for f in self.findings if f["severity"] == "error"]


def _img_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def hydrate_scene(scene: dict, blobs) -> dict:
    """Populate image_url as a data URI from the blob store for ImageScene rendering."""
    if scene.get("kind") == "image" and not scene.get("image_url") and scene.get("image_sha"):
        sha = scene["image_sha"]
        if blobs is not None and blobs.has(sha):
            raw = blobs.get(sha)
            b64 = base64.b64encode(raw).decode("ascii")
            return {**scene, "image_url": f"data:{_img_mime(raw)};base64,{b64}"}
    return scene


class _Worker:
    """Single Playwright + Chromium page bound permanently to one dedicated thread."""

    def __init__(self, html: str):
        self._html = html
        self._q: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._init_err: BaseException | None = None
        self._closed = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def wait_ready(self) -> None:
        self._ready.wait()
        if self._init_err is not None:
            raise self._init_err

    def _run(self) -> None:
        pw = browser = page = None
        cur_size = (1080, 1920)
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1080, "height": 1920})
            page.set_content(self._html)
            unthrottle_tree()
        except BaseException as exc:  # noqa: BLE001
            self._init_err = exc
            self._ready.set()
            return
        self._ready.set()

        def load(scene: dict, theme: dict, orientation: str, duration: float, reveals: dict | None) -> None:
            nonlocal cur_size
            portrait = orientation != "landscape"
            size = (1080 if portrait else 1920, 1920 if portrait else 1080)
            if size != cur_size:
                page.set_viewport_size({"width": size[0], "height": size[1]})
                cur_size = size
            page.evaluate("([s,t,o,d,r]) => SceneKit.render(s,t,o,{duration:d,reveals:r})",
                          [scene, theme, orientation, duration, reveals])
            page.evaluate("SceneKit.ready()")

        def exec_op(op: str, args: tuple):
            if op == "load":
                load(*args)
                return None
            if op == "load_and_keys":
                scene, theme, orientation, duration, reveals, n_frames, fps = args
                load(scene, theme, orientation, duration, reveals)
                return page.evaluate(
                    "([n, fps]) => Array.from({length: n}, (_, i) => SceneKit.frameKey(i / fps))",
                    [n_frames, fps],
                )
            if op == "lint":
                scene, theme, orientation = args
                load(scene, theme, orientation, 6, None)
                r = page.evaluate("SceneKit.lint()")
                return LintResult(r["findings"], r["emptyRatio"])
            if op == "poster":
                scene, theme, orientation, t = args
                load(scene, theme, orientation, 6, None)
                page.evaluate("(t)=>SceneKit.seek(t)", t)
                return page.screenshot(type="png")
            if op == "capture":
                indices, fps = args
                out: dict[int, bytes] = {}
                for idx in indices:
                    page.evaluate("(t)=>SceneKit.seek(t)", idx / fps)
                    out[idx] = page.screenshot(type="png")
                return out
            raise ValueError(f"Unknown worker op: {op}")

        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                op, args, rq = item
                try:
                    rq.put((True, exec_op(op, args)))
                except BaseException as exc:  # noqa: BLE001
                    rq.put((False, exc))
        finally:
            for obj, meth in ((page, "close"), (browser, "close"), (pw, "stop")):
                if obj is not None:
                    try:
                        getattr(obj, meth)()
                    except Exception:  # noqa: BLE001
                        pass

    def submit(self, op: str, *args) -> queue.Queue:
        self.wait_ready()
        rq: queue.Queue = queue.Queue(maxsize=1)
        self._q.put((op, args, rq))
        return rq

    def call(self, op: str, *args):
        ok, val = self.submit(op, *args).get()
        if not ok:
            raise val
        return val

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._q.put(None)
            self._t.join(timeout=10)


class RenderPool:
    """Pool of persistent Chromium workers with frame-range sharding and static-frame dedupe."""

    def __init__(self, workers: int = 3, blobs=None):
        n = max(1, int(workers))
        self._blobs = blobs
        self._html = _page_html()
        self._workers = [_Worker(self._html) for _ in range(n)]
        for w in self._workers:
            w.wait_ready()

    def close(self) -> None:
        for w in self._workers:
            w.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def lint(self, scene: dict, theme: dict, orientation: str = "portrait") -> LintResult:
        return self._workers[0].call("lint", hydrate_scene(scene, self._blobs), theme, orientation)

    def poster(self, scene: dict, theme: dict, orientation: str = "portrait", t: float = 99) -> bytes:
        return self._workers[0].call("poster", hydrate_scene(scene, self._blobs), theme, orientation, t)

    def render_video(self, scene: dict, theme: dict, out: Path, orientation: str = "portrait",
                     duration: float = 6.0, fps: int = 30, audio: Path | None = None,
                     reveals: dict | None = None, dedupe_static: bool = True) -> Path:
        ff = shutil.which("ffmpeg")
        if not ff:
            raise RuntimeError("ffmpeg not found")
        scene = hydrate_scene(scene, self._blobs)
        n_frames = max(1, int(duration * fps))

        w0 = self._workers[0]
        if dedupe_static:
            keys = w0.call("load_and_keys", scene, theme, orientation, duration, reveals, n_frames, fps)
        else:
            w0.call("load", scene, theme, orientation, duration, reveals)
            keys = [None] * n_frames

        first_for_key: dict[str, int] = {}
        capture_of: list[int] = []
        to_capture: list[int] = []
        for i, k in enumerate(keys):
            if k is None:
                capture_of.append(i)
                to_capture.append(i)
            elif k not in first_for_key:
                first_for_key[k] = i
                capture_of.append(i)
                to_capture.append(i)
            else:
                capture_of.append(first_for_key[k])

        k_workers = min(len(self._workers), max(1, len(to_capture) // 4))
        load_futs = [w.submit("load", scene, theme, orientation, duration, reveals)
                     for w in self._workers[1:k_workers]]

        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ff, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-"]
        if audio:
            cmd += ["-i", str(audio), "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest"]
        cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps),
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                "-movflags", "+faststart", str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        unthrottle(proc.pid)

        for fut in load_futs:
            ok, val = fut.get()
            if not ok:
                proc.terminate()
                raise val

        pngs: dict[int, bytes] = {}
        frame_cursor = 0
        wave_cap = k_workers * 12

        def flush_ready() -> None:
            nonlocal frame_cursor
            while frame_cursor < n_frames and capture_of[frame_cursor] in pngs:
                idx = capture_of[frame_cursor]
                proc.stdin.write(pngs[idx])
                if keys[frame_cursor] is None:
                    pngs.pop(idx, None)
                frame_cursor += 1

        try:
            for pos in range(0, len(to_capture), wave_cap):
                batch = to_capture[pos:pos + wave_cap]
                chunk = max(1, -(-len(batch) // k_workers))
                futs = []
                for wi in range(k_workers):
                    sub = batch[wi * chunk:(wi + 1) * chunk]
                    if sub:
                        futs.append(self._workers[wi].submit("capture", sub, fps))
                for fut in futs:
                    ok, part = fut.get()
                    if not ok:
                        raise part
                    pngs.update(part)
                flush_ready()
            flush_ready()
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            if proc.wait() != 0:
                raise RuntimeError(f"ffmpeg failed: {err}")
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
            raise
        finally:
            for pipe in (proc.stdin, proc.stderr):
                if pipe and not pipe.closed:
                    try:
                        pipe.close()
                    except Exception:  # noqa: BLE001
                        pass
        return out


class SceneRenderer(RenderPool):
    """Single-worker convenience wrapper kept for backwards compatibility."""

    def __init__(self, blobs=None):
        super().__init__(workers=1, blobs=blobs)
