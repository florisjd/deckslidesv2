"""FastAPI app. Thin routers over the pipeline; loopback-only by default."""
from __future__ import annotations
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pathlib import Path
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from ..blobs import BlobStore
from ..db.models import Claim, Job, Module, ModuleClaim, Project, Section, Source, make_session_factory
from ..jobs.queue import JobQueue
from ..pipeline import add_source, process_source
from ..settings import Settings
from .. import review as rv
from ..db.models import SceneRow, Project as ProjectRow


class ProjectIn(BaseModel):
    name: str
    language: str = "en"
    locale: str = "AU"


class BeatsIn(BaseModel):
    beats: list[dict]


class StyleIn(BaseModel):
    text: str


class BrandIn(BaseModel):
    org_name: str = ""
    series_name: str = ""
    outro_text: str = ""
    language: str | None = None


class DataIn(BaseModel):
    data: dict


def create_app(settings: Settings | None = None, run_worker: bool = True) -> FastAPI:
    st = settings or Settings()
    sf = make_session_factory(st.db_path)
    blobs = BlobStore(st.blob_dir)
    queue = JobQueue(sf)

    def ingest_handler(job: Job, ctx):
        res = process_source(sf, blobs, job.payload["source_id"], ctx.progress, pptx_rasteriser=st.pptx_rasteriser)
        ctx.progress(1.0, f"{res['claims']} claims, {res['modules']} modules")

    queue.register("ingest_source", ingest_handler)

    def export_handler(job: Job, ctx):
        res = rv.run_export(sf, st, job.payload["module_id"], ctx.progress)
        ctx.progress(1.0, f"{res['duration']:.0f}s, {res['lufs']:.1f} LUFS")

    queue.register("export_module", export_handler)
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            if not queue.run_next():
                time.sleep(0.3)

    @asynccontextmanager
    async def lifespan(_):
        queue.recover()
        if run_worker:
            threading.Thread(target=worker, daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="DeckCraft Core", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.queue, app.state.sf = queue, sf

    @app.post("/api/v1/projects")
    def create_project(p: ProjectIn):
        with sf() as s:
            row = Project(name=p.name, language=p.language, locale=p.locale)
            s.add(row)
            s.commit()
            return {"id": row.id, "name": row.name}

    @app.post("/api/v1/projects/{pid}/sources")
    async def upload(pid: int, file: UploadFile = File(...)):
        data = await file.read()
        if len(data) > st.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, "File too large")
        with sf() as s:
            if not s.get(Project, pid):
                raise HTTPException(404, "Project not found")
        name = (file.filename or "upload").replace("\\", "/").split("/")[-1]  # no path traversal
        sid = add_source(sf, blobs, pid, name, data)
        return {"source_id": sid, "job_id": queue.enqueue("ingest_source", {"source_id": sid}, pid)}

    @app.get("/api/v1/jobs/{jid}")
    def job(jid: int):
        with sf() as s:
            j = s.get(Job, jid)
            if not j:
                raise HTTPException(404)
            return {"id": j.id, "state": j.state, "progress": j.progress, "message": j.message, "error": j.error[:300]}

    @app.post("/api/v1/jobs/{jid}/cancel")
    def cancel(jid: int):
        queue.cancel(jid)
        return {"ok": True}

    @app.get("/api/v1/projects/{pid}/modules")
    def modules(pid: int):
        with sf() as s:
            out = []
            for m in s.query(Module).filter(Module.project_id == pid).order_by(Module.number):
                n = s.query(ModuleClaim).filter(ModuleClaim.module_id == m.id).count()
                out.append({"id": m.id, "number": m.number, "title": m.title, "seconds": m.target_seconds, "claims": n, "status": m.status, "language": m.language})
            return out

    @app.get("/api/v1/modules/{mid}/claims")
    def module_claims(mid: int):
        with sf() as s:
            rows = (s.query(Claim).join(ModuleClaim, ModuleClaim.claim_id == Claim.id)
                    .filter(ModuleClaim.module_id == mid).order_by(Claim.risk.desc()))
            return [{"id": c.id, "kind": c.kind, "modality": c.modality, "text": c.text, "page": c.page,
                     "quote": c.quote, "risk": c.risk} for c in rows]

    @app.post("/api/v1/modules/{mid}/storyboard")
    def storyboard(mid: int, use_llm: bool = False):
        llm = None
        if use_llm:
            from ..llm.ollama import OllamaLLM
            cand = OllamaLLM(st.ollama_url, st.llm_model)
            llm = cand if cand.available() else None
        return {"scenes": rv.storyboard(sf, mid, llm), "llm": llm is not None}

    @app.get("/api/v1/modules/{mid}/scenes")
    def scenes(mid: int):
        return rv.module_report(sf, mid)

    @app.put("/api/v1/scenes/{sid}/beats")
    def put_beats(sid: int, body: BeatsIn):
        rv.update_beats(sf, sid, body.beats)
        return {"ok": True}

    @app.post("/api/v1/scenes/{sid}/approve")
    def approve(sid: int):
        errs = rv.approve(sf, sid)
        if errs:
            raise HTTPException(409, {"blocking": errs})
        return {"ok": True}

    @app.get("/api/v1/scenes/{sid}/poster.png")
    def poster(sid: int, orientation: str = "portrait"):
        from ..render.scene_render import SceneRenderer, load_theme
        with sf() as s:
            row = s.get(SceneRow, sid)
            if not row:
                raise HTTPException(404)
            data = row.data
        with SceneRenderer(blobs=blobs) as r:
            return Response(r.poster(data, load_theme(st.theme), orientation), media_type="image/png")

    @app.post("/api/v1/modules/{mid}/export")
    def export(mid: int):
        with sf() as s:
            rows = s.query(SceneRow).filter(SceneRow.module_id == mid).all()
            if not rows or any(r.status != "approved" for r in rows):
                raise HTTPException(409, "All scenes must be approved first")
            pid = s.get(Module, mid).project_id
        return {"job_id": queue.enqueue("export_module", {"module_id": mid}, pid)}

    @app.get("/api/v1/modules/{mid}/files/{ext}")
    def files(mid: int, ext: str):
        if ext not in ("mp4", "srt", "vtt"):
            raise HTTPException(404)
        with sf() as s:
            n = s.get(Module, mid).number
        f = st.export_dir / f"module-{mid}" / f"module-{n}.{ext}"
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f)

    @app.get("/api/v1/scene-schema")
    def scene_schema():
        from ..scenes import SceneAdapter
        return SceneAdapter.json_schema()

    @app.put("/api/v1/scenes/{sid}/data")
    def put_scene(sid: int, body: DataIn):
        from pydantic import ValidationError
        try:
            return {"data": rv.update_scene_data(sf, sid, body.data)}
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False, include_context=False))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/v1/scenes/{sid}/move")
    def move(sid: int, direction: int):
        rv.move_scene(sf, sid, 1 if direction > 0 else -1)
        return {"ok": True}

    @app.delete("/api/v1/scenes/{sid}")
    def delete_scene(sid: int):
        rv.delete_scene(sf, sid)
        return {"ok": True}

    @app.get("/api/v1/claims/{cid}/source")
    def claim_source(cid: int):
        return rv.claim_source(sf, cid)

    @app.get("/api/v1/modules/{mid}/timeline")
    def timeline(mid: int):
        return rv.timeline(sf, mid)

    @app.post("/api/v1/sources/{sid}/revisions")
    async def revise(sid: int, file: UploadFile = File(...), apply: bool = False):
        from ..updates import revise_source
        data = await file.read()
        if len(data) > st.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, "File too large")
        try:
            return revise_source(sf, blobs, sid, data, apply=apply, pptx_rasteriser=st.pptx_rasteriser)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/v1/projects/{pid}/regenerate-stale")
    def regen(pid: int):
        from ..updates import regenerate_stale
        return regenerate_stale(sf, pid)

    @app.get("/api/v1/projects/{pid}/sources")
    def list_sources(pid: int):
        with sf() as s:
            return [{"id": x.id, "filename": x.filename, "pages": x.page_count} for x in s.query(Source).filter(Source.project_id == pid)]

    @app.get("/api/v1/projects/{pid}/style")
    def get_style(pid: int):
        from .. import memory
        with sf() as s:
            return {"text": memory.get_style_guide(s, pid), "metrics": memory.metrics(s, pid)}

    @app.put("/api/v1/projects/{pid}/style")
    def put_style(pid: int, body: StyleIn):
        from .. import memory
        with sf() as s:
            memory.set_style_guide(s, pid, body.text)
            s.commit()
        return {"ok": True}

    @app.put("/api/v1/projects/{pid}/brand")
    def put_brand(pid: int, body: BrandIn):
        with sf() as s:
            p = s.get(ProjectRow, pid)
            if not p:
                raise HTTPException(404)
            p.org_name, p.series_name, p.outro_text = body.org_name, body.series_name, body.outro_text
            if body.language:
                p.language = body.language
            s.commit()
        return {"ok": True}

    @app.post("/api/v1/modules/{mid}/translate")
    def translate(mid: int, language: str):
        if not st.enable_translation:
            raise HTTPException(403, "Translation is disabled (feature flag enable_translation)")
        from ..llm.ollama import OllamaLLM
        from ..review import translate_module
        llm = OllamaLLM(st.ollama_url, st.llm_model)
        if not llm.available():
            raise HTTPException(503, "No local model available")
        try:
            return {"module_id": translate_module(sf, mid, language, llm)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/review", response_class=HTMLResponse)
    def review_page():
        return (Path(__file__).parent / "review.html").read_text(encoding="utf-8")

    return app


def main():  # pragma: no cover
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8765)
