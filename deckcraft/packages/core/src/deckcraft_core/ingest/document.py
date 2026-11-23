"""Structured source document + ingest.
PDFs use pypdfium2 with typography-based headings and repetition-based furniture removal;
DOCX uses python-docx preserving document/table order; PPTX delegates to pptx_deck."""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

BULLET = re.compile(r"^\s*(?:[-•●▪◦*]|\d+[.)]|[ivxIVX]+\.|[a-z][.)])\s+")
PAGE_NO = re.compile(r"\s*(page\s*)?\d+(\s*of\s*\d+)?\s*", re.I)
TOC_DOTS = re.compile(r"\.{4,}\s*\d+\s*$")


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
    image_bytes: bytes | None = None
    notes: str = ""


@dataclass
class SourceDocument:
    sections: list[SectionDoc] = field(default_factory=list)
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)
    source_kind: str = "text"


def validate_zip(path: Path, max_entries: int = 2000, max_uncompressed: int = 200 * 1024 * 1024,
                 max_ratio: float = 100.0) -> list[str]:
    """Reject corrupt archives, path-traversal entries, and zip bombs before reading Office XML."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ValueError("Invalid ZIP archive") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise ValueError(f"ZIP exceeds max entries ({len(infos)} > {max_entries})")
        total = 0
        for info in infos:
            raw = info.filename.replace("\\", "/")
            segs = raw.split("/")
            if raw.startswith("/") or ".." in segs or (segs and ":" in segs[0]):
                raise ValueError(f"Unsafe path in ZIP archive: {info.filename!r}")
            total += info.file_size
            if total > max_uncompressed:
                raise ValueError(f"ZIP uncompressed size exceeds limit ({total} > {max_uncompressed})")
            if info.file_size > 1_000_000 and info.file_size / max(info.compress_size, 1) > max_ratio:
                raise ValueError(f"ZIP compression ratio exceeds limit for {info.filename!r}")
        return [info.filename for info in infos]


def detect_type(path: Path) -> str:
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        names = set(validate_zip(path))
        if "word/document.xml" in names:
            return "docx"
        if "ppt/presentation.xml" in names:
            return "pptx"
        return "zip"
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


def table_rows_to_lines(rows: list[list[str]]) -> list[str]:
    """Turn table rows into natural policy sentences so claim extraction and TTS read them cleanly."""
    cleaned = [[re.sub(r"\s+", " ", c or "").strip() for c in r] for r in rows]
    cleaned = [r for r in cleaned if any(r)]
    if not cleaned:
        return []
    headers = cleaned[0]
    has_headers = len(cleaned) >= 2 and len(headers) >= 2 and all(headers) and len(set(headers)) == len(headers)
    if not has_headers:
        return [" — ".join(c.rstrip(".") for c in r if c) + "." for r in cleaned]
    lines: list[str] = []
    for r in cleaned[1:]:
        if len(headers) == 2 and len(r) >= 2 and r[0] and r[1]:
            lines.append(f"{r[0].rstrip(':')}: {r[1].rstrip('.')}.")
        elif len(headers) >= 3 and r[0]:
            pairs = [(h, v) for h, v in zip(headers[1:], r[1:]) if v]
            if pairs:
                lines.append(f"{r[0].rstrip(':')}: " + "; ".join(f"{h}: {v.rstrip('.')}" for h, v in pairs) + ".")
            else:
                lines.append(f"{headers[0]}: {r[0].rstrip('.')}.")
        else:
            pairs = [(h, v) for h, v in zip(headers, r) if v]
            if pairs:
                lines.append("; ".join(f"{h}: {v.rstrip('.')}" for h, v in pairs) + ".")
    return lines


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
    try:
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
    finally:
        pdf.close()


def ingest_docx(path: Path) -> SourceDocument:
    """Ingest a Word .docx preserving headings, lists, and tables in document order."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    sections: list[SectionDoc] = []
    stack: list[tuple[int, str]] = []
    cur: SectionDoc | None = None
    max_heading = 1
    fallback_title = clean_heading(path.stem) or path.stem

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            text = re.sub(r"\s+", " ", p.text or "").strip()
            if not text:
                continue
            style_name = (p.style.name if p.style else "") or ""
            if style_name.lower().startswith("toc") or TOC_DOTS.search(text):
                continue

            lv = 0
            hm = re.match(r"heading\s+(\d+)", style_name, re.I)
            if style_name.lower() == "title":
                lv = 1
            elif hm:
                lv = int(hm.group(1))
                max_heading = max(max_heading, lv)
            else:
                pPr = child.find(qn("w:pPr"))
                has_num = pPr is not None and pPr.find(qn("w:numPr")) is not None
                non_empty_runs = [r for r in p.runs if r.text and r.text.strip()]
                all_bold = bool(non_empty_runs) and all(r.bold is True for r in non_empty_runs)
                if (not has_num and not BULLET.match(text) and all_bold and len(text) < 80
                        and len(text.split()) <= 9 and not text.endswith((".", ":", ",")) and "," not in text):
                    lv = max_heading + 1

            if lv:
                title = clean_heading(text)
                if title:
                    while stack and stack[-1][0] >= lv:
                        stack.pop()
                    stack.append((lv, title))
                    cur = SectionDoc(" > ".join(t for _, t in stack), lv, "", 1, 1)
                    sections.append(cur)
                continue

            if cur is None:
                cur = SectionDoc(fallback_title, 1, "", 1, 1)
                sections.append(cur)
            bm = BULLET.match(text)
            line = text[bm.end():].strip() if bm else text
            if line:
                cur.text += ("\n" if cur.text else "") + line

        elif child.tag == qn("w:tbl"):
            tbl = Table(child, doc)
            rows: list[list[str]] = []
            for tr in tbl.rows:
                row_cells, seen_tc = [], set()
                for cell in tr.cells:
                    if id(cell._tc) in seen_tc:
                        continue
                    seen_tc.add(id(cell._tc))
                    row_cells.append(re.sub(r"\s+", " ", cell.text or "").strip())
                rows.append(row_cells)
            lines = table_rows_to_lines(rows)
            if lines:
                if cur is None:
                    cur = SectionDoc(fallback_title, 1, "", 1, 1)
                    sections.append(cur)
                cur.text += ("\n" if cur.text else "") + "\n".join(lines)

    non_empty = [s for s in sections if s.text.strip()]
    page_count = max(1, len(non_empty))
    try:
        with zipfile.ZipFile(path) as zf:
            if "docProps/app.xml" in zf.namelist():
                root = ET.fromstring(zf.read("docProps/app.xml"))
                for el in root.iter():
                    if el.tag.endswith("Pages") and el.text and el.text.strip().isdigit():
                        val = int(el.text.strip())
                        if val > 0:
                            page_count = val
                        break
    except Exception:  # noqa: BLE001
        pass

    split_secs: list[SectionDoc] = []
    for s in non_empty:
        split_secs.extend(split_oversized(s))
    return SourceDocument(sections=split_secs, page_count=page_count, source_kind="docx")


def ingest(path: Path, *, pptx_rasteriser: str = "auto") -> SourceDocument:
    kind = detect_type(path)
    doc = SourceDocument(source_kind=kind)
    if kind == "pdf":
        pages = _pdf_blocks(path)
        doc.page_count = len(pages)
        if not any(pages):
            doc.warnings.append("No selectable text found (scanned PDF); OCR not yet supported.")
            return doc
        secs = build_outline(strip_furniture(pages), path.stem)
    elif kind == "docx":
        return ingest_docx(path)
    elif kind == "pptx":
        from .pptx_deck import ingest_pptx
        return ingest_pptx(path, rasteriser=pptx_rasteriser)
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
        doc.warnings.append("Unsupported ZIP archive (expected .docx or .pptx).")
        return doc
    for s in secs:
        doc.sections.extend(split_oversized(s))
    return doc
