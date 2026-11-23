import sqlite3
import subprocess
import sys

import sqlalchemy as sa

from deckcraft_core.db.migrate import current_revision, downgrade_db, upgrade_db
from deckcraft_core.db.models import Base, Project, make_session_factory
from deckcraft_core.export.module import export_module
from deckcraft_core.platform import unthrottle, unthrottle_tree
from deckcraft_core.render.scene_render import RenderPool, load_theme
from deckcraft_core.scenes import parse_scene
from deckcraft_core.script.beats import anchor_reveals, write_script_rule
from deckcraft_core.speech.tts import SilentProvider, SpeechService
from deckcraft_core.storyboard.build import C, build_module_scenes


def _framemd5(mp4) -> list[str]:
    out = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "framemd5", "-"],
        text=True,
    )
    return [line for line in out.splitlines() if line and not line.startswith("#")]


def test_ecoqos_helpers():
    if sys.platform == "win32":
        assert unthrottle() is True
        assert unthrottle_tree() >= 1
    else:
        assert unthrottle() is False
        assert unthrottle_tree() == 0


def test_render_pool_matches_brute_force_frame_for_frame(tmp_path):
    theme = load_theme("emerald-editorial")
    scene = parse_scene({
        "kind": "cards",
        "heading": "Site rules",
        "cards": [
            {"title": "Vests", "text": "Wear high-visibility vests on site."},
            {"title": "Phones", "text": "Never use mobile phones while driving."},
            {"title": "Reports", "text": "Report injuries within 24 hours."},
        ],
        "claim_ids": [1, 2, 3],
    }).model_dump()
    reveals = {"0": 0.4, "1": 1.4, "2": 2.4}
    brute = tmp_path / "brute.mp4"
    pooled = tmp_path / "pooled.mp4"

    with RenderPool(workers=1) as r1:
        r1.render_video(scene, theme, brute, orientation="portrait", duration=4.0, fps=15,
                        reveals=reveals, dedupe_static=False)

    with RenderPool(workers=3) as r3:
        r3.render_video(scene, theme, pooled, orientation="portrait", duration=4.0, fps=15,
                        reveals=reveals, dedupe_static=True)
        for orient in ("portrait", "landscape"):
            assert r3.lint(scene, theme, orient).errors == []
            assert r3.poster(scene, theme, orient, t=3.5).startswith(b"\x89PNG\r\n\x1a\n")

    assert _framemd5(brute) == _framemd5(pooled)


def test_render_pool_exports_multi_scene_module(tmp_path):
    claims = [
        C(1, "obligation", "must", "Employees must wear a high-visibility vest on site at all times."),
        C(2, "prohibition", "must_not", "Employees must not use a mobile phone while driving."),
        C(3, "deadline", "must", "Report any injury to your supervisor within 24 hours."),
        C(4, "permission", "may", "Staff may take a 30 minute lunch break."),
    ]
    by_id = {c.id: c for c in claims}
    scenes = build_module_scenes("Site safety", claims)[:3]
    pairs = [(s.model_dump(), anchor_reveals(s, write_script_rule(s, by_id))) for s in scenes]
    sp = SpeechService(SilentProvider(), tmp_path / "audio")
    with RenderPool(workers=3) as pool:
        res1 = export_module(pairs, load_theme("midnight"), sp, pool, tmp_path / "out", "m", fps=10)
        res2 = export_module(pairs, load_theme("midnight"), sp, pool, tmp_path / "out", "m", fps=10)
    assert res1.mp4.exists() and res2.cached_segments == 3


def test_alembic_schema_matches_models(tmp_path):
    from deckcraft_core import memory  # noqa: F401
    migrated_db = tmp_path / "migrated.db"
    metadata_db = tmp_path / "metadata.db"

    make_session_factory(migrated_db)
    eng_mig = sa.create_engine(f"sqlite:///{migrated_db}")
    assert current_revision(eng_mig) == "0003_section_image_notes"

    eng_meta = sa.create_engine(f"sqlite:///{metadata_db}")
    Base.metadata.create_all(eng_meta)

    insp_mig = sa.inspect(eng_mig)
    insp_meta = sa.inspect(eng_meta)
    mig_tables = {t for t in insp_mig.get_table_names() if t != "alembic_version"}
    meta_tables = set(insp_meta.get_table_names())
    assert mig_tables == meta_tables
    for tbl in meta_tables:
        mig_cols = [(c["name"], c["nullable"]) for c in insp_mig.get_columns(tbl)]
        meta_cols = [(c["name"], c["nullable"]) for c in insp_meta.get_columns(tbl)]
        assert mig_cols == meta_cols, tbl


def _create_legacy_db(path, *, include_claim_lead: bool) -> None:
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE project (
            id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, language VARCHAR(8) NOT NULL,
            locale VARCHAR(8) NOT NULL, orientation VARCHAR(10) NOT NULL, org_name VARCHAR(200) NOT NULL,
            series_name VARCHAR(200) NOT NULL, outro_text VARCHAR(200) NOT NULL, created_at DATETIME NOT NULL
        );
        CREATE TABLE source (
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES project(id),
            filename VARCHAR(300) NOT NULL, blob_sha VARCHAR(64) NOT NULL, mime VARCHAR(100) NOT NULL,
            page_count INTEGER NOT NULL, uploaded_at DATETIME NOT NULL
        );
        CREATE TABLE section (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES source(id),
            position INTEGER NOT NULL, heading_path VARCHAR(500) NOT NULL, level INTEGER NOT NULL,
            text TEXT NOT NULL, page_start INTEGER NOT NULL, page_end INTEGER NOT NULL
        );
        CREATE TABLE module (
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES project(id),
            number INTEGER NOT NULL, title VARCHAR(300) NOT NULL, status VARCHAR(20) NOT NULL,
            target_seconds INTEGER NOT NULL, language VARCHAR(8) NOT NULL, variant_of INTEGER
        );
        CREATE TABLE scene (
            id INTEGER PRIMARY KEY, module_id INTEGER NOT NULL REFERENCES module(id),
            position INTEGER NOT NULL, data JSON NOT NULL, beats JSON NOT NULL,
            status VARCHAR(12) NOT NULL, origin VARCHAR(8) NOT NULL, draft_text TEXT NOT NULL, rev INTEGER NOT NULL
        );
        CREATE TABLE source_version (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES source(id),
            version INTEGER NOT NULL, blob_sha VARCHAR(64) NOT NULL, uploaded_at DATETIME NOT NULL, summary JSON NOT NULL
        );
        CREATE TABLE job (
            id INTEGER PRIMARY KEY, kind VARCHAR(40) NOT NULL, project_id INTEGER, state VARCHAR(12) NOT NULL,
            payload JSON NOT NULL, progress FLOAT NOT NULL, message VARCHAR(500) NOT NULL, error TEXT NOT NULL,
            attempts INTEGER NOT NULL, heartbeat_at DATETIME, created_at DATETIME NOT NULL
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY, ts DATETIME NOT NULL, actor VARCHAR(40) NOT NULL,
            action VARCHAR(60) NOT NULL, entity VARCHAR(80) NOT NULL, detail JSON NOT NULL
        );
        CREATE TABLE style_example (
            id INTEGER PRIMARY KEY, project_id INTEGER, scene_kind VARCHAR(20) NOT NULL,
            draft TEXT NOT NULL, final TEXT NOT NULL, origin VARCHAR(10) NOT NULL,
            edit_ratio FLOAT NOT NULL, approved INTEGER NOT NULL, created_at DATETIME NOT NULL
        );
        CREATE TABLE style_guide (project_id INTEGER PRIMARY KEY, text TEXT NOT NULL);
    """)
    if include_claim_lead:
        c.execute("""
            CREATE TABLE claim (
                id INTEGER PRIMARY KEY, section_id INTEGER NOT NULL REFERENCES section(id),
                kind VARCHAR(20) NOT NULL, modality VARCHAR(10) NOT NULL, text TEXT NOT NULL,
                quote TEXT NOT NULL, page INTEGER NOT NULL, char_start INTEGER NOT NULL,
                char_end INTEGER NOT NULL, risk FLOAT NOT NULL, lead TEXT NOT NULL, item TEXT NOT NULL
            )
        """)
    else:
        c.execute("""
            CREATE TABLE claim (
                id INTEGER PRIMARY KEY, section_id INTEGER NOT NULL REFERENCES section(id),
                kind VARCHAR(20) NOT NULL, modality VARCHAR(10) NOT NULL, text TEXT NOT NULL,
                quote TEXT NOT NULL, page INTEGER NOT NULL, char_start INTEGER NOT NULL,
                char_end INTEGER NOT NULL, risk FLOAT NOT NULL
            )
        """)
    c.execute("""
        CREATE TABLE module_claim (
            module_id INTEGER NOT NULL REFERENCES module(id),
            claim_id INTEGER NOT NULL REFERENCES claim(id),
            role VARCHAR(10) NOT NULL, reason VARCHAR(300) NOT NULL,
            PRIMARY KEY (module_id, claim_id)
        )
    """)
    c.execute("INSERT INTO project VALUES (1, 'Legacy', 'en', 'AU', 'portrait', '', '', '', '2026-11-23 00:00:00')")
    c.execute("INSERT INTO source VALUES (1, 1, 'doc.pdf', 'abc', 'application/pdf', 1, '2026-11-23 00:00:00')")
    c.execute("INSERT INTO section VALUES (1, 1, 0, 'Safety', 1, 'Wear vests.', 1, 1)")
    if include_claim_lead:
        c.execute("INSERT INTO claim VALUES (1, 1, 'obligation', 'must', 'Wear vests.', 'Wear vests.', 1, 0, 11, 0.7, '', '')")
    else:
        c.execute("INSERT INTO claim VALUES (1, 1, 'obligation', 'must', 'Wear vests.', 'Wear vests.', 1, 0, 11, 0.7)")
    c.commit()
    c.close()


def test_legacy_dbs_upgrade_and_downgrade_round_trip(tmp_path):
    v1_db = tmp_path / "v1.db"
    v2_db = tmp_path / "v2.db"
    _create_legacy_db(v1_db, include_claim_lead=False)
    _create_legacy_db(v2_db, include_claim_lead=True)

    for p in (v1_db, v2_db):
        sf = make_session_factory(p)
        eng = sa.create_engine(f"sqlite:///{p}")
        assert current_revision(eng) == "0003_section_image_notes"
        insp = sa.inspect(eng)
        assert {"lead", "item"} <= {c["name"] for c in insp.get_columns("claim")}
        assert {"image_sha", "notes"} <= {c["name"] for c in insp.get_columns("section")}
        with sf() as s:
            assert s.get(Project, 1).name == "Legacy"

    eng = sa.create_engine(f"sqlite:///{v1_db}")
    assert downgrade_db(eng, "0001_initial") == "0001_initial"
    insp = sa.inspect(eng)
    assert "lead" not in {c["name"] for c in insp.get_columns("claim")}
    assert "image_sha" not in {c["name"] for c in insp.get_columns("section")}

    assert upgrade_db(eng, "head") == "0003_section_image_notes"
    insp = sa.inspect(eng)
    assert {"lead", "item"} <= {c["name"] for c in insp.get_columns("claim")}
    assert {"image_sha", "notes"} <= {c["name"] for c in insp.get_columns("section")}
