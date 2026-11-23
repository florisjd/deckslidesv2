"""Music bed with sidechain ducking under narration, then re-mastered to the loudness target."""
from __future__ import annotations
import subprocess
from pathlib import Path

from ..speech.tts import master_audio


def mix_music(narration_wav: Path, music: Path, out_wav: Path, duration: float,
              music_gain_db: float = -20.0, fade_out_s: float = 2.0) -> None:
    """Narration drives a sidechain compressor on the music, so the bed dips while someone is talking."""
    fade = max(0.0, duration - fade_out_s)
    fc = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={music_gain_db}dB,"
          f"afade=t=in:d=1,afade=t=out:st={fade:.2f}:d={fade_out_s}[m];"
          "[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[nar][sc];"
          "[m][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=500:makeup=1[duck];"
          "[nar][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.9[a]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(narration_wav), "-stream_loop", "-1", "-i", str(music),
                    "-filter_complex", fc, "-map", "[a]", "-t", f"{duration:.3f}", "-c:a", "pcm_s16le", str(out_wav)], check=True)


def add_music_bed(video: Path, music: Path, out: Path, duration: float, work: Path) -> Path:
    nar, mixed, final = work / "nar.wav", work / "mixed.wav", work / "mixed.mastered.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(nar)], check=True)
    mix_music(nar, music, mixed, duration)
    master_audio(mixed, final)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(final), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    return out
