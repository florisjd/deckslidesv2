"""Phase 1 pipeline: source blob -> sections -> claims -> module plan, persisted in SQLite."""
from __future__ import annotations
import tempfile
from pathlib import Path

from .blobs import BlobStore
from .db.models import AuditLog, Claim, Module, ModuleClaim, Section, Source
from .ingest.document import ingest
from .plan.modules import coverage, plan_modules
from .storyboard.build import clean_title
from .understand.claims import extract_claims


def add_source(sf, blobs: BlobStore, project_id: int, filename: str, data: bytes) -> int:
    sha = blobs.put(data)
    with sf() as s:
        src = Source(project_id=project_id, filename=filename, blob_sha=sha, mime="application/octet-stream")
        s.add(src)
        s.add(AuditLog(action="source.add", entity=f"source:{filename}", detail={"sha": sha}))
        s.commit()
        return src.id


def process_source(sf, blobs: BlobStore, source_id: int, progress=lambda p, m="": None) -> dict:
    with sf() as s:
        src = s.get(Source, source_id)
        sha, filename, project_id = src.blob_sha, src.filename, src.project_id
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / filename
        path.write_bytes(blobs.get(sha))
        progress(0.1, "Reading document")
        doc = ingest(path)

    progress(0.4, "Extracting claims and planning modules")
    # Single write transaction; never call progress() (a second writer) inside it.
    with sf() as s:
        src = s.get(Source, source_id)
        src.page_count = doc.page_count
        s.query(Section).filter(Section.source_id == source_id).delete()  # idempotent re-run
        rows = []
        for i, sec in enumerate(doc.sections):
            r = Section(source_id=source_id, position=i, heading_path=sec.heading_path[:500], level=sec.level,
                        text=sec.text, page_start=sec.page_start, page_end=sec.page_end)
            s.add(r)
            rows.append(r)
        s.flush()
        claim_dicts = []
        for r in rows:
            for c in extract_claims(r.text, r.page_start):
                row = Claim(section_id=r.id, kind=c.kind, modality=c.modality, text=c.text, quote=c.quote,
                            page=c.page, char_start=c.char_start, char_end=c.char_end, risk=c.risk, lead=c.lead, item=c.item)
                s.add(row)
                s.flush()
                claim_dicts.append({"id": row.id, "section_id": r.id, "modality": c.modality})
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
