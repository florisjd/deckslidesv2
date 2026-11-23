"""Review service: storyboard a module, edit beats, verify, approve, export."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path

from . import memory
from .blobs import BlobStore
from .db.models import AuditLog, Claim, Module, ModuleClaim, Project, SceneRow, Section
from .export.module import export_module
from .render.scene_render import SceneRenderer, load_theme
from .scenes import ImageScene, parse_scene
from .i18n import is_english, tts_params, translate_beats, translate_scene
from .script.beats import Beat, anchor_reveals, write_script_rule
from .speech.tts import KokoroProvider, SpeechService
from .storyboard.build import C, build_module_scenes, clean_title, validate_scene
from .verify.checks import check_number_spoken, check_scene, coverage_report, locale_lint


def module_claims(s, module_id: int) -> list[C]:
    rows = (s.query(Claim).join(ModuleClaim, ModuleClaim.claim_id == Claim.id)
            .filter(ModuleClaim.module_id == module_id).order_by(Claim.id))
    return [C(r.id, r.kind, r.modality, r.text, r.lead or "", r.item or "") for r in rows]


def _module_sections(s, module_id: int) -> list[tuple[Section, list[int]]]:
    rows = (s.query(Section, Claim.id)
            .join(Claim, Claim.section_id == Section.id)
            .join(ModuleClaim, ModuleClaim.claim_id == Claim.id)
            .filter(ModuleClaim.module_id == module_id)
            .order_by(Section.position, Claim.id)
            .all())
    grouped: dict[int, tuple[Section, list[int]]] = {}
    for sec, cid in rows:
        if sec.id not in grouped:
            grouped[sec.id] = (sec, [])
        grouped[sec.id][1].append(cid)
    return list(grouped.values())


def storyboard(sf, module_id: int, llm=None, style_guide: str = "") -> int:
    with sf() as s:
        mod = s.get(Module, module_id)
        guide = memory.get_style_guide(s, mod.project_id) or style_guide
        claims = module_claims(s, module_id)
        by_id = {c.id: c for c in claims}
        series = (s.get(Project, mod.project_id).series_name or "").strip()
        sec_pairs = _module_sections(s, module_id)
        if sec_pairs and any(sec.image_sha for sec, _ in sec_pairs):
            scenes = [
                ImageScene(
                    image_sha=sec.image_sha,
                    caption=clean_title(sec.heading_path.split(" > ")[-1])[:120],
                    claim_ids=cids,
                    notes=sec.notes or "",
                )
                for sec, cids in sec_pairs if sec.image_sha
            ]
        else:
            scenes = build_module_scenes(mod.title, claims, eyebrow=series or "Training")
        s.query(SceneRow).filter(SceneRow.module_id == module_id).delete()
        for i, sc in enumerate(scenes):
            beats = None
            if llm is not None:
                try:
                    from .script.beats import write_script_llm
                    shots = memory.few_shots(s, mod.project_id, sc.kind, " ".join(by_id[c].text for c in sc.claim_ids if c in by_id))
                    beats = write_script_llm(llm, sc, by_id, memory.render_prompt_block(guide, shots))
                    if check_scene(beats, [by_id[c] for c in sc.claim_ids]):
                        beats = None            # LLM drifted from the claims: fall back to the rule script
                except Exception:  # noqa: BLE001
                    beats = None
            origin = "llm" if beats else "rule"
            beats = anchor_reveals(sc, beats or write_script_rule(sc, by_id))
            s.add(SceneRow(module_id=module_id, position=i, data=sc.model_dump(), origin=origin,
                           beats=[b.model_dump() for b in beats], draft_text=" ".join(b.text for b in beats)))
        s.add(AuditLog(action="storyboard", entity=f"module:{module_id}", detail={"scenes": len(scenes)}))
        s.commit()
        return len(scenes)


def scene_findings(row: SceneRow, by_id: dict[int, C], locale: str = "AU", language: str = "en") -> list[dict]:
    sc = parse_scene(row.data)
    beats = [Beat(**b) for b in row.beats]
    cited = [by_id[c] for c in sc.claim_ids if c in by_id]
    fs = [asdict(f) for f in check_scene(beats, cited, language)]
    fs += [asdict(f) for f in check_number_spoken(row.data, beats)]
    fs += [{"severity": f.severity, "kind": f.kind, "span": "", "message": f.message, "claim_ids": []}
           for f in validate_scene(sc, by_id, locale if is_english(language) else "")]
    for b in (beats if is_english(language) else []):
        fs += [asdict(f) for f in locale_lint(b.text, locale)]
    return fs


def module_report(sf, module_id: int) -> dict:
    with sf() as s:
        claims = module_claims(s, module_id)
        by_id = {c.id: c for c in claims}
        mod = s.get(Module, module_id)
        locale = s.get(Project, mod.project_id).locale
        language = mod.language
        rows = s.query(SceneRow).filter(SceneRow.module_id == module_id).order_by(SceneRow.position).all()
        scenes = [{"id": r.id, "position": r.position, "data": r.data, "beats": r.beats, "status": r.status,
                   "findings": scene_findings(r, by_id, locale, language),
                   "claims": [{"id": c, "text": by_id[c].text} for c in r.data.get("claim_ids", []) if c in by_id]} for r in rows]
        texts = [b["text"] for r in rows for b in r.beats] + [str(r.data) for r in rows]
        cov = [asdict(f) for f in coverage_report(claims, texts, include_info=True)] if is_english(language) else []  # token coverage is English-only
        return {"module": {"id": mod.id, "title": mod.title, "language": language}, "scenes": scenes, "coverage": cov}


def update_beats(sf, scene_id: int, beats: list[dict]) -> None:
    parsed = [Beat(**b).model_dump() for b in beats]
    with sf() as s:
        r = s.get(SceneRow, scene_id)
        s.add(AuditLog(action="beats.edit", entity=f"scene:{scene_id}", detail={"before": r.beats, "after": parsed}))
        r.beats, r.status, r.rev = parsed, "draft", r.rev + 1
        s.commit()


def approve(sf, scene_id: int) -> list[dict]:
    """Blocking findings prevent approval (not editing)."""
    with sf() as s:
        r = s.get(SceneRow, scene_id)
        mod = s.get(Module, r.module_id)
        by_id = {c.id: c for c in module_claims(s, r.module_id)}
        errs = [f for f in scene_findings(r, by_id, s.get(Project, mod.project_id).locale, mod.language) if f["severity"] == "error"]
        if errs:
            return errs
        r.status = "approved"
        final = " ".join(b["text"] for b in r.beats)
        if mod.language == "en":    # style memory learns from the project's source language only
            memory.record_edit(s, mod.project_id, r.data["kind"], r.draft_text, final, r.origin)
            memory.mark_approved(s, mod.project_id, r.data["kind"], final)
        s.add(AuditLog(action="scene.approve", entity=f"scene:{scene_id}", detail={"rev": r.rev}))
        s.flush()
        if all(x.status == "approved" for x in s.query(SceneRow).filter(SceneRow.module_id == r.module_id)):
            mod.status = "approved"
        s.commit()
        return []


SUB_LANG = {"en": "eng", "es": "spa", "fr": "fra", "it": "ita", "pt": "por"}


def branding_cards(proj, mod_title: str, language: str, first_kind: str | None = None):
    """Optional intro/outro cards from the project's branding (no text configured -> no card)."""
    intro = outro = None
    if (proj.org_name or proj.series_name) and first_kind != "cover":   # a cover slide already carries the title
        intro = ({"kind": "cover", "eyebrow": (proj.series_name or proj.org_name)[:60], "title": mod_title[:80],
                  "subtitle": proj.org_name[:120] if proj.series_name else "", "claim_ids": [], "notes": ""},
                 [Beat(text=mod_title[:120])])
    if proj.outro_text:
        outro = ({"kind": "statement", "headline": proj.outro_text[:140], "body": proj.org_name[:220],
                  "claim_ids": [], "notes": ""}, [])
    return intro, outro


def run_export(sf, settings, module_id: int, progress=lambda p, m="": None, provider=None, music=None) -> dict:
    with sf() as s:
        mod = s.get(Module, module_id)
        proj = s.get(Project, mod.project_id)
        rows = s.query(SceneRow).filter(SceneRow.module_id == module_id).order_by(SceneRow.position).all()
        if not rows or any(r.status != "approved" for r in rows):
            raise RuntimeError("All scenes must be approved before export")
        pairs = [(r.data, [Beat(**b) for b in r.beats]) for r in rows]
        title, orientation, language = mod.title, proj.orientation, mod.language
        intro, outro = branding_cards(proj, title, language, rows[0].data["kind"])
        name = f"module-{mod.number}" + ("" if language == "en" else f"-{language}")
    progress(0.05, "Loading voice")
    lang_code, voice = tts_params(language)
    if language == "en":
        voice = settings.voice
    provider = provider or KokoroProvider(settings.model_dir, lang=lang_code)
    speech = SpeechService(provider, settings.root / "audio-cache", voice=voice)
    with SceneRenderer(blobs=BlobStore(settings.blob_dir)) as r:
        res = export_module(pairs, load_theme(settings.theme), speech, r,
                            settings.export_dir / f"module-{module_id}", name, orientation,
                            intro=intro, outro=outro, music=music or settings.music_path,
                            sub_lang=SUB_LANG.get(language.split("-")[0], "eng"))
    with sf() as s:
        s.add(AuditLog(action="export", entity=f"module:{module_id}",
                       detail={"mp4": str(res.mp4), "lufs": res.loudness_lufs}))
        s.commit()
    return {"mp4": str(res.mp4), "duration": res.duration, "lufs": res.loudness_lufs}


def translate_module(sf, module_id: int, language: str, llm) -> int:
    """Create a translated variant of a module (feature-flagged at the API). Numbers must survive
    translation and every scene must still fit its layout; any failure aborts without writing."""
    from .i18n import LANGS
    if language not in LANGS or language.startswith("en"):
        raise ValueError(f"Unsupported target language {language!r}")
    with sf() as s:
        src = s.get(Module, module_id)
        rows = s.query(SceneRow).filter(SceneRow.module_id == module_id).order_by(SceneRow.position).all()
        translated = []
        for r in rows:
            sc = translate_scene(llm, parse_scene(r.data), language)
            texts = translate_beats(llm, [b["text"] for b in r.beats], language)
            translated.append((sc, [{**b, "text": tx} for b, tx in zip(r.beats, texts)]))
        title = translate_beats(llm, [src.title], language)[0]
        n = s.query(Module).filter(Module.project_id == src.project_id).count() + 1
        mod = Module(project_id=src.project_id, number=n, title=title[:300], language=language,
                     variant_of=src.id, target_seconds=src.target_seconds)
        s.add(mod)
        s.flush()
        for mc in s.query(ModuleClaim).filter(ModuleClaim.module_id == module_id):
            s.add(ModuleClaim(module_id=mod.id, claim_id=mc.claim_id, role=mc.role, reason=mc.reason))
        for i, (sc, beats) in enumerate(translated):
            s.add(SceneRow(module_id=mod.id, position=i, data=sc.model_dump(), beats=beats, origin="llm",
                           draft_text=" ".join(b["text"] for b in beats)))
        s.add(AuditLog(action="module.translate", entity=f"module:{module_id}",
                       detail={"language": language, "new": mod.id}))
        s.commit()
        return mod.id


def update_scene_data(sf, scene_id: int, data: dict) -> dict:
    """Schema-driven slide edit: the typed union validates; the scene goes back to draft."""
    sc = parse_scene(data)                      # raises ValidationError on capacity/invariant violations
    with sf() as s:
        r = s.get(SceneRow, scene_id)
        if sc.kind != r.data.get("kind"):
            raise ValueError("Changing a scene's kind is not supported; delete and add a new scene")
        s.add(AuditLog(action="scene.edit", entity=f"scene:{scene_id}", detail={"before": r.data, "after": sc.model_dump()}))
        r.data, r.status, r.rev = sc.model_dump(), "draft", r.rev + 1
        mod = s.get(Module, r.module_id)
        if mod.status == "approved":
            mod.status = "draft"
        s.commit()
        return r.data


def move_scene(sf, scene_id: int, direction: int) -> None:
    with sf() as s:
        r = s.get(SceneRow, scene_id)
        rows = s.query(SceneRow).filter(SceneRow.module_id == r.module_id).order_by(SceneRow.position).all()
        i = rows.index(r)
        j = i + direction
        if 0 <= j < len(rows):
            rows[i].position, rows[j].position = rows[j].position, rows[i].position
        s.commit()


def delete_scene(sf, scene_id: int) -> None:
    with sf() as s:
        r = s.get(SceneRow, scene_id)
        s.add(AuditLog(action="scene.delete", entity=f"scene:{scene_id}", detail={"data": r.data, "beats": r.beats}))
        s.delete(r)
        s.commit()


def claim_source(sf, claim_id: int) -> dict:
    """What the Source pane shows: the section text with the cited quote span."""
    with sf() as s:
        c = s.get(Claim, claim_id)
        sec = s.get(Section, c.section_id)
        return {"claim_id": c.id, "heading": sec.heading_path, "page": c.page, "text": sec.text,
                "start": c.char_start, "end": c.char_end}


def timeline(sf, module_id: int) -> dict:
    """Estimated timeline (before audio exists): per-scene beat durations."""
    from .script.beats import est_seconds
    with sf() as s:
        rows = s.query(SceneRow).filter(SceneRow.module_id == module_id).order_by(SceneRow.position).all()
        t, out = 0.0, []
        for r in rows:
            beats, bt = [], 0.5
            for b in r.beats:
                d = est_seconds(b["text"])
                beats.append({"start": round(bt, 2), "end": round(bt + d, 2), "reveal": b.get("reveal")})
                bt += d + b.get("pause_after_ms", 350) / 1000
            dur = bt + 0.7
            out.append({"scene_id": r.id, "kind": r.data["kind"], "start": round(t, 2), "duration": round(dur, 2), "beats": beats})
            t += dur
        return {"total": round(t, 2), "scenes": out}
