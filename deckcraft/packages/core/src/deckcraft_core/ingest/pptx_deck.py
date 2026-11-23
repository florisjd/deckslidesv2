"""PowerPoint (.pptx) ingest and tiered slide rasteriser (LibreOffice -> PowerPoint COM -> Pillow fallback)."""
from __future__ import annotations
import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pptx import Presentation
from pptx.enum.dml import MSO_FILL
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .document import BULLET, SectionDoc, SourceDocument, clean_heading, table_rows_to_lines


def _slide_dims(prs) -> tuple[int, int]:
    return (1920, 1080) if (prs.slide_width or 0) >= (prs.slide_height or 0) else (1080, 1920)


def _find_libreoffice() -> str | None:
    for cand in (
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if cand and Path(cand).is_file():
            return str(cand)
    return None


def _rasterise_libreoffice(path: Path, target_w: int, target_h: int) -> list[bytes] | None:
    exe = _find_libreoffice()
    if not exe:
        return None
    try:
        import pypdfium2 as pdfium
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", td, str(path)],
                               capture_output=True, text=True, timeout=90)
            if r.returncode != 0:
                return None
            pdfs = list(Path(td).glob("*.pdf"))
            if not pdfs:
                return None
            pdf = pdfium.PdfDocument(str(pdfs[0]))
            try:
                out: list[bytes] = []
                for page in pdf:
                    scale = max(target_w, target_h) / max(page.get_width(), page.get_height(), 1)
                    pil = page.render(scale=scale).to_pil()
                    buf = io.BytesIO()
                    pil.save(buf, format="PNG")
                    out.append(buf.getvalue())
                return out
            finally:
                pdf.close()
    except Exception:  # noqa: BLE001
        return None


def _find_powerpoint() -> str | None:
    if os.name != "nt":
        return None
    for cand in (
        shutil.which("powerpnt"),
        r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"C:\Program Files\Microsoft Office\Office16\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office16\POWERPNT.EXE",
        r"C:\Program Files\Microsoft Office\Office15\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office15\POWERPNT.EXE",
    ):
        if cand and Path(cand).is_file():
            return str(cand)
    return None


def _rasterise_powerpoint(path: Path, target_w: int, target_h: int) -> list[bytes] | None:
    if not _find_powerpoint():
        return None
    ps_script = (
        "$ppt = $null; $pres = $null\n"
        "try {\n"
        "  $ppt = New-Object -ComObject PowerPoint.Application\n"
        "  $pres = $ppt.Presentations.Open($env:DC_PPTX_IN, 1, 0, 0)\n"
        "  $pres.Export($env:DC_PPTX_OUT, 'PNG', [int]$env:DC_W, [int]$env:DC_H)\n"
        "} finally {\n"
        "  if ($pres) { try { $pres.Close() } catch {} }\n"
        "  if ($ppt)  { try { $ppt.Quit() } catch {} }\n"
        "}\n"
    )
    try:
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "DC_PPTX_IN": str(path.resolve()), "DC_PPTX_OUT": td,
                   "DC_W": str(target_w), "DC_H": str(target_h)}
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                               env=env, capture_output=True, text=True, timeout=90)
            if r.returncode != 0:
                return None
            pngs = [p for p in Path(td).iterdir() if p.suffix.lower() == ".png"]
            if not pngs:
                return None
            def slide_num(p: Path) -> int:
                m = re.search(r"(\d+)", p.stem)
                return int(m.group(1)) if m else 0
            pngs.sort(key=slide_num)
            return [p.read_bytes() for p in pngs]
    except Exception:  # noqa: BLE001
        return None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default(size=size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        cand = f"{cur} {w}"
        if draw.textlength(cand, font=font) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _iter_shapes(shapes):
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)
        else:
            yield shape


def _extract_slide_content(slide, index: int) -> tuple[str, list[str], str]:
    try:
        title_shape = slide.shapes.title
    except Exception:  # noqa: BLE001
        title_shape = None
    title_el = title_shape._element if title_shape is not None else None
    title = re.sub(r"\s+", " ", title_shape.text).strip() if title_shape and title_shape.has_text_frame else ""

    body_lines: list[str] = []
    for shape in _iter_shapes(slide.shapes):
        if title_el is not None and shape._element is title_el:
            continue
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                t = re.sub(r"\s+", " ", p.text or "").strip()
                if not t:
                    continue
                if not title and len(t) < 80 and not t.endswith((".", ":")):
                    title = t
                    continue
                bm = BULLET.match(t)
                line = t[bm.end():].strip() if bm else t
                if line:
                    body_lines.append(line)
        elif shape.has_table:
            rows: list[list[str]] = []
            for tr in shape.table.rows:
                row_cells, seen_tc = [], set()
                for cell in tr.cells:
                    if id(cell._tc) in seen_tc:
                        continue
                    seen_tc.add(id(cell._tc))
                    row_cells.append(re.sub(r"\s+", " ", cell.text or "").strip())
                rows.append(row_cells)
            body_lines.extend(table_rows_to_lines(rows))

    notes = ""
    if slide.has_notes_slide:
        tf = slide.notes_slide.notes_text_frame
        if tf:
            notes = "\n".join(ln.strip() for ln in tf.text.splitlines() if ln.strip())

    return title or f"Slide {index}", body_lines, notes


def _rasterise_fallback(prs) -> list[bytes]:
    w, h = _slide_dims(prs)
    sw = max(int(prs.slide_width or 1), 1)
    sh = max(int(prs.slide_height or 1), 1)
    margin_x, margin_y = int(w * 0.08), int(h * 0.08)
    max_text_w = w - 2 * margin_x
    title_font = _font(56 if w > h else 48)
    body_font = _font(32 if w > h else 30)
    out: list[bytes] = []

    for idx, slide in enumerate(prs.slides, 1):
        bg = (246, 244, 238)
        try:
            fill = slide.background.fill
            if fill.type == MSO_FILL.SOLID and fill.fore_color and fill.fore_color.rgb:
                rgb = fill.fore_color.rgb
                bg = (rgb[0], rgb[1], rgb[2])
        except Exception:  # noqa: BLE001
            pass
        lum = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255.0
        fg = (255, 255, 255) if lum < 0.35 else (20, 35, 28)
        muted = (214, 224, 219) if lum < 0.35 else (74, 90, 82)
        accent = (103, 212, 167) if lum < 0.35 else (15, 107, 79)

        img = Image.new("RGB", (w, h), bg)
        for shape in _iter_shapes(slide.shapes):
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    pic = Image.open(io.BytesIO(shape.image.blob))
                    px = max(0, int((shape.left or 0) / sw * w))
                    py = max(0, int((shape.top or 0) / sh * h))
                    pw = max(1, int((shape.width or sw) / sw * w))
                    ph = max(1, int((shape.height or sh) / sh * h))
                    fitted = ImageOps.contain(pic.convert("RGBA"), (pw, ph))
                    ox = px + (pw - fitted.width) // 2
                    oy = py + (ph - fitted.height) // 2
                    img.paste(fitted, (ox, oy), fitted)
                except Exception:  # noqa: BLE001
                    pass

        draw = ImageDraw.Draw(img)
        title, body_lines, _ = _extract_slide_content(slide, idx)
        has_real_text = bool(body_lines) or any(
            s.has_text_frame and s.text_frame.text.strip() for s in _iter_shapes(slide.shapes)
        )
        if has_real_text:
            y = margin_y
            for line in _wrap_text(draw, title, title_font, max_text_w):
                if y + 70 > h - margin_y:
                    break
                draw.text((margin_x, y), line, font=title_font, fill=fg)
                y += 66 if w > h else 58
            y += 12
            if y < h - margin_y:
                draw.rectangle([margin_x, y, margin_x + 120, y + 6], fill=accent)
                y += 32
            for item in body_lines:
                for line in _wrap_text(draw, item, body_font, max_text_w):
                    if y + 46 > h - margin_y:
                        break
                    draw.text((margin_x, y), line, font=body_font, fill=muted)
                    y += 44 if w > h else 42
                y += 14

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def rasterise_pptx(path: Path, prs, prefer: str = "auto") -> tuple[list[bytes], str, list[str]]:
    """Try LibreOffice, then PowerPoint COM on Windows, then pure-Python Pillow fallback."""
    w, h = _slide_dims(prs)
    n = len(prs.slides)
    order = {
        "auto": ("libreoffice", "powerpoint", "fallback"),
        "libreoffice": ("libreoffice", "fallback"),
        "powerpoint": ("powerpoint", "fallback"),
        "fallback": ("fallback",),
    }.get(prefer, ("libreoffice", "powerpoint", "fallback"))

    warnings: list[str] = []
    for backend in order:
        imgs: list[bytes] | None = None
        if backend == "libreoffice":
            imgs = _rasterise_libreoffice(path, w, h)
        elif backend == "powerpoint":
            imgs = _rasterise_powerpoint(path, w, h)
        else:
            imgs = _rasterise_fallback(prs)
            warnings.append("PPTX rasterised with built-in fallback renderer (install LibreOffice or PowerPoint for full visual fidelity).")
            return imgs, "fallback", warnings
        if imgs is not None and len(imgs) == n:
            return imgs, backend, warnings

    imgs = _rasterise_fallback(prs)
    warnings.append("PPTX rasterised with built-in fallback renderer (install LibreOffice or PowerPoint for full visual fidelity).")
    return imgs, "fallback", warnings


def ingest_pptx(path: Path, rasteriser: str = "auto") -> SourceDocument:
    """Ingest a PowerPoint .pptx into one SectionDoc per slide with rasterised PNG + speaker notes."""
    prs = Presentation(str(path))
    if not prs.slides:
        return SourceDocument(sections=[], page_count=0, warnings=["Presentation contains no slides."], source_kind="pptx")

    imgs, _, warnings = rasterise_pptx(path, prs, prefer=rasteriser)
    deck_title = clean_heading(path.stem) or path.stem
    sections: list[SectionDoc] = []

    for i, slide in enumerate(prs.slides, 1):
        title, body_lines, notes = _extract_slide_content(slide, i)
        parts = list(body_lines)
        if notes:
            parts.append(notes)
        text = "\n".join(parts).strip() or title or f"Slide {i}"
        slide_title = clean_heading(title) or f"Slide {i}"
        sections.append(SectionDoc(
            heading_path=f"{deck_title} > {slide_title}",
            level=2,
            text=text,
            page_start=i,
            page_end=i,
            image_bytes=imgs[i - 1],
            notes=notes,
        ))

    return SourceDocument(sections=sections, page_count=len(prs.slides), warnings=warnings, source_kind="pptx")
