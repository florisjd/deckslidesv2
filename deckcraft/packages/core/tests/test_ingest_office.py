import io
import json
import subprocess
import zipfile
from pathlib import Path

import docx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from deckcraft_core import review as rv
from deckcraft_core.api.app import create_app
from deckcraft_core.blobs import BlobStore
from deckcraft_core.ingest.document import detect_type, ingest, ingest_docx, validate_zip
from deckcraft_core.ingest.pptx_deck import ingest_pptx
from deckcraft_core.render.scene_render import SceneRenderer, load_theme
from deckcraft_core.settings import Settings
from deckcraft_core.speech.tts import SilentProvider
from deckcraft_core.understand.claims import extract_claims
from deckcraft_core.updates import regenerate_stale, revise_source


def _png_bytes(color: tuple[int, int, int] = (15, 107, 79), size: tuple[int, int] = (320, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_docx(path: Path, *, safety_deadline: str = "24 hours") -> Path:
    doc = docx.Document()
    doc.add_heading("1. Safety Rules", level=1)
    toc = doc.add_paragraph("1. Safety Rules .......... 2")
    try:
        toc.style = "TOC 1"
    except KeyError:
        pass
    doc.add_paragraph("2. Reporting .......... 3")

    sub = doc.add_paragraph()
    r = sub.add_run("Vehicle Rules")
    r.bold = True

    doc.add_paragraph("When driving a Company vehicle, team members must:")
    doc.add_paragraph("Wear a seatbelt at all times.", style="List Bullet")
    doc.add_paragraph("Not use a hand-held phone while driving.", style="List Bullet")

    tbl = doc.add_table(rows=3, cols=3)
    headers = ["Event", "Action", "Deadline"]
    rows = [
        ["Minor hazard", "Log in register", "End of shift"],
        ["Serious injury", "Notify supervisor", f"Within {safety_deadline}"],
    ]
    for c, h in enumerate(headers):
        tbl.cell(0, c).text = h
    for ri, row in enumerate(rows, 1):
        for ci, val in enumerate(row):
            tbl.cell(ri, ci).text = val

    merged = doc.add_table(rows=1, cols=3)
    merged.cell(0, 0).text = "Zero tolerance"
    merged.cell(0, 0).merge(merged.cell(0, 1))
    merged.cell(0, 2).text = "Immediate dismissal"

    doc.add_heading("Leave Policy", level=1)
    doc.add_paragraph("Annual leave should be requested at least 2 weeks in advance.")
    doc.add_paragraph("Staff may take a 30 minute lunch break each day.")
    doc.save(str(path))
    return path


def _make_pptx(path: Path, *, deadline: str = "24 hours", slide3_color: tuple[int, int, int] = (30, 80, 140)) -> Path:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Slide 1: title + bullets + speaker notes
    s1 = prs.slides.add_slide(prs.slide_layouts[1])
    s1.shapes.title.text = "Welcome aboard"
    tf = s1.placeholders[1].text_frame
    tf.text = "Work safely at all times."
    tf.add_paragraph().text = "Never skip pre-start checks."
    s1.notes_slide.notes_text_frame.text = "Welcome to site operations. Stay alert and never skip pre-start checks."

    # Slide 2: title + picture + table + speaker notes with number
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "Incident reporting"
    s2.shapes.add_picture(io.BytesIO(_png_bytes((15, 107, 79))), Inches(0.8), Inches(4.2), width=Inches(4.0))
    tshape = s2.shapes.add_table(2, 2, Inches(5.2), Inches(2.0), Inches(7.0), Inches(1.8))
    tshape.table.cell(0, 0).text = "Event"
    tshape.table.cell(0, 1).text = "Deadline"
    tshape.table.cell(1, 0).text = "Report injury"
    tshape.table.cell(1, 1).text = f"Within {deadline}"
    s2.notes_slide.notes_text_frame.text = f"Employees must report any injury within {deadline}. Call your supervisor immediately."

    # Slide 3: picture-only, no notes
    s3 = prs.slides.add_slide(prs.slide_layouts[6])
    s3.shapes.add_picture(io.BytesIO(_png_bytes(slide3_color)), Inches(1.5), Inches(1.0), width=Inches(10.0))

    prs.save(str(path))
    return path


def test_validate_zip_and_detect_type(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"PK\x03\x04corrupt")
    with pytest.raises(ValueError, match="Invalid ZIP"):
        detect_type(bad)

    trav = tmp_path / "trav.zip"
    with zipfile.ZipFile(trav, "w") as zf:
        zf.writestr("../../evil.txt", "nope")
    with pytest.raises(ValueError, match="Unsafe path"):
        validate_zip(trav)

    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\x00" * 1_500_000)
    with pytest.raises(ValueError, match="ratio"):
        validate_zip(bomb)

    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("hello.txt", "world")
    assert detect_type(plain) == "zip"
    doc = ingest(plain)
    assert doc.source_kind == "zip" and doc.warnings

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    md = tmp_path / "a.md"
    md.write_text("# Hi\nBody text.", encoding="utf-8")
    docx_p = _make_docx(tmp_path / "a.docx")
    pptx_p = _make_pptx(tmp_path / "a.pptx")
    assert detect_type(pdf) == "pdf"
    assert detect_type(md) == "text"
    assert detect_type(docx_p) == "docx"
    assert detect_type(pptx_p) == "pptx"


def test_docx_ingest_headings_lists_tables_and_updates(tmp_path):
    v1_path = _make_docx(tmp_path / "policy.docx", safety_deadline="24 hours")
    doc = ingest_docx(v1_path)
    assert doc.source_kind == "docx"
    paths = [s.heading_path for s in doc.sections]
    assert any("1. Safety Rules > Vehicle Rules" == p for p in paths)
    assert any("Leave Policy" == p for p in paths)
    veh = next(s for s in doc.sections if s.heading_path.endswith("Vehicle Rules"))
    assert ".......... 2" not in veh.text
    assert "Reporting" not in veh.text
    assert "Serious injury: Action: Notify supervisor; Deadline: Within 24 hours." in veh.text
    assert "Zero tolerance — Immediate dismissal." in veh.text
    assert veh.text.count("Zero tolerance") == 1

    claims = extract_claims(veh.text)
    mods = {(c.text, c.modality, c.lead) for c in claims}
    assert any("wear a seatbelt at all times" in t and m == "must" and "team members must" in ld for t, m, ld in mods)
    assert any("not use a hand-held phone" in t and m == "must_not" for t, m, _ in mods)

    app = create_app(Settings(root=tmp_path / "app"), run_worker=False)
    with TestClient(app, base_url="http://localhost") as c:
        pid = c.post("/api/v1/projects", json={"name": "Docx"}).json()["id"]
        up = c.post(f"/api/v1/projects/{pid}/sources",
                    files={"file": ("policy.docx", v1_path.read_bytes())}).json()
        app.state.queue.drain()
        assert c.get(f"/api/v1/jobs/{up['job_id']}").json()["state"] == "done"
        mods_list = c.get(f"/api/v1/projects/{pid}/modules").json()
        assert len(mods_list) >= 2
        for m in mods_list:
            c.post(f"/api/v1/modules/{m['id']}/storyboard")
        v2_path = _make_docx(tmp_path / "policy_v2.docx", safety_deadline="48 hours")
        rev = c.post(f"/api/v1/sources/{up['source_id']}/revisions?apply=true",
                     files={"file": ("policy.docx", v2_path.read_bytes())}).json()
        assert rev["summary"]["changed"] == 1 and rev["summary"]["unchanged"] == 1
        after = {m["title"]: m["status"] for m in c.get(f"/api/v1/projects/{pid}/modules").json()}
        assert after["Leave Policy"] != "stale"
        assert len(rev["stale_modules"]) == 1


def test_pptx_ingest_imagescene_notes_lint_and_export(tmp_path):
    pptx_path = _make_pptx(tmp_path / "induction.pptx", deadline="24 hours")
    doc = ingest_pptx(pptx_path, rasteriser="fallback")
    assert doc.source_kind == "pptx" and doc.page_count == 3 and len(doc.sections) == 3
    assert any("fallback renderer" in w for w in doc.warnings)
    for sec in doc.sections:
        assert sec.image_bytes is not None
        im = Image.open(io.BytesIO(sec.image_bytes))
        assert im.size == (1920, 1080)
    assert "Stay alert" in doc.sections[0].notes
    assert "24 hours" in doc.sections[1].text and "24 hours" in doc.sections[1].notes
    assert doc.sections[2].notes == ""

    st = Settings(root=tmp_path / "studio", pptx_rasteriser="fallback")
    app = create_app(st, run_worker=False)
    with TestClient(app, base_url="http://localhost") as c:
        pid = c.post("/api/v1/projects", json={"name": "Deck"}).json()["id"]
        up = c.post(f"/api/v1/projects/{pid}/sources",
                    files={"file": ("induction.pptx", pptx_path.read_bytes())}).json()
        app.state.queue.drain()
        assert c.get(f"/api/v1/jobs/{up['job_id']}").json()["state"] == "done"
        mods = c.get(f"/api/v1/projects/{pid}/modules").json()
        assert len(mods) == 1
        mid = mods[0]["id"]
        assert c.post(f"/api/v1/modules/{mid}/storyboard").json()["scenes"] == 3

        rep = c.get(f"/api/v1/modules/{mid}/scenes").json()
        scenes = rep["scenes"]
        assert all(s["data"]["kind"] == "image" and s["data"]["image_sha"] for s in scenes)
        assert "Stay alert" in " ".join(b["text"] for b in scenes[0]["beats"])
        assert "24 hours" in " ".join(b["text"] for b in scenes[1]["beats"])
        assert scenes[2]["beats"][0]["text"]

        poster = c.get(f"/api/v1/scenes/{scenes[0]['id']}/poster.png")
        assert poster.status_code == 200 and poster.content.startswith(b"\x89PNG\r\n\x1a\n")

        theme = load_theme("emerald-editorial")
        with SceneRenderer(blobs=BlobStore(st.blob_dir)) as r:
            for orient in ("portrait", "landscape"):
                for sc in scenes:
                    res = r.lint(sc["data"], theme, orient)
                    assert res.errors == [], (orient, sc["data"]["caption"], res.errors)

        # Corrupting a number in narration blocks approval
        s2_id = scenes[1]["id"]
        orig_beats = scenes[1]["beats"]
        c.put(f"/api/v1/scenes/{s2_id}/beats",
              json={"beats": [{"text": "Employees must report any injury within 48 hours.", "reveal": 0}]})
        bad_app = c.post(f"/api/v1/scenes/{s2_id}/approve")
        assert bad_app.status_code == 409 and bad_app.json()["detail"]["blocking"][0]["kind"] == "number"

        c.put(f"/api/v1/scenes/{s2_id}/beats", json={"beats": orig_beats})
        for sc in c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]:
            assert c.post(f"/api/v1/scenes/{sc['id']}/approve").status_code == 200

        out = rv.run_export(app.state.sf, st, mid, provider=SilentProvider())
        mp4 = Path(out["mp4"])
        assert mp4.exists()
        info = json.loads(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_chapters", "-of", "json", str(mp4)]
        ))
        kinds = [s["codec_type"] for s in info["streams"]]
        assert kinds.count("video") == 1 and kinds.count("audio") == 1 and "subtitle" in kinds
        assert len(info["chapters"]) == 3
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert v["color_space"] == "bt709" and v["pix_fmt"] == "yuv420p"
        assert mp4.with_suffix(".srt").read_text(encoding="utf-8").strip()
        assert mp4.with_suffix(".vtt").read_text(encoding="utf-8").strip()


def test_missing_image_lint_error():
    theme = load_theme("emerald-editorial")
    with SceneRenderer(blobs=None) as r:
        res = r.lint({"kind": "image", "image_sha": "0" * 64, "caption": "Missing"}, theme, "portrait")
        assert any(e["kind"] == "missing-image" for e in res.errors)


def test_pptx_revision_marks_stale_on_notes_and_visual_changes(tmp_path):
    v1 = _make_pptx(tmp_path / "deck_v1.pptx", deadline="24 hours", slide3_color=(30, 80, 140))
    st = Settings(root=tmp_path / "studio", pptx_rasteriser="fallback")
    app = create_app(st, run_worker=False)
    blobs = BlobStore(st.blob_dir)
    with TestClient(app, base_url="http://localhost") as c:
        pid = c.post("/api/v1/projects", json={"name": "DeckRev"}).json()["id"]
        up = c.post(f"/api/v1/projects/{pid}/sources",
                    files={"file": ("deck.pptx", v1.read_bytes())}).json()
        app.state.queue.drain()
        mid = c.get(f"/api/v1/projects/{pid}/modules").json()[0]["id"]
        c.post(f"/api/v1/modules/{mid}/storyboard")
        for sc in c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]:
            assert c.post(f"/api/v1/scenes/{sc['id']}/approve").status_code == 200

        # Case 1: notes + deadline text changed
        v2 = _make_pptx(tmp_path / "deck_v2.pptx", deadline="48 hours", slide3_color=(30, 80, 140))
        rep = revise_source(app.state.sf, blobs, up["source_id"], v2.read_bytes(), apply=True, pptx_rasteriser="fallback")
        assert mid in rep["stale_modules"]
        assert c.get(f"/api/v1/projects/{pid}/modules").json()[0]["status"] == "stale"
        regenerate_stale(app.state.sf, pid)
        scenes = c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]
        assert "48 hours" in " ".join(b["text"] for b in scenes[1]["beats"])
        for sc in scenes:
            assert c.post(f"/api/v1/scenes/{sc['id']}/approve").status_code == 200

        # Case 2: visual-only change on Slide 3 (text stays "Slide 3")
        v3 = _make_pptx(tmp_path / "deck_v3.pptx", deadline="48 hours", slide3_color=(180, 40, 40))
        rep2 = revise_source(app.state.sf, blobs, up["source_id"], v3.read_bytes(), apply=True, pptx_rasteriser="fallback")
        assert mid in rep2["stale_modules"]
        assert c.get(f"/api/v1/projects/{pid}/modules").json()[0]["status"] == "stale"
