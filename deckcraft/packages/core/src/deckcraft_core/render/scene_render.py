"""Render scenes with Playwright's Chromium (seek-and-screenshot) and encode with ffmpeg.
Colour is explicit: RGB -> BT.709 limited range, tagged (fixes untagged yuvj420p, plan P8)."""
from __future__ import annotations
import base64
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

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
    return f"<!doctype html><html><head><meta charset='utf-8'></head><body><script>{gsap}</script><script>{kit}</script></body></html>"


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


class SceneRenderer:
    def __init__(self, blobs=None):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._html = _page_html()
        self._blobs = blobs

    def close(self):
        self._browser.close()
        self._pw.stop()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def _open(self, scene: dict, theme: dict, orientation: str, duration: float, reveals: dict | None = None):
        scene = hydrate_scene(scene, self._blobs)
        portrait = orientation != "landscape"
        page = self._browser.new_page(viewport={"width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080})
        page.set_content(self._html)
        page.evaluate("([s,t,o,d,r]) => SceneKit.render(s,t,o,{duration:d,reveals:r})", [scene, theme, orientation, duration, reveals])
        page.evaluate("SceneKit.ready()")
        return page

    def lint(self, scene: dict, theme: dict, orientation: str = "portrait") -> LintResult:
        page = self._open(scene, theme, orientation, 6)
        try:
            r = page.evaluate("SceneKit.lint()")
        finally:
            page.close()
        return LintResult(r["findings"], r["emptyRatio"])

    def poster(self, scene: dict, theme: dict, orientation: str = "portrait", t: float = 99) -> bytes:
        page = self._open(scene, theme, orientation, 6)
        try:
            page.evaluate("(t)=>SceneKit.seek(t)", t)
            return page.screenshot(type="png")
        finally:
            page.close()

    def render_video(self, scene: dict, theme: dict, out: Path, orientation: str = "portrait",
                     duration: float = 6.0, fps: int = 30, audio: Path | None = None,
                     reveals: dict | None = None) -> Path:
        ff = shutil.which("ffmpeg")
        if not ff:
            raise RuntimeError("ffmpeg not found")
        page = self._open(scene, theme, orientation, duration, reveals)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ff, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-"]
        if audio:
            cmd += ["-i", str(audio), "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest"]
        cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps),
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                "-movflags", "+faststart", str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(int(duration * fps)):
                page.evaluate("(t)=>SceneKit.seek(t)", i / fps)
                proc.stdin.write(page.screenshot(type="png"))
            proc.stdin.close()
            err = proc.stderr.read().decode()
            if proc.wait() != 0:
                raise RuntimeError(f"ffmpeg failed: {err}")
        finally:
            page.close()
        return out
