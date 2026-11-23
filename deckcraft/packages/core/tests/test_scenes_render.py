import json
import subprocess

import pytest
from pydantic import ValidationError

from deckcraft_core.render.scene_render import SceneRenderer, load_theme, spec_hash
from deckcraft_core.scenes import LAYOUT_KINDS, Process, Scenario, Stat, parse_scene
from deckcraft_core.storyboard.build import C, build_module_scenes, choose_sequence, validate_scene

CLAIMS = [
    C(1, "obligation", "must", "Employees must wear a high-visibility vest on site at all times."),
    C(2, "prohibition", "must_not", "Employees must not use a mobile phone while driving."),
    C(3, "deadline", "must", "Report any injury to your supervisor within 24 hours."),
    C(4, "permission", "may", "Staff may take a 30 minute lunch break."),
    C(6, "prohibition", "must_not", "Employees must never use headphones while operating machinery."),
    C(5, "contact", "info", "Contact the safety officer on extension 204 for urgent hazards."),
]


def test_schema_limits():
    with pytest.raises(ValidationError):
        Process(heading="x", steps=[{"title": "a"}, {"title": "b"}])      # needs 3-5
    with pytest.raises(ValidationError):
        Stat(value="lots", label="x")                                     # needs a digit
    with pytest.raises(ValidationError):
        Scenario(situation="s", options=["a", "b"], answer_index=2, why="w")
    assert parse_scene({"kind": "stat", "value": "24 hours", "label": "x"}).kind == "stat"
    assert len(LAYOUT_KINDS) == 13


def test_storyboard_sequence_and_validation():
    seq = choose_sequence(CLAIMS)
    assert seq[0] == "cover" and seq[-1] == "takeaways" and {"compare", "stat"} <= set(seq)
    scenes = build_module_scenes("Site safety", CLAIMS)
    by_id = {c.id: c for c in CLAIMS}
    for s in scenes:
        assert [f for f in validate_scene(s, by_id) if f.severity == "error"] == [], s.kind


def test_validator_catches_bad_number_and_us_spelling():
    by_id = {c.id: c for c in CLAIMS}
    bad = Stat(value="48 hours", label="Report", claim_ids=[3])
    assert any(f.kind == "number" for f in validate_scene(bad, by_id))
    us = parse_scene({"kind": "statement", "headline": "Prioritize safety", "claim_ids": [1]})
    assert any(f.kind == "locale" for f in validate_scene(us, by_id, "AU"))


@pytest.fixture(scope="module")
def renderer():
    with SceneRenderer() as r:
        yield r


@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
@pytest.mark.parametrize("theme_id", ["emerald-editorial", "midnight", "sunrise"])
def test_every_layout_passes_linter(renderer, theme_id, orientation):
    theme = load_theme(theme_id)
    scenes = build_module_scenes("Site safety and incident reporting", CLAIMS)
    scenes += [parse_scene({"kind": "myth_fact", "myth": "Phones are fine at red lights.", "fact": "No phone use while driving.", "claim_ids": [2]}),
               parse_scene({"kind": "scenario", "situation": "You see a spill in the aisle.", "options": ["Walk around it", "Report it now"], "answer_index": 1, "why": "Hazards are reported immediately.", "claim_ids": [3]}),
               parse_scene({"kind": "process", "heading": "Report an injury", "steps": [{"title": "Stop work"}, {"title": "Tell your supervisor", "text": "Within 24 hours"}, {"title": "Complete the form"}], "claim_ids": [3]})]
    for s in scenes:
        res = renderer.lint(s.model_dump(), theme, orientation)
        assert res.errors == [], (s.kind, res.errors)
        assert res.empty_ratio < 0.5, (s.kind, theme_id, orientation, res.empty_ratio)


def test_render_is_deterministic(renderer):
    theme = load_theme("emerald-editorial")
    s = build_module_scenes("Site safety", CLAIMS)[1].model_dump()
    assert renderer.poster(s, theme, t=1.3) == renderer.poster(s, theme, t=1.3)
    assert spec_hash(s, theme, "portrait", 6, 30) == spec_hash(s, theme, "portrait", 6, 30)


def test_video_is_bt709_tagged(renderer, tmp_path):
    theme = load_theme("midnight")
    s = build_module_scenes("Site safety", CLAIMS)[0].model_dump()
    out = renderer.render_video(s, theme, tmp_path / "a.mp4", duration=1.0, fps=10)
    info = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=pix_fmt,color_space,color_transfer,color_primaries,color_range,width,height", "-of", "json", str(out)]))["streams"][0]
    assert info["pix_fmt"] == "yuv420p" and info["color_space"] == "bt709" and info["color_range"] == "tv"
    assert (info["width"], info["height"]) == (1080, 1920)
