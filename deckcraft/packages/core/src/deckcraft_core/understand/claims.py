"""Claim extraction. Every claim carries a verbatim quote + char span that must exist
in its section (hallucinated quotes are rejected). The rule-based extractor is the
model-free fallback; an LLM extractor feeds the same validate step via llm_drafts."""
from __future__ import annotations
import re
from dataclasses import dataclass

MUST_NOT = re.compile(r"\b(must not|will not|to not|shall not|may not|cannot|can not|must never|never|prohibited|not permitted|not allowed|forbidden)\b", re.I)
MUST = re.compile(r"\b(must|shall|required to|are required|is required|mandatory|need to|needs to|expected to|responsible for)\b", re.I)
SHOULD = re.compile(r"\b(should|encouraged to|recommended)\b", re.I)
MAY = re.compile(r"\b(may|can|is permitted|are permitted|allowed to|entitled to)\b", re.I)
NUMBER = re.compile(r"\d")
DEADLINE = re.compile(r"\bwithin\b|\bno later than\b|\bbefore\b|\bby (?:the )?\w+day\b", re.I)
CONTACT = re.compile(r"\b(contact|report to|notify|call|email|speak to)\b", re.I)
SENT = re.compile(r"(?<=[.!?])\s+|\n+")
MODAL_ANY = re.compile(MUST_NOT.pattern + "|" + MUST.pattern + "|" + SHOULD.pattern + "|" + MAY.pattern, re.I)
VERB_LEAD = re.compile(r"\b(?:must(?: not)?|should(?: not)?|may(?: not)?|will(?: not)?|shall(?: not)?|expected to|required to|need to|needs to|responsible for|to)$", re.I)


@dataclass
class ClaimDraft:
    kind: str
    modality: str
    text: str
    quote: str
    char_start: int
    char_end: int
    page: int = 1
    risk: float = 0.0
    lead: str = ""      # shared lead-in of a list ('..., team members must'); '' for standalone sentences
    item: str = ""      # the list item as written ('Wear a seatbelt at all times')


def classify(sentence: str) -> tuple[str, str] | None:
    if MUST_NOT.search(sentence):
        return "prohibition", "must_not"
    if MUST.search(sentence):
        return ("deadline" if DEADLINE.search(sentence) else "obligation"), "must"
    if SHOULD.search(sentence):
        return "obligation", "should"
    if CONTACT.search(sentence):
        return "contact", "info"
    if MAY.search(sentence):
        return "permission", "may"
    if NUMBER.search(sentence) and len(sentence) < 240:
        return "threshold", "info"
    if len(sentence) >= 40:               # descriptive content (welcome, values, background) is still teachable
        return "info", "info"
    return None


def risk_score(kind: str, modality: str, text: str) -> float:
    r = {"info": 0.1, "prohibition": 0.9, "deadline": 0.8, "obligation": 0.6, "threshold": 0.5, "contact": 0.4}.get(kind, 0.2)
    if modality in ("must", "must_not"):
        r += 0.1
    return min(r, 1.0)


STANDALONE_START = re.compile(r"^(?:If|When|Where|Before|After|Once|Unless)\b")
DESCRIPTIVE_LEAD = re.compile(r"\b(?:includes?|including|such as|for example)\s*$", re.I)
PRONOUN_LEAD = re.compile(r"^(?:This|These|It|They)\b.*\b(?:includes?|including)\s*$", re.I)


def _compose(lead: str, item: str) -> str:
    """'Team members must' + 'Wear a seatbelt at all times.' -> 'Team members must wear a seatbelt at all times.'"""
    item = item.strip()
    if item.endswith("?"):                      # "ask yourself:" + "Is it safe?"
        return f"{lead}: {item}"
    item = item.rstrip(".")
    first = item.split(" ", 1)[0]
    if VERB_LEAD.search(lead) and first[:1].isupper() and not first.isupper():
        item = item[:1].lower() + item[1:]
    out = f"{lead} {item}."
    return out[:1].upper() + out[1:]


def extract_rule_based(text: str, page: int = 1) -> list[ClaimDraft]:
    """Line-aware: a line ending in ':' is a lead-in; the following lines that carry no modal verb of their own
    are its list items and inherit its obligation. The quote stays verbatim (the item); the text is composed."""
    out: list[ClaimDraft] = []
    lead: str | None = None
    prev: str = ""                    # last standalone sentence line: the antecedent of "This includes:"
    pos = 0
    for raw in text.split("\n"):
        start, pos = pos, pos + len(raw) + 1
        line = raw.strip()
        if not line:
            lead = None
            continue
        a = start + (len(raw) - len(raw.lstrip()))
        if line.endswith(":"):
            lead = line[:-1].strip()
            continue
        standalone = bool(STANDALONE_START.match(line))      # "If you are still unsure, ..." is its own sentence
        if lead and not standalone and not MODAL_ANY.search(line) and len(line) <= 200:
            if PRONOUN_LEAD.match(lead) and prev:
                # "Team members are responsible for caring for the vehicle." + "This includes:" + "Locking it."
                item = line.strip().rstrip(".")
                item = item[:1].lower() + item[1:] if not item.split(" ", 1)[0].isupper() else item
                composed = f"{prev.rstrip('.')}, including {item}."
                c = classify(composed)
                lead_out = f"{prev.rstrip('.')}, including"
            else:
                composed = _compose(lead, line)
                lead_out = lead
                c = ("info", "info") if DESCRIPTIVE_LEAD.search(lead) else classify(composed)   # "...may include:" is a list, not a permission
            if c:
                out.append(ClaimDraft(c[0], c[1], composed, line, a, a + len(line), page, risk_score(c[0], c[1], composed),
                                      lead=lead_out, item=line.rstrip(".")))
            continue
        lead = None
        prev = line
        spans, last = [], 0
        for m in SENT.finditer(line):
            spans.append((last, m.start()))
            last = m.end()
        spans.append((last, len(line)))
        for x, y in spans:
            q = line[x:y].strip()
            if len(q) < 15:
                continue
            s0 = a + x + (len(line[x:y]) - len(line[x:y].lstrip()))
            c = classify(q)
            if c:
                out.append(ClaimDraft(c[0], c[1], q, q, s0, s0 + len(q), page, risk_score(c[0], c[1], q)))
    return out


def validate_quote(c: ClaimDraft, section_text: str) -> bool:
    """Quote must literally occur in the section; spans are corrected if they drifted."""
    if not c.quote.strip():
        return False
    if section_text[c.char_start:c.char_end] == c.quote:
        return True
    i = section_text.find(c.quote)
    if i < 0:
        return False
    c.char_start, c.char_end = i, i + len(c.quote)
    return True


def dedupe(claims: list[ClaimDraft]) -> list[ClaimDraft]:
    seen, out = set(), []
    for c in claims:
        k = re.sub(r"\W+", " ", c.text.lower()).strip()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def extract_claims(section_text: str, page: int = 1, llm_drafts: list[ClaimDraft] | None = None) -> list[ClaimDraft]:
    drafts = (llm_drafts or []) + extract_rule_based(section_text, page)
    return dedupe([c for c in drafts if validate_quote(c, section_text)])
