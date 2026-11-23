"""Verifier (plan S6): cheap deterministic checks that tie narration to cited claims.
Findings block approval, not editing."""
from __future__ import annotations
import re
from dataclasses import dataclass

from ..script.beats import Beat
from ..storyboard.build import C

NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
WORD_NUM = {"two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
            "nine": "9", "ten": "10", "twelve": "12", "twenty": "20", "twenty four": "24", "thirty": "30"}
WEAK_FRAME = re.compile(r"\b(important|good idea|best to|worth|helpful|advised|please|ideal)\b", re.I)
SOFT = re.compile(r"\b(should|may|might|can|could|optional|ideally|try to|encouraged)\b", re.I)
STRONG = re.compile(r"\b(must|shall|required|need to|needs to|have to|always|mandatory|expected to|responsible for)\b", re.I)
NEG = re.compile(r"\b(not|never|no|don[’']?t|cannot|can[’']?t|must not|prohibited|forbidden|avoid)\b", re.I)
ENTITY = re.compile(r"(\b[\w.+-]+@[\w-]+\.[\w.]+\b|\b(?:\+?\d[\d ()-]{7,}\d)\b|https?://\S+)")
STOP = set("the a an and or to of in on at for with your you we our is are be it that this as by from if when must should may not".split())


@dataclass
class Finding:
    severity: str
    kind: str
    span: str
    message: str
    claim_ids: tuple[int, ...] = ()


def _nums(text: str) -> set[str]:
    text = re.sub(r"\(\d+/\d+\)", " ", text)          # '(1/2)' page suffixes are layout, not claims
    out = {re.sub(r"[^\d.]", "", m.group(0)).rstrip(".") for m in NUM.finditer(text)}
    low = text.lower()
    for w in sorted(WORD_NUM, key=len, reverse=True):  # longest first; consume so 'twenty four' != 20 + 4
        pat = r"\b" + re.escape(w) + r"\b"
        if re.search(pat, low):
            out.add(WORD_NUM[w])
            low = re.sub(pat, " ", low)
    return {n for n in out if n}


def _toks(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOP and len(w) > 2}


def _best_claim(beat_text: str, claims: list[C]) -> C | None:
    """The cited claim this sentence mostly restates (>=3 shared content words covering >=half the sentence)."""
    mine = _toks(beat_text)
    best, score = None, 0.0
    for c in claims:
        ov = len(mine & _toks(c.text))
        if mine and ov >= 3 and ov / len(mine) >= 0.5 and ov / len(mine) > score:
            best, score = c, ov / len(mine)
    return best


def check_beat(beat: Beat, claims: list[C], language: str = "en") -> list[Finding]:
    english = language.split("-")[0] == "en"
    out: list[Finding] = []
    src = " ".join(c.text for c in claims)
    ids = tuple(c.id for c in claims)
    # 1. numbers must come from cited claims (digits or number-words)
    for n in sorted(_nums(beat.text) - _nums(src)):
        out.append(Finding("error", "number", beat.text, f"'{n}' is not in the cited claims", ids))
    # 2. modality / negation: judged against the ONE claim this sentence restates (English-only lexicons)
    best = _best_claim(beat.text, claims) if english else None
    if best and best.modality in ("must", "must_not"):
        if SOFT.search(beat.text) and not STRONG.search(beat.text) and not NEG.search(beat.text):
            out.append(Finding("error", "modality", beat.text, "Obligation weakened to optional wording", (best.id,)))
        elif WEAK_FRAME.search(beat.text) and not STRONG.search(beat.text) and not NEG.search(beat.text):
            out.append(Finding("warn", "modality-soft", beat.text, "Obligation restated without 'must' or equivalent", (best.id,)))
        if best.modality == "must_not" and not NEG.search(beat.text):
            out.append(Finding("error", "negation", beat.text, "Prohibition stated without negation", (best.id,)))
    # 3b. narration that mostly says things the cited claims do not (the model embellishing). Warning only.
    if english and claims:
        mine = _toks(beat.text)
        if len(mine) >= 4 and len(mine & _toks(src)) / len(mine) < 0.4:
            out.append(Finding("warn", "unsupported", beat.text, "Little of this sentence is backed by the cited source text", ids))
    # 4. entities (emails, phones, URLs) must exist in the source
    for m in ENTITY.finditer(beat.text):
        if m.group(0) not in src:
            out.append(Finding("error", "entity", beat.text, f"'{m.group(0)}' not in source", ids))
    return out


def check_scene(beats: list[Beat], claims: list[C], language: str = "en") -> list[Finding]:
    return [f for b in beats for f in check_beat(b, claims, language)]


def check_number_spoken(scene: dict, beats: list[Beat]) -> list[Finding]:
    """A number shown on a stat slide should also be said (the LLM sometimes paraphrases it away)."""
    if scene.get("kind") != "stat":
        return []
    shown = _nums(scene.get("value", ""))
    said = set().union(*[_nums(b.text) for b in beats]) if beats else set()
    miss = shown - said
    return [Finding("warn", "number-omitted", scene.get("value", ""), f"Slide shows {', '.join(sorted(miss))} but the narration never says it")] if miss else []


def coverage_report(all_claims: list[C], scene_texts: list[str], excluded: set[int] = frozenset(),
                    include_info: bool = False) -> list[Finding]:
    """Every must/must-not claim must be touched by some beat or text, or be explicitly excluded."""
    blob = _toks(" ".join(scene_texts))
    out = []
    for c in all_claims:
        strong = c.modality in ("must", "must_not")
        if c.id in excluded or not (strong or include_info):
            continue
        t = _toks(c.text)
        if t and len(t & blob) / len(t) < 0.5:
            out.append(Finding("error" if strong else "warn", "coverage" if strong else "coverage-info", c.text,
                               "Obligation not covered by any slide or narration" if strong else "Statement not covered by any slide", (c.id,)))
    return out


def locale_lint(text: str, locale: str = "AU") -> list[Finding]:
    from ..storyboard.build import LOCALE_SWAPS
    if locale not in ("AU", "UK"):
        return []
    return [Finding("warn", "locale", text, f"US spelling '{us}' (use '{uk}')")
            for us, uk in LOCALE_SWAPS.items() if re.search(rf"\b{us}\w*", text, re.I)]
