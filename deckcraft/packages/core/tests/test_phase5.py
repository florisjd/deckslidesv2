import json
import re
import subprocess

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from deckcraft_core import memory
from deckcraft_core.api.app import create_app
from deckcraft_core.db.models import Module, SceneRow, make_session_factory
from deckcraft_core.export.module import export_module
from deckcraft_core.export.music import mix_music
from deckcraft_core.i18n import Translated, translate_scene, translate_strings, tts_params
from deckcraft_core.render.scene_render import SceneRenderer, load_theme
from deckcraft_core.scenes import parse_scene
from deckcraft_core.script.beats import Beat
from deckcraft_core.settings import Settings
from deckcraft_core.speech.tts import SilentProvider, SpeechService, measure_loudness
from deckcraft_core.verify.checks import check_beat
from deckcraft_core.storyboard.build import C


class FakeLLM:
    """Pretends to translate: prefixes each numbered line. `mangle` changes digits to simulate a bad model."""
    def __init__(self, mangle=False, drop=False):
        self.mangle, self.drop = mangle, drop

    def generate(self, messages, schema, temperature=0.4):
        lines = [re.sub(r"^\d+\.\s*", "", l) for l in messages[-1]["content"].split("\n")]
        out = [("[es] " + l).replace("24", "42") if self.mangle else "[es] " + l for l in lines]
        return Translated(lines=out[:-1] if self.drop else out)


# ---------------- i18n
def test_translation_preserves_numbers_and_length_checks():
    assert translate_strings(FakeLLM(), ["Report within 24 hours."], "es") == ["[es] Report within 24 hours."]
    with pytest.raises(ValueError, match="numbers changed"):
        translate_strings(FakeLLM(mangle=True), ["Report within 24 hours."], "es")
    with pytest.raises(ValueError, match="lines"):
        translate_strings(FakeLLM(drop=True), ["a b c", "d e f"], "es")


def test_translate_scene_keeps_structure_and_skips_ids():
    sc = parse_scene({"kind": "stat", "value": "24 hours", "label": "Report injuries", "body": "Tell your supervisor.", "claim_ids": [3]})
    out = translate_scene(FakeLLM(), sc, "es")
    assert out.kind == "stat" and out.claim_ids == [3] and out.label.startswith("[es]") and out.value.startswith("[es]")


def test_translated_scene_that_overflows_is_rejected():
    class Long(FakeLLM):
        def generate(self, messages, schema, temperature=0.4):
            n = len(messages[-1]["content"].split("\n"))
            return Translated(lines=["x" * 300] * n)
    sc = parse_scene({"kind": "cover", "title": "Hello", "subtitle": "", "claim_ids": []})
    with pytest.raises(Exception):
        translate_scene(Long(), sc, "es")


def test_voices_and_english_only_checks():
    assert tts_params("es")[1] == "ef_dora" and tts_params("fr")[0] == "fr-fr"
    must = C(2, "prohibition", "must_not", "Employees must not use a mobile phone while driving.")
    soft = Beat(text="You can use a phone while driving, a mobile phone is fine.")
    assert any(f.kind == "negation" for f in check_beat(soft, [must], "en"))
    assert check_beat(soft, [must], "es") == []                          # English lexicons not applied to other languages
    assert any(f.kind == "number" for f in check_beat(Beat(text="Informe en 48 horas."), [C(3, "deadline", "must", "Report within 24 hours.")], "es"))


# ---------------- style memory
def test_edit_ratio_and_few_shots(tmp_path):
    assert memory.edit_ratio("a b c d", "a b c d") == 0 and memory.edit_ratio("a b c d", "w x y z") == 1
    sf_ = make_session_factory(tmp_path / "m.db")
    with sf_() as s:
        memory.set_style_guide(s, 1, "Speak plainly. Never say 'ensure'.")
        memory.record_edit(s, 1, "checklist", "Employees shall ensure vest wear.", "Wear your vest on site.", "rule")
        memory.record_edit(s, 1, "cover", "Intro.", "Welcome to site safety.", "rule")
        memory.mark_approved(s, 1, "checklist", "Wear your vest on site.")
        s.commit()
        shots = memory.few_shots(s, 1, "checklist", "vest on site rules")
        assert shots == [("Employees shall ensure vest wear.", "Wear your vest on site.")]   # same kind only
        block = memory.render_prompt_block(memory.get_style_guide(s, 1), shots)
        assert "Never say 'ensure'" in block and "Wear your vest" in block
        m = memory.metrics(s, 1)
        assert m["examples"] == 2 and m["approved"] == 1 and m["mean_edit_ratio"] > 0.5


POLICY = ("# Safety\nEmployees must wear a high-visibility vest on site at all times.\n"
          "Employees must not use a mobile phone while driving.\nReport any injury to your supervisor within 24 hours.\n"
          "Staff may take a 30 minute lunch break.\n")


def _project(tmp_path, **kw):
    st = Settings(root=tmp_path, **kw)
    app = create_app(st, run_worker=False)
    c = TestClient(app, base_url="http://localhost")
    c.__enter__()
    pid = c.post("/api/v1/projects", json={"name": "D"}).json()["id"]
    c.post(f"/api/v1/projects/{pid}/sources", files={"file": ("p.md", POLICY.encode())})
    app.state.queue.drain()
    mid = c.get(f"/api/v1/projects/{pid}/modules").json()[0]["id"]
    c.post(f"/api/v1/modules/{mid}/storyboard")
    return st, app, c, pid, mid


def test_approval_feeds_style_memory(tmp_path):
    st, app, c, pid, mid = _project(tmp_path)
    scenes = c.get(f"/api/v1/modules/{mid}/scenes").json()["scenes"]
    cover = scenes[0]
    beats = [{"text": "Welcome. Let us keep each other safe on site.", "reveal": None}]
    c.put(f"/api/v1/scenes/{cover['id']}/beats", json={"beats": beats})
    assert c.post(f"/api/v1/scenes/{cover['id']}/approve").status_code == 200
    m = c.get(f"/api/v1/projects/{pid}/style").json()["metrics"]
    assert m["examples"] == 1 and m["approved"] == 1 and m["mean_edit_ratio"] > 0
    c.put(f"/api/v1/projects/{pid}/style", json={"text": "Warm, direct."})
    assert c.get(f"/api/v1/projects/{pid}/style").json()["text"] == "Warm, direct."


# ---------------- translation endpoint + variant module
def test_translate_flag_and_variant(tmp_path):
    from deckcraft_core.review import translate_module, module_report
    st, app, c, pid, mid = _project(tmp_path)
    assert c.post(f"/api/v1/modules/{mid}/translate?language=es").status_code == 403    # flag off by default
    new = translate_module(app.state.sf, mid, "es", FakeLLM())
    mods = {m["id"]: m for m in c.get(f"/api/v1/projects/{pid}/modules").json()}
    assert mods[new]["language"] == "es" and mods[new]["claims"] == mods[mid]["claims"]
    rep = c.get(f"/api/v1/modules/{new}/scenes").json()
    assert rep["coverage"] == [] and all(s["status"] == "draft" for s in rep["scenes"])
    assert all(f["severity"] != "error" for s in rep["scenes"] for f in s["findings"]), [s["findings"] for s in rep["scenes"]]
    with pytest.raises(ValueError):
        translate_module(app.state.sf, mid, "en", FakeLLM())


# ---------------- music, intro/outro
def _tone(freq, secs, amp, sr=48000):
    t = np.arange(int(sr * secs)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype("float32")


def test_music_ducks_under_narration(tmp_path):
    sr = 48000
    nar = np.concatenate([np.zeros(sr * 2), _tone(300, 3, 0.4) * (1 + np.sin(np.arange(sr * 3) * 0.002)) / 2, np.zeros(sr * 2)]).astype("float32")
    sf.write(tmp_path / "nar.wav", nar, sr)
    sf.write(tmp_path / "mus.wav", _tone(2000, 7, 0.5), sr)
    mix_music(tmp_path / "nar.wav", tmp_path / "mus.wav", tmp_path / "mix.wav", 7.0, fade_out_s=0.5)
    mix, _ = sf.read(tmp_path / "mix.wav", dtype="float32")
    mono = mix.mean(axis=1) if mix.ndim > 1 else mix

    def music_energy(a, b):
        seg = mono[int(a * sr):int(b * sr)]
        spec = np.abs(np.fft.rfft(seg))
        f = np.fft.rfftfreq(len(seg), 1 / sr)
        return spec[(f > 1900) & (f < 2100)].sum() / len(seg)
    gap, speech = music_energy(0.5, 1.5), music_energy(3.0, 4.5)   # 0-2s silence, 2-5s speech
    assert speech < gap * 0.6, (gap, speech)                         # bed dips while narration plays


def test_export_with_intro_outro_and_music(tmp_path):
    scene = {"kind": "cover", "eyebrow": "Training", "title": "Site safety", "subtitle": "", "claim_ids": [1], "notes": ""}
    st = Settings(root=tmp_path)
    class P:  # fake project
        org_name, series_name, outro_text = "Nationwide Co", "Safety Series", "Thanks for watching"
    from deckcraft_core.review import branding_cards
    intro, outro = branding_cards(P, "Site safety", "en")
    assert intro[0]["eyebrow"] == "Safety Series" and outro[0]["headline"] == "Thanks for watching"
    sf.write(tmp_path / "bed.wav", _tone(440, 5, 0.3, 44100), 44100)
    sp = SpeechService(SilentProvider(), tmp_path / "a")
    with SceneRenderer() as r:
        res = export_module([(scene, [Beat(text="One two three four.")])], load_theme("midnight"), sp, r,
                            tmp_path / "out", "m", fps=8, intro=intro, outro=outro, music=tmp_path / "bed.wav", card_hold=2.0)
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_chapters", "-show_streams", "-of", "json", str(res.mp4)]))
    assert len(info["chapters"]) == 3                                 # intro + scene + outro
    assert res.chapters[0][1] - res.chapters[0][0] >= 2.0 and res.chapters[-1][1] - res.chapters[-1][0] >= 2.0
    assert [s["codec_type"] for s in info["streams"]].count("audio") == 1
    assert abs(res.loudness_lufs + 16) < 1.5, res.loudness_lufs


# ---------------- real local model smoke (skipped when unavailable)
def _ollama():
    from deckcraft_core.llm.ollama import OllamaLLM
    llm = OllamaLLM()
    return llm if llm.available() else None


@pytest.mark.skipif(_ollama() is None, reason="Ollama / gemma3 not available")
def test_real_model_translation_preserves_numbers():
    sc = parse_scene({"kind": "checklist", "heading": "Site rules", "items": [
        "Wear a high-visibility vest at all times", "Report injuries within 24 hours", "Never use a phone while driving"], "claim_ids": [1]})
    out = translate_scene(_ollama(), sc, "es")
    assert "24" in json.dumps(out.model_dump()) and out.kind == "checklist"
    print(out.model_dump())


def test_verifier_flags_paraphrased_strength_and_omitted_number():
    from deckcraft_core.verify.checks import check_number_spoken
    vest = C(1, "obligation", "must", "Employees must wear a high-visibility vest on site at all times.")
    fs = check_beat(Beat(text="It is important that everyone wears a high-visibility vest on site."), [vest])
    assert [(f.kind, f.severity) for f in fs] == [("modality-soft", "warn")]
    assert check_beat(Beat(text="You must wear a high-visibility vest on site."), [vest]) == []
    stat = {"kind": "stat", "value": "24 hours", "label": "x"}
    assert check_number_spoken(stat, [Beat(text="Report injuries promptly.")])[0].kind == "number-omitted"
    assert check_number_spoken(stat, [Beat(text="Report injuries within twenty four hours.")]) == []


def test_number_words_are_really_parsed():
    from deckcraft_core.verify.checks import _nums
    assert _nums("within twenty four hours") == {"24"} and _nums("no one is exempt") == set()
    one = [C(3, "deadline", "must", "Report any injury to your supervisor within 24 hours.")]
    assert [f.kind for f in check_beat(Beat(text="Report any injury within forty eight hours."), one)] == ["number"]  # a wrong number is still caught (via "eight")
    assert [f.kind for f in check_beat(Beat(text="Report any injury within twelve hours."), one)] == ["number"]
