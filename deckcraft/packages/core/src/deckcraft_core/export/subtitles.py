"""SRT / WebVTT / chapters from beat timings. Cue splitting balances two short lines.
Times inside a beat are distributed by character count (estimated; forced alignment is a later upgrade)."""
from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class Cue:
    start: float
    end: float
    text: str


def two_lines(text: str, width: int = 38) -> str:
    if len(text) <= width:
        return text
    words, best = text.split(), None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = abs(len(a) - len(b))
        if best is None or score < best[0]:
            best = (score, a, b)
    return f"{best[1]}\n{best[2]}"


def split_cues(text: str, start: float, end: float, max_chars: int = 76) -> list[Cue]:
    sents = [s for s in re.split(r"(?<=[.!?:])\s+", text.strip()) if s]
    chunks, buf = [], ""
    for s in sents:
        if buf and len(buf) + len(s) + 1 > max_chars:
            chunks.append(buf)
            buf = ""
        buf = (buf + " " + s).strip()
    if buf:
        chunks.append(buf)
    total = sum(len(c) for c in chunks) or 1
    cues, t = [], start
    for c in chunks:
        d = (end - start) * len(c) / total
        cues.append(Cue(t, t + d, two_lines(c)))
        t += d
    return cues


def _ts(t: float, sep: str) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def to_srt(cues: list[Cue]) -> str:
    return "".join(f"{i}\n{_ts(c.start, ',')} --> {_ts(c.end, ',')}\n{c.text}\n\n" for i, c in enumerate(cues, 1))


def to_vtt(cues: list[Cue]) -> str:
    return "WEBVTT\n\n" + "".join(f"{_ts(c.start, '.')} --> {_ts(c.end, '.')}\n{c.text}\n\n" for c in cues)


def ffmetadata(chapters: list[tuple[float, float, str]]) -> str:
    out = ";FFMETADATA1\n"
    for s, e, title in chapters:
        t = title.replace("=", "-").replace(";", ",").replace("#", "").replace("\\", "")
        out += f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(s * 1000)}\nEND={int(e * 1000)}\ntitle={t}\n"
    return out
