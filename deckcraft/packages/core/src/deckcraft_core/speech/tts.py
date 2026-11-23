"""TTS providers, content-addressed beat audio, scene audio assembly, loudness mastering."""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf

from ..script.beats import Beat
from ..script.spoken import to_spoken

SR = 24000


class TTSProvider(Protocol):
    name: str
    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]: ...


class KokoroProvider:
    name = "kokoro-v1.0"

    def __init__(self, model_dir: Path, lang: str = "en-us"):
        from kokoro_onnx import Kokoro
        self._k = Kokoro(str(model_dir / "kokoro-v1.0.onnx"), str(model_dir / "voices-v1.0.bin"))
        self.lang = lang

    def synthesize(self, text, voice="af_heart", speed=1.0):
        a, sr = self._k.create(text, voice=voice, speed=speed, lang=self.lang)
        return np.asarray(a, dtype=np.float32), sr


class SilentProvider:
    """Test double: silence proportional to word count (deterministic, no model)."""
    name = "silent"

    def synthesize(self, text, voice="x", speed=1.0):
        n = max(1, len(text.split()))
        return np.zeros(int(SR * 0.35 * n / speed), dtype=np.float32), SR


@dataclass
class BeatTiming:
    start: float
    end: float
    text: str
    reveal: int | None


@dataclass
class SceneAudio:
    path: Path
    duration: float
    timings: list[BeatTiming]


class SpeechService:
    def __init__(self, provider: TTSProvider, cache_dir: Path, voice: str = "af_heart", speed: float = 1.0,
                 lexicon: dict[str, str] | None = None):
        self.p, self.cache, self.voice, self.speed, self.lex = provider, Path(cache_dir), voice, speed, lexicon or {}
        self.cache.mkdir(parents=True, exist_ok=True)

    def beat_audio(self, text: str) -> np.ndarray:
        spoken = to_spoken(text, self.lex)
        key = hashlib.sha256(json.dumps([self.p.name, self.voice, self.speed, spoken]).encode()).hexdigest()[:24]
        f = self.cache / f"{key}.wav"
        if f.exists():
            a, _ = sf.read(f, dtype="float32")
            return a
        a, sr = self.p.synthesize(spoken, self.voice, self.speed)
        if sr != SR:
            a = np.interp(np.linspace(0, len(a), int(len(a) * SR / sr), endpoint=False), np.arange(len(a)), a).astype(np.float32)
        sf.write(f, a, SR)
        return a

    def scene_audio(self, beats: list[Beat], out: Path, lead: float = 0.5, tail: float = 0.7,
                    min_duration: float = 0.0) -> SceneAudio:
        parts, timings, t = [np.zeros(int(SR * lead), dtype=np.float32)], [], lead
        for b in beats:
            a = self.beat_audio(b.text)
            d = len(a) / SR
            timings.append(BeatTiming(t, t + d, b.text, b.reveal))
            pause = np.zeros(int(SR * b.pause_after_ms / 1000), dtype=np.float32)
            parts += [a, pause]
            t += d + b.pause_after_ms / 1000
        parts.append(np.zeros(int(SR * tail), dtype=np.float32))
        audio = np.concatenate(parts)
        if len(audio) / SR < min_duration:   # silent title/outro cards hold for a minimum time
            audio = np.concatenate([audio, np.zeros(int(SR * min_duration) - len(audio), dtype=np.float32)])
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out, audio, SR)
        return SceneAudio(out, len(audio) / SR, timings)


def measure_loudness(path: Path) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                        "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        raise RuntimeError("loudnorm measurement failed: " + r.stderr[-300:])
    return json.loads(m.group(0))


def master_audio(src: Path, dst: Path, target_i: float = -16.0, tp: float = -1.5) -> dict:
    """Two-pass loudnorm to -16 LUFS, 48 kHz, explicit stereo. Digital silence is passed through."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found")
    m = measure_loudness(src)
    if m["input_i"] in ("-inf", "inf"):
        af = "aresample=48000"
    else:
        af = (f"loudnorm=I={target_i}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
              f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample=48000")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", af, "-ac", "2", "-ar", "48000",
                    "-c:a", "pcm_s16le", str(dst)], check=True)
    return m
