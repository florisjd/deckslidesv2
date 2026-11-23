"""Policy-update workflow: diff a revised source against the stored one at section level,
keep unchanged claims (and therefore modules/scenes) intact, re-extract changed sections,
and mark only the affected modules stale."""
from __future__ import annotations
import difflib
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .blobs import BlobStore
from .db.models import AuditLog, Claim, Module, ModuleClaim, SceneRow, Section, Source, SourceVersion
from .ingest.document import SectionDoc, ingest
from .understand.claims import extract_claims

_norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()


@dataclass
class SectionChange:
    kind: str                 # added | removed | changed | unchanged
    heading: str
    similarity: float = 1.0
    old_id: int | None = None
    new_index: int | None = None


@dataclass
class SourceDiff:
    changes: list[SectionChange] = field(default_factory=list)

    def of(self, kind: str) -> list[SectionChange]:
        return [c for c in self.changes if c.kind == kind]

    @property
    def summary(self) -> dict:
        return {k: len(self.of(k)) for k in ("added", "removed", "changed", "unchanged")}


def _keys(headings: list[str]) -> list[tuple[str, int]]:
    seen: Counter = Counter()
    out = []
    for h in headings:
        out.append((_norm(h), seen[_norm(h)]))
        seen[_norm(h)] += 1
    return out


def diff_sections(old: list[tuple[int, str, str]], new: list[SectionDoc]) -> SourceDiff:
    """old: (section_id, heading, text). Match by heading, then rescue renamed headings by text similarity."""
    d = SourceDiff()
    okeys, nkeys = _keys([h for _, h, _ in old]), _keys([s.heading_path for s in new])
    nmap = {k: i for i, k in enumerate(nkeys)}
    used_new: set[int] = set()
    left_old = []
    for (sid, h, txt), k in zip(old, okeys):
        i = nmap.get(k)
        if i is None:
            left_old.append((sid, h, txt))
            continue
        used_new.add(i)
        same = _norm(txt) == _norm(new[i].text)
        sim = 1.0 if same else difflib.SequenceMatcher(None, _norm(txt), _norm(new[i].text)).quick_ratio()
        d.changes.append(SectionChange("unchanged" if same else "changed", h, sim, sid, i))
    for sid, h, txt in left_old:  # renamed heading?
        best, bi = 0.0, None
        for i, s in enumerate(new):
            if i in used_new:
                continue
            r = difflib.SequenceMatcher(None, _norm(txt), _norm(s.text)).ratio()
            if r > best:
                best, bi = r, i
        if bi is not None and best >= 0.7:
            used_new.add(bi)
            d.changes.append(SectionChange("changed", f"{h} → {new[bi].heading_path}", best, sid, bi))
        else:
            d.changes.append(SectionChange("removed", h, 0.0, sid, None))
    for i, s in enumerate(new):
        if i not in used_new:
            d.changes.append(SectionChange("added", s.heading_path, 0.0, None, i))
    return d


def _load(blobs: BlobStore, sha: str, filename: str):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / filename
        p.write_bytes(blobs.get(sha))
        return ingest(p)


def revise_source(sf, blobs: BlobStore, source_id: int, data: bytes, apply: bool = True) -> dict:
    sha = blobs.put(data) if apply else None
    with sf() as s:
        src = s.get(Source, source_id)
        filename, project_id = src.filename, src.project_id
        old = [(r.id, r.heading_path, r.text) for r in s.query(Section).filter(Section.source_id == source_id).order_by(Section.position)]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / filename
        p.write_bytes(data)
        doc = ingest(p)
    diff = diff_sections(old, doc.sections)
    report = {"summary": diff.summary, "changes": [
        {"kind": c.kind, "heading": c.heading, "similarity": round(c.similarity, 2)} for c in diff.changes if c.kind != "unchanged"],
        "stale_modules": [], "warnings": doc.warnings}
    if not apply:
        return report

    stale: set[int] = set()
    with sf() as s:
        mod_of_claim: dict[int, int] = {}
        for mc in s.query(ModuleClaim).join(Module).filter(Module.project_id == project_id):
            mod_of_claim[mc.claim_id] = mc.module_id

        def attach(section_id: int, claim_row: Claim, modules_hint: Counter):
            if modules_hint:
                mid = modules_hint.most_common(1)[0][0]
                s.add(ModuleClaim(module_id=mid, claim_id=claim_row.id))
                stale.add(mid)
                return mid
            return None

        for ch in diff.changes:
            if ch.kind == "unchanged":
                continue
            if ch.kind == "removed":
                for c in s.query(Claim).filter(Claim.section_id == ch.old_id):
                    if c.id in mod_of_claim:
                        stale.add(mod_of_claim[c.id])
                    s.query(ModuleClaim).filter(ModuleClaim.claim_id == c.id).delete()
                    s.delete(c)
                s.query(Section).filter(Section.id == ch.old_id).delete()
                continue
            nd = doc.sections[ch.new_index]
            if ch.kind == "changed":
                sec = s.get(Section, ch.old_id)
                sec.heading_path, sec.text, sec.level = nd.heading_path[:500], nd.text, nd.level
                sec.page_start, sec.page_end, sec.position = nd.page_start, nd.page_end, ch.new_index
                old_claims = {_norm(c.text): c for c in s.query(Claim).filter(Claim.section_id == sec.id)}
                hint = Counter(mod_of_claim[c.id] for c in old_claims.values() if c.id in mod_of_claim)
                fresh = {_norm(c.text): c for c in extract_claims(nd.text, nd.page_start)}
                for k, c in old_claims.items():
                    if k not in fresh:                       # claim removed or reworded
                        if c.id in mod_of_claim:
                            stale.add(mod_of_claim[c.id])
                        s.query(ModuleClaim).filter(ModuleClaim.claim_id == c.id).delete()
                        s.delete(c)
                    else:                                    # same sentence: keep id, refresh quote span
                        f = fresh[k]
                        c.quote, c.char_start, c.char_end, c.page, c.lead, c.item = f.quote, f.char_start, f.char_end, f.page, f.lead, f.item
                for k, f in fresh.items():
                    if k not in old_claims:
                        row = Claim(section_id=sec.id, kind=f.kind, modality=f.modality, text=f.text, quote=f.quote,
                                    page=f.page, char_start=f.char_start, char_end=f.char_end, risk=f.risk, lead=f.lead, item=f.item)
                        s.add(row)
                        s.flush()
                        attach(sec.id, row, hint)
            elif ch.kind == "added":
                sec = Section(source_id=source_id, position=ch.new_index, heading_path=nd.heading_path[:500], level=nd.level,
                              text=nd.text, page_start=nd.page_start, page_end=nd.page_end)
                s.add(sec)
                s.flush()
                rows = []
                for f in extract_claims(nd.text, nd.page_start):
                    row = Claim(section_id=sec.id, kind=f.kind, modality=f.modality, text=f.text, quote=f.quote,
                                page=f.page, char_start=f.char_start, char_end=f.char_end, risk=f.risk, lead=f.lead, item=f.item)
                    s.add(row)
                    rows.append(row)
                s.flush()
                if rows:
                    n = (s.query(Module).filter(Module.project_id == project_id).count()) + 1
                    mod = Module(project_id=project_id, number=n, title=nd.heading_path.split(" > ")[-1][:300], status="stale")
                    s.add(mod)
                    s.flush()
                    for r in rows:
                        s.add(ModuleClaim(module_id=mod.id, claim_id=r.id))
                    report.setdefault("new_modules", []).append(mod.id)
        # modules left with no claims are retired by marking stale (user decides to delete)
        for mid in stale:
            m = s.get(Module, mid)
            m.status = "stale"
            for sc in s.query(SceneRow).filter(SceneRow.module_id == mid):
                sc.status = "draft"
        src0 = s.get(Source, source_id)
        n_ver = s.query(SourceVersion).filter(SourceVersion.source_id == source_id).count()
        if n_ver == 0:  # the original upload becomes v1
            s.add(SourceVersion(source_id=source_id, version=1, blob_sha=src0.blob_sha, summary={"original": True}))
            n_ver = 1
        s.add(SourceVersion(source_id=source_id, version=n_ver + 1, blob_sha=sha, summary=report["summary"]))
        src = s.get(Source, source_id)
        src.blob_sha, src.page_count = sha, doc.page_count
        s.add(AuditLog(action="source.revise", entity=f"source:{source_id}", detail=report["summary"]))
        s.commit()
        report["stale_modules"] = sorted(stale | set(report.get("new_modules", [])))
    return report


def regenerate_stale(sf, project_id: int, llm=None) -> dict:
    """Regenerate only stale modules. A stale module whose claims are all gone is retired (audit-logged)."""
    from .review import storyboard
    regenerated, retired = [], []
    with sf() as s:
        ids = [m.id for m in s.query(Module).filter(Module.project_id == project_id, Module.status == "stale")]
    for mid in ids:
        with sf() as s:
            n = s.query(ModuleClaim).filter(ModuleClaim.module_id == mid).count()
            if n == 0:
                m = s.get(Module, mid)
                s.add(AuditLog(action="module.retire", entity=f"module:{mid}", detail={"title": m.title, "reason": "source section removed"}))
                s.query(SceneRow).filter(SceneRow.module_id == mid).delete()
                s.delete(m)
                s.commit()
                retired.append(mid)
                continue
        storyboard(sf, mid, llm)
        with sf() as s:
            s.get(Module, mid).status = "draft"
            s.commit()
        regenerated.append(mid)
    return {"regenerated": regenerated, "retired": retired}
