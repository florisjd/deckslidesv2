"""Narration as beats: one sentence each, optionally anchored to an on-screen element.
Rule-based writer is the deterministic fallback; LLM writer feeds the same validators."""
from __future__ import annotations
import re
from pydantic import BaseModel, Field

from ..scenes import Base
from ..storyboard.build import C, sentence_core

WPM = 150
# Layouts whose list items reveal one by one; value = number of .rv elements before the first item.
ITEM_OFFSET = {"cards": 1, "process": 1, "checklist": 1, "takeaways": 1, "escalation": 1}


class Beat(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    reveal: int | None = None     # index of the .rv element this beat reveals
    pause_after_ms: int = 350


class BeatList(BaseModel):
    beats: list[Beat] = Field(min_length=1, max_length=8)


def est_seconds(text: str) -> float:
    return len(text.split()) / WPM * 60


def _items(scene: Base) -> list[str]:
    d = scene.model_dump()
    for key in ("items", "points"):
        if key in d:
            return d[key]
    for key in ("cards", "steps", "levels"):
        if key in d:
            return [": ".join(p for p in (x.get("title"), x.get("text")) if p) for x in d[key]]
    return []


PAGE_SUFFIX = re.compile(r"\s*\(\d+/\d+\)\s*$")


def speakable(title: str) -> str:
    """'The Code (1/2)' is layout; the voice says 'The Code'."""
    return PAGE_SUFFIX.sub("", title or "").strip()


def drop_shared_lead(texts: list[str], min_words: int = 4) -> list[str]:
    """Items that share an opening ('When driving a Company vehicle, team members must ...') say it once."""
    if len(texts) < 3:
        return texts
    words = [t.split() for t in texts]
    n = 0
    while all(len(w) > n + 1 for w in words) and len({w[n].lower() for w in words}) == 1:
        n += 1
    if n < min_words:
        return texts
    out = [texts[0]]
    for w in words[1:]:
        rest = " ".join(w[n:])
        out.append(rest[:1].upper() + rest[1:])
    return out


def lead_texts(cited: list[C], n: int, negate: bool = False) -> list[str] | None:
    """First item as the full sentence ('..., team members must wear a seatbelt'), the rest as bare items."""
    if len(cited) != n or n < 2 or not cited[0].lead:
        return None
    rest = []
    for c in cited[1:]:
        it = (c.item or sentence_core(c.text)).rstrip(".")
        if negate:
            it = "Do not " + re.sub(r"^(?:do not|not)\s+", "", it, flags=re.I)[:1].lower() + re.sub(r"^(?:do not|not)\s+", "", it, flags=re.I)[1:]
        rest.append(it)
    return [sentence_core(cited[0].text), *rest]


def notes_to_beats(notes: str, fallback: list[str] | None = None) -> list[Beat]:
    """Turn slide speaker notes into bounded narration beats (one sentence per beat, <=300 chars)."""
    chunks: list[str] = []
    if notes and notes.strip():
        for raw in re.split(r"(?<=[.!?])\s+|\n+", notes.strip()):
            s = re.sub(r"^\s*(?:[-•●▪◦*]|\d+[.)])\s*", "", raw).strip()
            if s:
                chunks.append(s)
    if not chunks:
        chunks = [s.strip() for s in (fallback or []) if s and s.strip()] or ["Here is the slide."]

    bounded: list[str] = []
    for s in chunks:
        while len(s) > 260:
            cut = s.rfind(",", 120, 260)
            if cut < 0:
                cut = s.rfind(" ", 120, 260)
            if cut < 0:
                cut = 260
            bounded.append(s[:cut].rstrip(" ,;:-") + ".")
            s = s[cut:].lstrip(" ,;:-")
        if s:
            bounded.append(s)
    return [Beat(text=t, reveal=0 if i == 0 else None) for i, t in enumerate(bounded)]


def write_script_rule(scene: Base, claims: dict[int, C]) -> list[Beat]:
    k = scene.kind
    d = scene.model_dump()
    cited = [claims[c] for c in scene.claim_ids if c in claims]
    say = lambda c: sentence_core(c.text) + "."
    if k == "cover":
        b = [Beat(text=f"{speakable(d['title'])}.")]
        if d["subtitle"] and len(d["subtitle"]) <= 90:
            b.append(Beat(text=d["subtitle"].rstrip(".") + "."))
        return b
    if k == "stat":
        return [Beat(text=f"{d['label']}: {d['value']}."), *([Beat(text=say(cited[0]))] if cited else [])]
    if k == "compare":
        n_do, n_dont = len(d["do"]), len(d["dont"])
        do_c = cited[:n_do]
        dont_c = cited[n_do:n_do + n_dont]
        do_t = (lead_texts(do_c, n_do) if d.get("do_lead") else None) or [x.rstrip(".") for x in d["do"]]
        dont_t = (lead_texts(dont_c, n_dont, negate=True) if d.get("dont_lead") else None) or [x.rstrip(".") for x in d["dont"]]
        return ([Beat(text=f"{speakable(d['heading'])}. Here is what to do.")] + [Beat(text=x + ".") for x in do_t]
                + [Beat(text="And here is what not to do.")] + [Beat(text=x + ".") for x in dont_t])
    if k in ITEM_OFFSET:
        off = ITEM_OFFSET[k]
        out = [Beat(text=f"{speakable(d.get('heading', 'Key takeaways'))}.", reveal=0)]
        items = _items(scene)
        # narrate the full source sentence of each item (card titles are layout, e.g. 'When working...: You are...')
        full = [sentence_core(claims[i].text) for i in scene.claim_ids if i in claims]
        texts = (lead_texts(cited, len(items)) if d.get("lead") else None) or drop_shared_lead(full if len(full) == len(items) else items)
        out += [Beat(text=t.rstrip(".") + ".", reveal=off + i) for i, t in enumerate(texts)]
        return out
    if k == "scenario":
        opts = " ".join(f"Option {'ABC'[i]}: {o.rstrip('.')}." for i, o in enumerate(d["options"]))
        return [Beat(text=d["situation"]), Beat(text=opts), Beat(text=f"The answer is option {'ABC'[d['answer_index']]}. {d['why']}")]
    if k == "myth_fact":
        return [Beat(text=f"Myth: {d['myth']}"), Beat(text=f"Fact: {d['fact']}")]
    if k == "spotlight":
        return [Beat(text=d["term"] + "."), Beat(text=d["definition"])]
    if k == "image":
        fb = [sentence_core(c.text) + "." for c in cited] or ([d["caption"]] if d.get("caption") else None)
        return notes_to_beats(d.get("notes", ""), fb)
    return [Beat(text=d.get("headline", "") + "."), *([Beat(text=d["body"])] if d.get("body") else [])]


def total_seconds(beats: list[Beat]) -> float:
    return sum(est_seconds(b.text) + b.pause_after_ms / 1000 for b in beats)


def length_off(beats: list[Beat], target_s: float, tol: float = 0.5) -> str | None:
    t = total_seconds(beats)
    if t > target_s * (1 + tol):
        return "long"
    return None


def write_script_llm(llm, scene: Base, claims: dict[int, C], style_guide: str = "", locale: str = "AU") -> list[Beat]:
    """style_guide may include the house style plus retrieved few-shot examples (memory.render_prompt_block)."""
    cited = "\n".join(f"- {claims[c].text}" for c in scene.claim_ids if c in claims)
    shown = PAGE_SUFFIX.sub("", scene.model_dump_json(exclude={"claim_ids", "notes"})) if False else re.sub(r"\s*\(\d+/\d+\)", "", scene.model_dump_json(exclude={"claim_ids", "notes"}))
    notes_blk = f"Speaker notes:\n{scene.notes}\n\n" if scene.kind == "image" and scene.notes else ""
    msgs = [
        {"role": "system", "content": (
            f"You write short narration for a workplace training video. Use {locale} English spelling. "
            "Use ONLY facts in the source claims; never add rules, numbers, names or change must/should strength. "
            f"One sentence per beat, 2-6 beats, friendly and direct. {style_guide}")},
        {"role": "user", "content": f"Slide ({scene.kind}):\n{shown}\n\n{notes_blk}Source claims:\n{cited}\n\nWrite the narration beats."},
    ]
    return llm.generate(msgs, BeatList).beats


def anchor_reveals(scene: Base, beats: list[Beat]) -> list[Beat]:
    """LLM beats carry no anchors: map the Nth item beat onto the Nth item element."""
    off = ITEM_OFFSET.get(scene.kind)
    if off is None or any(b.reveal is not None for b in beats):
        return beats
    items = _items(scene)
    for i, b in enumerate(beats[:len(items)]):      # beat i narrates item i; the heading appears immediately
        b.reveal = off + i
    return beats
