"""Export one module: per-scene audio -> mastered -> frames -> segment (cached) -> concat -> chapters + captions."""
from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..render.scene_render import RenderPool, SceneRenderer, spec_hash
from ..script.beats import Beat
from ..speech.tts import SpeechService, master_audio, measure_loudness
from .music import add_music_bed
from .subtitles import Cue, ffmetadata, split_cues, to_srt, to_vtt


@dataclass
class ExportResult:
    mp4: Path
    srt: Path
    vtt: Path
    duration: float
    chapters: list[tuple[float, float, str]]
    loudness_lufs: float
    cached_segments: int = 0
    findings: list[str] = field(default_factory=list)


def _title(scene: dict) -> str:
    for k in ("title", "heading", "headline", "term", "label", "situation", "myth"):
        if scene.get(k):
            return str(scene[k])[:60]
    return scene["kind"].title()


def export_module(scenes: list[tuple[dict, list[Beat]]], theme: dict, speech: SpeechService,
                  renderer: SceneRenderer | RenderPool,
                  out_dir: Path, name: str, orientation: str = "portrait", fps: int = 30,
                  intro: tuple[dict, list[Beat]] | None = None, outro: tuple[dict, list[Beat]] | None = None,
                  music: Path | None = None, card_hold: float = 3.0, sub_lang: str = "eng") -> ExportResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".work"
    work.mkdir(exist_ok=True)
    segs, cues, chapters, t0, cached, notes = [], [], [], 0.0, 0, []

    seq = ([intro] if intro else []) + list(scenes) + ([outro] if outro else [])
    for i, (scene, beats) in enumerate(seq):
        lint = renderer.lint(scene, theme, orientation)  # quality gate: blocks export
        if lint.errors:
            raise RuntimeError(f"Layout lint failed for scene {i} ({scene['kind']}): {lint.errors[:2]}")
        raw = work / f"s{i}.raw.wav"
        is_card = (intro and i == 0) or (outro and i == len(seq) - 1)
        sa = speech.scene_audio(beats, raw, min_duration=card_hold if is_card else 0.0)
        mastered = work / f"s{i}.wav"
        master_audio(raw, mastered)
        reveals = {str(b.reveal): round(tm.start, 3) for b, tm in zip(beats, sa.timings) if b.reveal is not None}
        key = spec_hash(scene, theme, orientation, round(sa.duration, 3), fps) + "-" + json.dumps(reveals, sort_keys=True)[:0]
        key = spec_hash({"s": scene, "r": reveals, "a": mastered.stat().st_size}, theme, orientation, round(sa.duration, 3), fps)
        seg = work / f"seg-{key}.mp4"
        if seg.exists():
            cached += 1
        else:
            renderer.render_video(scene, theme, seg, orientation, sa.duration, fps, audio=mastered, reveals=reveals)
        segs.append(seg)
        for tm in sa.timings:
            cues += split_cues(tm.text, t0 + tm.start, t0 + tm.end)
        chapters.append((t0, t0 + sa.duration, _title(scene)))
        t0 += sa.duration

    lst = work / "list.txt"
    lst.write_text("".join(f"file '{s.resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
    joined = work / "joined.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)], check=True)

    srt, vtt = out_dir / f"{name}.srt", out_dir / f"{name}.vtt"
    srt.write_text(to_srt(cues), encoding="utf-8")
    vtt.write_text(to_vtt(cues), encoding="utf-8")
    if music:
        joined = add_music_bed(joined, Path(music), work / "joined.music.mp4", t0, work)
    meta = work / "chapters.txt"
    meta.write_text(ffmetadata(chapters), encoding="utf-8")
    mp4 = out_dir / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-i", str(srt), "-i", str(meta),
                    "-map", "0:v", "-map", "0:a", "-map", "1:0", "-map_metadata", "2", "-map_chapters", "2",
                    "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", f"language={sub_lang}",
                    "-movflags", "+faststart", str(mp4)], check=True)
    lufs = float(measure_loudness(mp4)["input_i"])
    return ExportResult(mp4, srt, vtt, t0, chapters, lufs, cached, notes)
