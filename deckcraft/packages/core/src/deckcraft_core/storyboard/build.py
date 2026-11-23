"""Storyboard: choose layout sequence from claim kinds, build deterministic fallback scenes,
validate scenes against their claims. (LLM fill_scene plugs in later via the same validators.)"""
from __future__ import annotations
import re
from dataclasses import dataclass

from ..understand.claims import VERB_LEAD
from ..scenes import (Cards, Checklist, Compare, Cover, Scene, Scenario, Spotlight, Stat, Statement,
                      Takeaways, Card, parse_scene, Base)

LOCALE_SWAPS = {  # US -> AU/UK spelling lint (plan: "Prioritize" bug)
    "prioritize": "prioritise", "organize": "organise", "recognize": "recognise", "center": "centre",
    "behavior": "behaviour", "color": "colour", "license": "licence", "authorize": "authorise",
}


@dataclass
class C:  # claim view used by the storyboard
    id: int
    kind: str
    modality: str
    text: str
    lead: str = ""
    item: str = ""


@dataclass
class Finding:
    severity: str  # error|warn
    kind: str
    message: str


def clean_title(s: str) -> str:
    """'1 WELCOME TO THE TEAM' -> 'Welcome to the team'; '3.3.2 Respect' -> 'Respect'."""
    s = re.sub(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[A-Za-z][.)])\s+", "", s or "").strip()
    if s.isupper() and len(s) > 4:
        s = s.capitalize()
    return s


def clip(s: str, n: int) -> str:
    """Shorten to n chars at a clause boundary when possible (no ellipsis), else at a word with an ellipsis."""
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= n:
        return s
    cuts = [m.start() for m in re.finditer(r"[,;:]|\s[-\u2013\u2014]\s", s[:n]) if m.start() >= n * 0.5]
    if cuts:
        return s[:cuts[-1]].rstrip(",;:- ")
    return s[:n - 1].rsplit(" ", 1)[0].rstrip(",;:- ") + "\u2026"


def sentence_core(text: str) -> str:
    return re.sub(r"^\s*(?:[-\u2022*]|\d+[.)])\s*", "", text).rstrip(".")


def split_title_body(sentence: str) -> tuple[str, str]:
    """A short card title from a natural break; never repeat the sentence as its own title."""
    s = sentence_core(sentence)
    m = re.search(r":\s+", s[:60])
    if m:
        return s[:m.start()], s[m.end():]
    m = re.search(r"[,;]\s+|\s[\u2013\u2014-]\s", s[14:52])
    if m:
        a = 14 + m.start()
        body = s[14 + m.end():]
        return s[:a], body[:1].upper() + body[1:]
    if len(s) <= 48:
        return s, ""
    return "", s


def shared_lead(cs: list[C], allow_not: bool = False) -> str:
    """The lead-in that every item of a list shares ('' unless there are 2+ items with the same non-empty lead).
    Items that start with 'Not ...' read badly under a positive caption ('team members must: Not use a phone'),
    so such lists keep full sentences unless the caller handles negation itself (Don't columns)."""
    leads = {c.lead for c in cs}
    if len(cs) < 2 or len(leads) != 1 or not next(iter(leads)):
        return ""
    if not allow_not and any(NOT_PREFIX.match(c.item or "") for c in cs):
        return ""
    return next(iter(leads))


def caption(lead: str) -> str:
    return clip(lead.rstrip(": ") + ":", 170)


def item_text(c: C, n: int = 140) -> str:
    return clip((c.item or sentence_core(c.text)).rstrip("."), n)


NOT_PREFIX = re.compile(r"^(?:do not|not)\s+", re.I)


def _first(claims, pred):
    return next((c for c in claims if pred(c)), None)


def fallback_scene(kind: str, claims: list[C], title: str = "Module", eyebrow: str = "Training") -> Scene:
    title = clean_title(title)
    ids = [c.id for c in claims]
    cores = [sentence_core(c.text) for c in claims]
    if kind == "cover":
        fits = [(i, c) for i, c in enumerate(cores) if 20 <= len(c) <= 120]
        i0, sub = fits[0] if fits else (0, clip(cores[0], 120) if cores else "")
        return Cover(eyebrow=clip(eyebrow, 60), title=clip(title, 80), subtitle=sub,
                     claim_ids=[claims[i0].id] if claims else [])
    if kind == "stat":
        c = _first(claims, lambda c: re.search(r"\d", c.text)) or claims[0]
        m = re.search(r"\$?\d[\d,]*(?:\.\d+)?\s?(?:%|hours?|days?|weeks?|months?|minutes?|years?)?", c.text)
        return Stat(value=clip(m.group(0).strip(), 14) if m else "1", label=clip(title, 60),
                    body=clip(sentence_core(c.text), 220), claim_ids=[c.id])
    if kind == "compare":
        dont = [c for c in claims if c.kind == "prohibition"][:4]
        do = [c for c in claims if c.modality in ("must", "may", "should") and c.kind != "prohibition"][:4]
        do_lead, dont_lead = shared_lead(do), ""
        do_items = [item_text(c) for c in do] if do_lead else [clip(sentence_core(c.text), 140) for c in do]
        dont_items = [clip(sentence_core(c.text), 140) for c in dont]
        dl = shared_lead(dont, allow_not=True)
        if dl and VERB_LEAD.search(dl) and all(NOT_PREFIX.match(c.item or "") for c in dont):
            # "..., team members must" + "Not use a phone"  ->  caption "..., team members must not:" + item "Use a phone"
            dont_lead = caption(dl + " not")
            strip = lambda c: NOT_PREFIX.sub("", item_text(c, 400))
            dont_items = [clip(strip(c)[:1].upper() + strip(c)[1:], 140) for c in dont]
        return Compare(heading=clip(title, 60), do_lead=caption(do_lead) if do_lead else "", dont_lead=dont_lead,
                       do=do_items, dont=dont_items, claim_ids=[c.id for c in do + dont])
    if kind == "checklist":
        pick = [c for c in claims if c.modality == "must"][:6]
        lead = shared_lead(pick)
        return Checklist(heading=clip(title, 60), lead=caption(lead) if lead else "",
                         items=[item_text(c) if lead else clip(sentence_core(c.text), 140) for c in pick],
                         claim_ids=[c.id for c in pick])
    if kind == "cards":
        pick = claims[:4]
        lead = shared_lead(pick)
        parts = [split_title_body(c.item if lead else c.text) for c in pick]
        titles = [tt for tt, _ in parts]
        if len(set(titles)) < len(titles):          # repeated titles: show the sentence alone
            parts = [("", (c.item or sentence_core(c.text)).rstrip(".") if lead else sentence_core(c.text)) for c in pick]
        cards = [Card(title=clip(tt, 60), text=clip(bb, 160)) for tt, bb in parts]
        return Cards(heading=clip(title, 60), lead=caption(lead) if lead else "", cards=cards, claim_ids=[c.id for c in pick])
    if kind == "spotlight":
        c = _first(claims, lambda c: c.kind == "contact") or claims[0]
        return Spotlight(term=clip(title, 60), definition=clip(sentence_core(c.text), 220), claim_ids=[c.id])
    if kind == "takeaways":
        top = (claims[:4] if len(claims) >= 2 else claims * 2)[:4]
        lead = shared_lead(top)
        return Takeaways(lead=caption(lead) if lead else "",
                         points=[item_text(c) if lead else clip(sentence_core(c.text), 140) for c in top],
                         claim_ids=[c.id for c in top])
    c = claims[0]
    return Statement(headline=clip(sentence_core(c.text), 140), claim_ids=[c.id])


def fit_content(scene: Scene) -> Scene:
    """Clip over-long strings to schema capacity before validation (rule-based, no model)."""
    return scene


def validate_scene(scene: Base, claims: dict[int, C], locale: str = "AU") -> list[Finding]:
    out: list[Finding] = []
    if not scene.claim_ids and scene.kind not in ("image",):
        out.append(Finding("error", "provenance", "Scene cites no claim"))
    for cid in scene.claim_ids:
        if cid not in claims:
            out.append(Finding("error", "provenance", f"Unknown claim {cid}"))
    blob = scene.model_dump_json().lower()
    if locale in ("AU", "UK"):
        for us, uk in LOCALE_SWAPS.items():
            if re.search(rf"\b{us}\w*", blob):
                out.append(Finding("warn", "locale", f"US spelling '{us}' (use '{uk}')"))
    if scene.kind == "stat":
        src = " ".join(claims[c].text for c in scene.claim_ids if c in claims)
        digits = re.sub(r"\D", "", scene.value)
        if digits and digits not in re.sub(r"\D", "", src):
            out.append(Finding("error", "number", f"Stat value {scene.value!r} not in cited claims"))
    return out


def build_module_scenes(title: str, claims: list[C], eyebrow: str = "Training", max_scenes: int = 9) -> list[Scene]:
    """Every claim lands on a slide when capacity allows: special layouts first, then the remaining
    claims are spread over as many cards slides as needed (4 per slide)."""
    title = clean_title(title)
    if not claims:
        return []
    used: set[int] = set()
    body: list[Scene] = []

    def add(kind: str, subset: list[C]):
        sc = fallback_scene(kind, subset, title, eyebrow)
        sc.claim_ids = list(dict.fromkeys(sc.claim_ids))
        used.update(sc.claim_ids)
        body.append(sc)

    prohib = [c for c in claims if c.kind == "prohibition"]
    dos = [c for c in claims if c.modality in ("must", "may", "should") and c.kind != "prohibition"]
    if len(prohib) >= 2 and len(dos) >= 2:      # compare needs 2+ distinct items per side; never pad by repeating
        add("compare", prohib + dos)
    numeric = [c for c in claims if c.kind in ("threshold", "deadline") and re.search(r"\d", c.text)]
    if numeric:
        add("stat", numeric[:1])
    musts = [c for c in claims if c.modality == "must" and c.id not in used]
    if len(musts) >= 3:
        add("checklist", musts)
    contact = [c for c in claims if c.kind == "contact" and c.id not in used]
    if contact:
        add("spotlight", contact)
    rest = [c for c in claims if c.id not in used]
    chunks = [rest[i:i + 4] for i in range(0, len(rest), 4)]
    n = len(chunks)
    for i, ch in enumerate(chunks):
        if len(body) >= max_scenes - 2:
            break
        if len(ch) == 1:
            add("statement", ch)
        else:
            add("cards", ch)
            if n > 1:
                body[-1].heading = clip(f"{title} ({i + 1}/{n})", 60)
    scenes: list[Scene] = [fallback_scene("cover", claims, title, eyebrow)] + body
    if len(body) >= 2:                            # a recap only when there is something to recap
        firsts = [c for s in body for c in claims if s.claim_ids and c.id == s.claim_ids[0]]
        pool = firsts + [c for c in claims if c not in firsts]
        short = [c for c in pool if len(sentence_core(c.text)) <= 140]
        recap = sorted(short[:4] if len(short) >= 2 else pool[:4], key=lambda c: claims.index(c))
        if len(recap) >= 2:
            scenes.append(fallback_scene("takeaways", recap, title, eyebrow))
    if len(scenes) == 1:
        scenes.append(fallback_scene("statement", claims, title, eyebrow))
    return scenes


def choose_sequence(claims: list[C]) -> list[str]:
    return [s.kind for s in build_module_scenes("Module", claims)]
