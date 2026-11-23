from fastapi.testclient import TestClient

from deckcraft_core.api.app import create_app
from deckcraft_core.settings import Settings

def _pad(tag, n):
    return "".join(f"Staff must complete {tag} training item {i} before starting work.\n" for i in range(n))


V1 = """# Safety
Employees must wear a high-visibility vest on site at all times.
Employees must not use a mobile phone while driving.
Report any injury to your supervisor within 24 hours.
""" + _pad("safety", 4) + """
# Leave
Annual leave should be requested at least 2 weeks in advance.
Staff may take a 30 minute lunch break.
""" + _pad("leave", 5) + """
# Conduct
Employees must treat colleagues with respect at all times.
Harassment of any kind is not permitted.
""" + _pad("conduct", 5)
# Safety: deadline 24 -> 48 hours (changed). Leave: untouched. Conduct: removed. Expenses: added.
V2 = V1.replace("within 24 hours", "within 48 hours").split("# Conduct")[0] + """# Expenses
Employees must submit expense claims within 30 days of purchase.
Receipts must be attached to every claim.
"""


def _setup(tmp_path):
    app = create_app(Settings(root=tmp_path), run_worker=False)
    c = TestClient(app, base_url="http://localhost")
    c.__enter__()
    pid = c.post("/api/v1/projects", json={"name": "D"}).json()["id"]
    sid = c.post(f"/api/v1/projects/{pid}/sources", files={"file": ("p.md", V1.encode())}).json()["source_id"]
    app.state.queue.drain()
    return app, c, pid, sid


def test_revision_preview_does_not_write(tmp_path):
    app, c, pid, sid = _setup(tmp_path)
    before = c.get(f"/api/v1/projects/{pid}/modules").json()
    r = c.post(f"/api/v1/sources/{sid}/revisions", files={"file": ("p.md", V2.encode())}).json()
    assert r["summary"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 1}
    assert c.get(f"/api/v1/projects/{pid}/modules").json() == before


def test_update_marks_only_affected_modules_stale(tmp_path):
    app, c, pid, sid = _setup(tmp_path)
    mods = c.get(f"/api/v1/projects/{pid}/modules").json()
    by_title = {m["title"]: m for m in mods}
    assert set(by_title) >= {"Safety", "Leave", "Conduct"}
    for m in mods:
        c.post(f"/api/v1/modules/{m['id']}/storyboard")
    leave_scenes = c.get(f"/api/v1/modules/{by_title['Leave']['id']}/scenes").json()["scenes"]
    leave_ids_before = [s["id"] for s in leave_scenes]

    rep = c.post(f"/api/v1/sources/{sid}/revisions?apply=true", files={"file": ("p.md", V2.encode())}).json()
    now = {m["title"]: m for m in c.get(f"/api/v1/projects/{pid}/modules").json()}
    assert now["Safety"]["status"] == "stale"        # a number changed
    assert now["Conduct"]["status"] == "stale"       # section removed
    assert now["Expenses"]["status"] == "stale"      # new section -> new module
    assert now["Leave"]["status"] != "stale"         # untouched
    assert len(rep["stale_modules"]) == 3

    # unchanged module keeps its scenes (same ids) and approvals untouched
    assert [s["id"] for s in c.get(f"/api/v1/modules/{now['Leave']['id']}/scenes").json()["scenes"]] == leave_ids_before

    # regenerate only the stale ones
    done = c.post(f"/api/v1/projects/{pid}/regenerate-stale").json()
    assert len(done["regenerated"]) == 2 and done["retired"] == [now["Conduct"]["id"]]   # emptied module is retired
    left = c.get(f"/api/v1/projects/{pid}/modules").json()
    assert {m["status"] for m in left} == {"draft"} and "Conduct" not in {m["title"] for m in left}
    claims = " ".join(x["text"] for x in c.get(f"/api/v1/modules/{now['Safety']['id']}/claims").json())
    assert "48 hours" in claims and "24 hours" not in claims


def test_scene_edit_is_schema_validated_and_resets_approval(tmp_path):
    app, c, pid, sid = _setup(tmp_path)
    mid = c.get(f"/api/v1/projects/{pid}/modules").json()[0]["id"]
    c.post(f"/api/v1/modules/{mid}/storyboard")
    scenes = c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]
    cover = scenes[0]
    assert c.post(f"/api/v1/scenes/{cover['id']}/approve").status_code == 200
    data = dict(cover["data"], title="Safety on site")
    assert c.put(f"/api/v1/scenes/{cover['id']}/data", json={"data": data}).status_code == 200
    st = {s["id"]: s for s in c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]}[cover["id"]]
    assert st["status"] == "draft" and st["data"]["title"] == "Safety on site"
    # capacity violation rejected; kind change rejected
    assert c.put(f"/api/v1/scenes/{cover['id']}/data", json={"data": dict(data, title="x" * 200)}).status_code == 422
    assert c.put(f"/api/v1/scenes/{cover['id']}/data", json={"data": {"kind": "statement", "headline": "h", "claim_ids": [1]}}).status_code == 422
    assert "oneOf" in c.get("/api/v1/scene-schema").text or "anyOf" in c.get("/api/v1/scene-schema").text


def test_reorder_delete_source_pane_timeline(tmp_path):
    app, c, pid, sid = _setup(tmp_path)
    mid = c.get(f"/api/v1/projects/{pid}/modules").json()[0]["id"]
    c.post(f"/api/v1/modules/{mid}/storyboard")
    sc = c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]
    first, second = sc[0]["id"], sc[1]["id"]
    c.post(f"/api/v1/scenes/{first}/move?direction=1")
    assert [s["id"] for s in c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]][:2] == [second, first]
    cid = next(cl["id"] for s in sc for cl in s["claims"])
    src = c.get(f"/api/v1/claims/{cid}/source").json()
    assert src["text"][src["start"]:src["end"]] and src["heading"]
    tl = c.get(f"/api/v1/modules/{mid}/timeline").json()
    assert tl["total"] > 0 and abs(tl["total"] - sum(x["duration"] for x in tl["scenes"])) < 0.1
    c.delete(f"/api/v1/scenes/{first}")
    assert len(c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]) == len(sc) - 1
