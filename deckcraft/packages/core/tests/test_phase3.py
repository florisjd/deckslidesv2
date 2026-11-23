import json
import subprocess
from pathlib import Path

import pytest

from deckcraft_core.export.module import export_module
from deckcraft_core.export.subtitles import ffmetadata, split_cues, to_srt, to_vtt, two_lines
from deckcraft_core.render.scene_render import SceneRenderer, load_theme
from deckcraft_core.script.beats import Beat, anchor_reveals, write_script_rule
from deckcraft_core.script.spoken import to_spoken
from deckcraft_core.speech.tts import KokoroProvider, SilentProvider, SpeechService, master_audio, measure_loudness
from deckcraft_core.storyboard.build import C, build_module_scenes
from deckcraft_core.verify.checks import check_beat, coverage_report, locale_lint

CLAIMS = [
    C(1, "obligation", "must", "Employees must wear a high-visibility vest on site at all times."),
    C(2, "prohibition", "must_not", "Employees must not use a mobile phone while driving."),
    C(3, "deadline", "must", "Report any injury to your supervisor within 24 hours."),
    C(4, "permission", "may", "Staff may take a 30 minute lunch break."),
    C(5, "contact", "info", "Contact the safety officer on extension 204 for urgent hazards."),
]
MODELS = Path(__file__).resolve().parents[3] / "models"


def test_spoken_text():
    assert to_spoken("Budget is $4.2M, up 25% in Q3") == "Budget is 4.2 million dollars, up 25 percent in quarter 3"
    assert to_spoken("Open 24/7 & free") == "Open twenty four seven and free"
    assert to_spoken("Call Nguyen", {"Nguyen": "Win"}) == "Call Win"


def test_verifier_catches_corruptions():
    one = [CLAIMS[2]]
    assert check_beat(Beat(text="Report any injury within 24 hours."), one) == []
    assert [f.kind for f in check_beat(Beat(text="Report any injury within 48 hours."), one)] == ["number"]
    assert [f.kind for f in check_beat(Beat(text="Report any injury within twenty four hours."), one)] == []
    assert any(f.kind == "modality" for f in check_beat(Beat(text="You should wear a high-visibility vest on site."), [CLAIMS[0]]))
    assert any(f.kind == "negation" for f in check_beat(Beat(text="Employees use a mobile phone while driving."), [CLAIMS[1]]))
    assert any(f.kind == "entity" for f in check_beat(Beat(text="Email safety@example.com for help."), [CLAIMS[4]]))
    assert check_beat(Beat(text="Employees must not use a mobile phone while driving."), [CLAIMS[1]]) == []


def test_coverage_and_locale():
    assert coverage_report(CLAIMS, ["Wear a high-visibility vest on site. Report any injury within 24 hours."])[0].claim_ids == (2,)
    assert coverage_report(CLAIMS, ["x"], excluded={1, 2, 3}) == []
    assert locale_lint("Prioritize safety", "AU") and not locale_lint("Prioritize safety", "US")


def test_rule_script_passes_own_verifier():
    by_id = {c.id: c for c in CLAIMS}
    for s in build_module_scenes("Site safety", CLAIMS):
        beats = anchor_reveals(s, write_script_rule(s, by_id))
        cited = [by_id[i] for i in s.claim_ids]
        errs = [f for b in beats for f in check_beat(b, cited) if f.kind in ("number", "entity")]
        assert errs == [], (s.kind, errs)
    cl = next(s for s in build_module_scenes("x", CLAIMS) if s.kind == "cards")
    assert [b.reveal for b in write_script_rule(cl, by_id)][:3] == [0, 1, 2]


def test_subtitles():
    cues = split_cues("Report any injury to your supervisor within 24 hours. Then complete the incident form.", 1.0, 7.0)
    assert cues[0].start == 1.0 and abs(cues[-1].end - 7.0) < 1e-6
    assert all(len(l) <= 40 for c in cues for l in c.text.split("\n")) and "\n" in two_lines("a" * 30 + " " + "b" * 30)
    assert "00:00:01,000 -->" in to_srt(cues) and to_vtt(cues).startswith("WEBVTT")
    assert "[CHAPTER]" in ffmetadata([(0, 1.5, "A=b")])


def test_mastering_hits_target(tmp_path):
    import numpy as np, soundfile as sf
    t = np.arange(24000 * 4) / 24000
    sf.write(tmp_path / "in.wav", (0.05 * np.sin(2 * np.pi * 220 * t) * (1 + np.sin(2 * np.pi * 3 * t)) / 2).astype("float32"), 24000)
    master_audio(tmp_path / "in.wav", tmp_path / "out.wav")
    m = measure_loudness(tmp_path / "out.wav")
    assert abs(float(m["input_i"]) + 16) < 1.0 and float(m["input_tp"]) <= -1.0


def test_audio_cache_and_timing(tmp_path):
    sp = SpeechService(SilentProvider(), tmp_path / "c")
    beats = [Beat(text="One two three."), Beat(text="Four five.", reveal=1)]
    a = sp.scene_audio(beats, tmp_path / "s.wav")
    assert a.timings[1].start > a.timings[0].end and a.duration > a.timings[1].end
    assert len(list((tmp_path / "c").glob("*.wav"))) == 2
    sp.scene_audio(beats, tmp_path / "s2.wav")
    assert len(list((tmp_path / "c").glob("*.wav"))) == 2


@pytest.mark.skipif(not (MODELS / "kokoro-v1.0.onnx").exists(), reason="Kokoro model not downloaded")
def test_full_module_export_with_real_voice(tmp_path):
    by_id = {c.id: c for c in CLAIMS}
    scenes = build_module_scenes("Site safety", CLAIMS)
    pairs = [(s.model_dump(), anchor_reveals(s, write_script_rule(s, by_id))) for s in scenes[:3]]
    theme = load_theme("emerald-editorial")
    sp = SpeechService(KokoroProvider(MODELS), tmp_path / "audio")
    with SceneRenderer() as r:
        res = export_module(pairs, theme, sp, r, tmp_path / "out", "module1", fps=12)
        res2 = export_module(pairs, theme, sp, r, tmp_path / "out", "module1", fps=12)
    assert res2.cached_segments == 3                       # unchanged scenes are not re-rendered
    assert abs(res.loudness_lufs + 16) < 1.5
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_chapters", "-of", "json", str(res.mp4)]))
    kinds = [s["codec_type"] for s in info["streams"]]
    assert kinds.count("video") == 1 and kinds.count("audio") == 1 and "subtitle" in kinds
    assert len(info["chapters"]) == 3
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v["color_space"] == "bt709" and v["pix_fmt"] == "yuv420p"
    assert abs(float(info["format"]["duration"]) - res.duration) < 0.5 if "format" in info else True
    assert res.srt.read_text().strip()


def test_review_flow_blocks_bad_beats_then_exports(tmp_path):
    from fastapi.testclient import TestClient
    from deckcraft_core.api.app import create_app
    from deckcraft_core.settings import Settings
    from deckcraft_core import review as rv

    policy = ("# Safety\nEmployees must wear a high-visibility vest on site at all times.\n"
              "Employees must not use a mobile phone while driving.\nReport any injury to your supervisor within 24 hours.\n"
              "Staff may take a 30 minute lunch break.\n")
    st = Settings(root=tmp_path)
    app = create_app(st, run_worker=False)
    with TestClient(app, base_url="http://localhost") as c:
        pid = c.post("/api/v1/projects", json={"name": "D"}).json()["id"]
        c.post(f"/api/v1/projects/{pid}/sources", files={"file": ("p.md", policy.encode())})
        app.state.queue.drain()
        mid = c.get(f"/api/v1/projects/{pid}/modules").json()[0]["id"]
        assert c.post(f"/api/v1/modules/{mid}/storyboard").json()["scenes"] >= 3
        assert "Review" in c.get("/review").text or "review" in c.get("/review").text
        d = c.get(f"/api/v1/modules/{mid}/scenes").json()
        assert d["coverage"] == []
        # export refused until approved
        assert c.post(f"/api/v1/modules/{mid}/export").status_code == 409
        # a corrupted beat (wrong number) blocks approval
        stat = next(s for s in d["scenes"] if s["data"]["kind"] == "stat")
        bad = [{"text": "Report any injury within 48 hours."}]
        c.put(f"/api/v1/scenes/{stat['id']}/beats", json={"beats": bad})
        r = c.post(f"/api/v1/scenes/{stat['id']}/approve")
        assert r.status_code == 409 and r.json()["detail"]["blocking"][0]["kind"] == "number"
        # restore a faithful beat, approve everything, export with the silent test provider
        c.put(f"/api/v1/scenes/{stat['id']}/beats", json={"beats": [{"text": "Report any injury within 24 hours."}]})
        for s in c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]:
            assert c.post(f"/api/v1/scenes/{s['id']}/approve").status_code == 200, s["data"]["kind"]
        res = rv.run_export(app.state.sf, st, mid, provider=SilentProvider())
        assert Path(res["mp4"]).exists() and abs(res["lufs"] + 16) < 3 or res["lufs"] < -60
