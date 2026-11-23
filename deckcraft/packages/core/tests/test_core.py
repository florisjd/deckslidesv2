import time
from fastapi.testclient import TestClient

from deckcraft_core.api.app import create_app
from deckcraft_core.blobs import BlobStore
from deckcraft_core.db.models import Job, make_session_factory
from deckcraft_core.jobs.queue import JobQueue
from deckcraft_core.plan.modules import coverage, plan_modules
from deckcraft_core.settings import Settings
from deckcraft_core.understand.claims import ClaimDraft, extract_claims, validate_quote

POLICY = """# Safety
Employees must wear a high-visibility vest on site at all times.
Employees must not use a mobile phone while driving.
Report any injury to your supervisor within 24 hours.
Staff may take a 30 minute lunch break.

# Leave
Annual leave should be requested at least 2 weeks in advance.
"""


def test_blobs_dedupe(tmp_path):
    b = BlobStore(tmp_path)
    s1, s2 = b.put(b"hello"), b.put(b"hello")
    assert s1 == s2 and b.get(s1) == b"hello"


def test_claim_modality_and_quotes():
    cs = extract_claims(POLICY)
    mods = {c.modality for c in cs}
    assert {"must", "must_not", "may", "should"} <= mods
    assert all(POLICY[c.char_start:c.char_end] == c.quote for c in cs)
    assert any(c.kind == "prohibition" and "mobile phone" in c.text for c in cs)


def test_hallucinated_quote_rejected():
    bad = ClaimDraft("obligation", "must", "x", "Employees must wear a hard hat.", 0, 10)
    assert not validate_quote(bad, POLICY)
    assert extract_claims(POLICY, llm_drafts=[bad]) == extract_claims(POLICY)


def test_planner_coverage_flags_unassigned_must():
    secs = [{"id": 1, "heading_path": "A", "text": "word " * 50}]
    claims = [{"id": 1, "section_id": 1, "modality": "must"}, {"id": 2, "section_id": 9, "modality": "must_not"}]
    plan = plan_modules(secs, claims)
    rep = coverage(plan, claims)
    assert rep.unassigned_must == [2] and not rep.ok
    assert coverage(plan, claims, excluded={2}).ok


def test_job_survives_restart(tmp_path):
    sf = make_session_factory(tmp_path / "t.db")
    q = JobQueue(sf)
    q.register("x", lambda j, c: None)
    jid = q.enqueue("x", {})
    with sf() as s:  # simulate a crash mid-run
        j = s.get(Job, jid)
        j.state = "running"
        s.commit()
    assert q.recover() == 1
    q.drain()
    with sf() as s:
        assert s.get(Job, jid).state == "done"


def test_job_retries_then_fails(tmp_path):
    sf = make_session_factory(tmp_path / "t.db")
    q = JobQueue(sf, max_attempts=2)

    def boom(j, c):
        raise RuntimeError("x")
    q.register("b", boom)
    jid = q.enqueue("b", {})
    q.drain()
    with sf() as s:
        j = s.get(Job, jid)
        assert j.state == "failed" and j.attempts == 2


def test_end_to_end_api(tmp_path):
    app = create_app(Settings(root=tmp_path), run_worker=False)
    with TestClient(app, base_url="http://localhost") as c:
        pid = c.post("/api/v1/projects", json={"name": "Demo"}).json()["id"]
        r = c.post(f"/api/v1/projects/{pid}/sources", files={"file": ("policy.md", POLICY.encode())}).json()
        app.state.queue.drain()
        jb = c.get(f"/api/v1/jobs/{r['job_id']}").json(); assert jb["state"] == "done", jb["error"]
        mods = c.get(f"/api/v1/projects/{pid}/modules").json()
        assert mods and sum(m["claims"] for m in mods) >= 5
        claims = c.get(f"/api/v1/modules/{mods[0]['id']}/claims").json()
        assert claims[0]["risk"] >= claims[-1]["risk"]
