"""Structured source document + ingest. PDFs use pypdfium2 with typography-based
heading detection and repetition-based furniture removal (no customer-specific regex)."""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

BULLET = re.compile(r"^\s*(?:[-•●▪◦*]|\d+[.)]|[ivxIVX]+\.|[a-z][.)])\s+")
PAGE_NO = re.compile(r"\s*(page\s*)?\d+(\s*of\s*\d+)?\s*", re.I)


@dataclass
class Block:
    text: str
    page: int
    size: float = 0.0
    bold: bool = False


@dataclass
class SectionDoc:
    heading_path: str
    level: int
    text: str
    page_start: int
    page_end: int


@dataclass
class SourceDocument:
    sections: list[SectionDoc] = field(default_factory=list)
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)


def detect_type(path: Path) -> str:
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        return "zip"  # docx/pptx: Phase 1b
    return "text"


def strip_furniture(pages: list[list[Block]]) -> list[list[Block]]:
    """Drop lines repeating on >=40% of pages (headers/footers) and bare page numbers."""
    def norm(t: str) -> str:
        return re.sub(r"\d+", "#", t.strip().lower())
    counts: Counter = Counter()
    for p in pages:
        for k in {norm(b.text) for b in p}:
            counts[k] += 1
    limit = max(2, int(len(pages) * 0.4)) if len(pages) >= 3 else 10**9
    return [[b for b in p if counts[norm(b.text)] < limit and not PAGE_NO.fullmatch(b.text)] for p in pages]


EMOJI = re.compile("[\u2600-\u27bf\u2b50\U0001f300-\U0001faff\ufe0f]")


def clean_heading(s: str) -> str:
    return re.sub(r"\s+", " ", EMOJI.sub("", s)).strip()


def build_outline(pages: list[list[Block]], fallback_title: str = "Document") -> list[SectionDoc]:
    sizes = Counter(round(b.size) for p in pages for b in p if b.size)
    body = sizes.most_common(1)[0][0] if sizes else 0
    big = sorted({round(b.size) for p in pages for b in p if b.size and round(b.size) > body + 1}, reverse=True)

    def level_of(b: Block, prev_open: bool) -> int:
        if len(b.text) > 120:
            return 0
        if big and round(b.size) in big:
            return big.index(round(b.size)) + 1
        # bold-only heading: never a bullet, never the continuation of an unfinished sentence
        words = len(b.text.split())
        if (b.bold and len(b.text) < 80 and words <= 9 and not b.text.endswith((".", ":", ",")) and "," not in b.text
                and not BULLET.match(b.text) and not prev_open):
            return len(big) + 1
        return 0

    sections: list[SectionDoc] = []
    stack: list[tuple[int, str]] = []
    cur = None
    in_list = False
    for pi, p in enumerate(pages, 1):
        for b in p:
            last = cur.text.rsplit("\n", 1)[-1] if cur and cur.text else ""
            prev_open = bool(last) and not re.search(r"[.!?:]$", last)
            lv = level_of(b, prev_open)
            if lv:
                in_list = False
                title = clean_heading(b.text)
                if cur is not None and not cur.text and stack and stack[-1][0] == lv and cur.page_start == pi:
                    stack[-1] = (lv, stack[-1][1] + " " + title)                  # wrapped / stacked heading lines
                    cur.heading_path = " > ".join(t for _, t in stack)
                    continue
                while stack and stack[-1][0] >= lv:
                    stack.pop()
                stack.append((lv, title))
                cur = SectionDoc(" > ".join(t for _, t in stack), lv, "", pi, pi)
                sections.append(cur)
                continue
            if cur is None:
                cur = SectionDoc(fallback_title, 1, "", pi, pi)
                sections.append(cur)
            m = BULLET.match(b.text.strip())
            line = b.text.strip()[m.end():] if m else b.text.strip()           # drop the bullet glyph
            if m:
                in_list = False
            if cur.text and not m and in_list and len(line) < 60 and not line.endswith("."):
                cur.text += "\n" + line                                        # unbulleted list item
            elif cur.text and not m and prev_open:
                cur.text += " " + line                                          # reflow a wrapped line
            else:
                if cur.text and line and last.endswith(":"):
                    in_list = True
                cur.text += ("\n" if cur.text else "") + line
            cur.page_end = pi
    return [s for s in sections if s.text.strip()]


def split_oversized(sec: SectionDoc, max_chars: int = 3000) -> list[SectionDoc]:
    if len(sec.text) <= max_chars:
        return [sec]
    out, buf = [], ""
    for s in re.split(r"(?<=[.!?])\s+", sec.text):
        if buf and len(buf) + len(s) > max_chars:
            out.append(buf)
            buf = ""
        buf += (" " if buf else "") + s
    if buf:
        out.append(buf)
    return [SectionDoc(f"{sec.heading_path} (part {i})", sec.level, t, sec.page_start, sec.page_end)
            for i, t in enumerate(out, 1)]


def _pdf_blocks(path: Path) -> list[list[Block]]:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    pdf = pdfium.PdfDocument(str(path))
    pages: list[list[Block]] = []
    for page in pdf:
        tp = page.get_textpage()
        text = tp.get_text_range() if tp.count_chars() else ""
        blocks, pos = [], 0
        for line in text.splitlines():
            ln = line.strip()
            if not ln:
                continue
            idx = text.find(line, pos)
            size, bold = 0.0, False
            if idx >= 0:
                pos = idx + len(line)
                try:  # size/weight of the first letter (bullets use a different font)
                    k = next((i for i, ch in enumerate(line) if ch.isalnum()), 0)
                    size = round(raw.FPDFText_GetFontSize(tp, idx + k), 1)
                    bold = raw.FPDFText_GetFontWeight(tp, idx + k) >= 600
                except Exception:  # noqa: BLE001
                    pass
            blocks.append(Block(ln, len(pages) + 1, size, bold))
        pages.append(blocks)
    return pages


def ingest(path: Path) -> SourceDocument:
    kind = detect_type(path)
    doc = SourceDocument()
    if kind == "pdf":
        pages = _pdf_blocks(path)
        doc.page_count = len(pages)
        if not any(pages):
            doc.warnings.append("No selectable text found (scanned PDF); OCR not yet supported.")
            return doc
        secs = build_outline(strip_furniture(pages), path.stem)
    elif kind == "text":
        txt = path.read_text(encoding="utf-8", errors="replace")
        doc.page_count = 1
        secs = []
        for chunk in re.split(r"\n(?=#+ )", txt):
            m = re.match(r"(#+)\s+(.*)\n?", chunk)
            title, lvl, body = (m.group(2), len(m.group(1)), chunk[m.end():]) if m else (path.stem, 1, chunk)
            if body.strip():
                secs.append(SectionDoc(title, lvl, body.strip(), 1, 1))
    else:
        doc.warnings.append("DOCX/PPTX ingest not implemented yet (Phase 1b).")
        return doc
    for s in secs:
        doc.sections.extend(split_oversized(s))
    return doc
