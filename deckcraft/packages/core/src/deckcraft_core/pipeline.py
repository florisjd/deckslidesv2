"""Phase 1 pipeline: source blob -> sections -> claims -> module plan, persisted in SQLite."""
from __future__ import annotations
import tempfile
from pathlib import Path

from .blobs import BlobStore
from .db.models import AuditLog, Claim, Module, ModuleClaim, Section, Source
from .ingest.document import ingest
from .plan.modules import PlannedModule, coverage, plan_modules
from .storyboard.build import clean_title
from .understand.claims import ClaimDraft, extract_claims

MIME_MAP = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text": "text/plain",
}


def add_source(sf, blobs: BlobStore, project_id: int, filename: str, data: bytes) -> int:
    sha = blobs.put(data)
    with sf() as s:
        src = Source(project_id=project_id, filename=filename, blob_sha=sha, mime="application/octet-stream")
        s.add(src)
        s.add(AuditLog(action="source.add", entity=f"source:{filename}", detail={"sha": sha}))
        s.commit()
        return src.id


def process_source(sf, blobs: BlobStore, source_id: int, progress=lambda p, m="": None,
                   pptx_rasteriser: str = "auto") -> dict:
    with sf() as s:
        src = s.get(Source, source_id)
        sha, filename, project_id = src.blob_sha, src.filename, src.project_id
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / filename
        path.write_bytes(blobs.get(sha))
        progress(0.1, "Reading document")
        doc = ingest(path, pptx_rasteriser=pptx_rasteriser)

    progress(0.4, "Extracting claims and planning modules")
    # Single write transaction; never call progress() (a second writer) inside it.
    with sf() as s:
        src = s.get(Source, source_id)
        src.page_count = doc.page_count
        src.mime = MIME_MAP.get(doc.source_kind, "application/octet-stream")
        s.query(Section).filter(Section.source_id == source_id).delete()  # idempotent re-run
        rows = []
        for i, sec in enumerate(doc.sections):
            img_sha = blobs.put(sec.image_bytes) if sec.image_bytes is not None else ""
            r = Section(source_id=source_id, position=i, heading_path=sec.heading_path[:500], level=sec.level,
                        text=sec.text, page_start=sec.page_start, page_end=sec.page_end,
                        image_sha=img_sha, notes=sec.notes)
            s.add(r)
            rows.append(r)
        s.flush()
        claim_dicts = []
        for r in rows:
            extracted = extract_claims(r.text, r.page_start)
            if not extracted and doc.source_kind == "pptx" and r.text.strip():
                q = r.text.strip()
                extracted = [ClaimDraft("info", "info", q, q, 0, len(q), r.page_start, 0.1)]
            for c in extracted:
                row = Claim(section_id=r.id, kind=c.kind, modality=c.modality, text=c.text, quote=c.quote,
                            page=c.page, char_start=c.char_start, char_end=c.char_end, risk=c.risk, lead=c.lead, item=c.item)
                s.add(row)
                s.flush()
                claim_dicts.append({"id": row.id, "section_id": r.id, "modality": c.modality})
        if doc.source_kind == "pptx" and rows:
            base_title = clean_title(Path(filename).stem) or Path(filename).stem
            n = max(1, -(-len(rows) // 12))
            size = -(-len(rows) // n)
            by_sec: dict[int, list[int]] = {}
            for cd in claim_dicts:
                by_sec.setdefault(cd["section_id"], []).append(cd["id"])
            plan = []
            for i in range(n):
                part_rows = rows[i * size:(i + 1) * size]
                cids = [cid for r in part_rows for cid in by_sec.get(r.id, [])]
                words = sum(len(r.text.split()) for r in part_rows)
                title = base_title if n == 1 else f"{base_title} ({i + 1}/{n})"
                plan.append(PlannedModule(title, [r.id for r in part_rows], cids, words))
        else:
            plan = plan_modules([{"id": r.id, "heading_path": r.heading_path, "text": r.text} for r in rows], claim_dicts)
        for n, m in enumerate(plan, 1):
            mod = Module(project_id=project_id, number=n, title=clean_title(m.title)[:300], target_seconds=max(30, m.seconds))
            s.add(mod)
            s.flush()
            for cid in m.claim_ids:
                s.add(ModuleClaim(module_id=mod.id, claim_id=cid))
        cov = coverage(plan, claim_dicts)
        s.commit()
    return {"sections": len(rows), "claims": len(claim_dicts), "modules": len(plan),
            "unassigned_must": cov.unassigned_must, "warnings": doc.warnings}
