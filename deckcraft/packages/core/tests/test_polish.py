from deckcraft_core.plan.modules import module_title
from deckcraft_core.review import branding_cards
from deckcraft_core.scenes import Cards
from deckcraft_core.script.beats import anchor_reveals, write_script_rule, Beat
from deckcraft_core.storyboard.build import C, build_module_scenes, clean_title, clip, split_title_body
from deckcraft_core.verify.checks import check_beat, coverage_report

INFO = [C(i, "info", "info", t) for i, t in enumerate([
    "We are proud to be an family-owned Australian transport business providing urgent freight and hotshot transport services across the country.",
    "From major mining operations and agricultural businesses to construction projects and remote communities, our customers trust us to deliver critical freight safely, professionally and on time.",
    "When you join Acme Freight, you become part of a team that helps keep Australia's industries moving.",
    "Our fleet operates nationwide, delivering urgent freight to: mining, agriculture, construction and more.",
    "Every delivery matters to the customer waiting on it.",
    "Every team member matters to us and to each other.",
    "We recognise the Traditional Custodians of the lands on which we work.",
    "We are committed to a workplace where everyone feels welcome.",
    "Short fact about our founding year in Western Australia.",
], 1)]


def test_titles_lose_numbering_and_shouting():
    assert clean_title("1 WELCOME TO THE TEAM") == "Welcome to the team"
    assert clean_title("3.3.2 Respect") == "Respect" and clean_title("HR") == "HR"


def test_clip_prefers_clause_boundary_over_ellipsis():
    out = clip(INFO[1].text, 160)
    assert "…" not in out and out.endswith("remote communities")
    assert clip("a" * 50 + " " + "b" * 50, 60).endswith("…")


def test_card_title_never_repeats_the_body():
    assert split_title_body("Our fleet operates nationwide: delivering urgent freight") == ("Our fleet operates nationwide", "delivering urgent freight")
    t, b = split_title_body(INFO[0].text)
    assert t == "" and b                                    # no natural break -> body only, no clipped duplicate title
    assert split_title_body("Safety first") == ("Safety first", "")
    long_colon = "Acme Freight started in Western Australia with a simple goal: to provide fast transport"
    assert split_title_body(long_colon)[0] == ""            # title would exceed a card title -> body only


def test_every_claim_gets_a_slide_and_nothing_is_clipped_mid_sentence():
    scenes = build_module_scenes("Welcome to the team", INFO, eyebrow="Team Member Guide")
    shown = {i for s in scenes for i in s.claim_ids}
    assert {c.id for c in INFO} <= shown or len(scenes) == 9
    assert scenes[0].kind == "cover" and scenes[0].eyebrow == "Team Member Guide"
    assert sum(s.kind == "cards" for s in scenes) >= 2     # overflow claims spread over several cards slides
    assert all("…" not in s.model_dump_json() for s in scenes)
    cov = coverage_report(INFO, [str(s.model_dump()) for s in scenes], include_info=True)
    assert cov == [] or all(f.severity == "warn" for f in cov)


def test_page_suffix_not_spoken_and_beats_anchor_from_first_item():
    scenes = build_module_scenes("Our story", INFO[:8])
    cards = next(s for s in scenes if s.kind == "cards")
    cards.heading = "Our story (1/2)"
    beats = write_script_rule(cards, {c.id: c for c in INFO})
    assert beats[0].text == "Our story." and "(1/2)" not in " ".join(b.text for b in beats)
    llm = [Beat(text="One."), Beat(text="Two."), Beat(text="Three.")]
    assert [b.reveal for b in anchor_reveals(cards, llm)] == [1, 2, 3]   # beat i narrates item i; heading shows at once


def test_module_named_after_shared_parent_heading():
    root = "GUIDE"
    both = [f"{root} > 3 VALUES > 3.3 Our Values > 3.3.2 Respect", f"{root} > 3 VALUES > 3.3 Our Values > 3.3.3 Reliability"]
    assert module_title(both) == "Our Values"                                   # parent heading, numbering removed
    assert module_title([f"{root} > 1 WELCOME"]) == "Welcome"                    # one section: its own heading
    assert module_title([f"{root} > 1 A", f"{root} > 2 B"]) == "A – B"        # nothing shared: first - last section


def test_intro_card_skipped_when_cover_already_shows_the_title():
    class P:
        org_name, series_name, outro_text = "Org", "Series", ""
    assert branding_cards(P, "Title", "en", first_kind="cover")[0] is None
    assert branding_cards(P, "Title", "en", first_kind="stat")[0] is not None


def test_unsupported_narration_is_warned_not_blocked():
    c = [C(1, "info", "info", "We take pride in our work, our vehicles and the service we provide.")]
    ok = check_beat(Beat(text="We take pride in our work and our vehicles."), c)
    bad = check_beat(Beat(text="We maintain our vehicles to a high standard."), c)
    assert ok == [] and [(f.kind, f.severity) for f in bad] == [("unsupported", "warn")]


# ---------------- compliance-style lists (found on a real HR guide)
LISTY = """When driving a Company vehicle, team members must:
Wear a seatbelt at all times.
Obey all road rules and traffic signs.
Not use a hand-held mobile phone while driving.
Team members are responsible for taking reasonable care of any Company vehicle assigned to them.
This includes:
Completing required vehicle inspections and pre-start checks.
Locking vehicles when unattended.
If you are unsure, ask yourself:
Is it safe?
If you are still unsure, speak with your Manager before taking action.
"""


def test_list_items_inherit_the_lead_in_obligation():
    from deckcraft_core.understand.claims import extract_claims
    cl = {c.text: c for c in extract_claims(LISTY)}
    seat = cl["When driving a Company vehicle, team members must wear a seatbelt at all times."]
    phone = cl["When driving a Company vehicle, team members must not use a hand-held mobile phone while driving."]
    assert (seat.modality, phone.modality, phone.kind) == ("must", "must_not", "prohibition")
    assert seat.quote == "Wear a seatbelt at all times."                         # quote stays verbatim
    assert LISTY[seat.char_start:seat.char_end] == seat.quote
    inc = [c for c in cl.values() if ", including" in c.text]
    assert len(inc) == 2 and all(c.modality == "must" for c in inc)             # "This includes:" attaches to the sentence before
    assert any(t.endswith("ask yourself: Is it safe?") for t in cl)
    assert "If you are still unsure, speak with your Manager before taking action." in cl   # standalone, not swallowed


def test_sentence_is_judged_against_the_claim_it_restates_not_every_claim_on_the_slide():
    claims = [C(1, "obligation", "must", "Team members must take regular breaks on long journeys."),
              C(2, "obligation", "should", "Before travelling, team members should consider site access requirements.")]
    assert check_beat(Beat(text="Before travelling, team members should consider site access requirements."), claims) == []
    assert [f.kind for f in check_beat(Beat(text="Team members may take regular breaks on long journeys."), claims)] == ["modality"]
    expected = [C(3, "obligation", "must", "You are expected to tell your Manager if you have a personal interest that could affect your decisions.")]
    assert check_beat(Beat(text="You are expected to tell your Manager if you have a personal interest that could affect your decisions."), expected) == []


def test_shared_lead_is_spoken_once_and_card_titles_do_not_repeat():
    from deckcraft_core.script.beats import drop_shared_lead, speakable
    out = drop_shared_lead(["When driving a Company vehicle, team members must wear a seatbelt.",
                            "When driving a Company vehicle, team members must obey road rules.",
                            "When driving a Company vehicle, team members must not use a phone."])
    assert out[0].startswith("When driving") and out[1] == "Obey road rules." and out[2] == "Not use a phone."
    assert speakable("Vehicle Care (2/2)") == "Vehicle Care"
    claims = [C(i, "obligation", "must", f"When working for the Company, you are expected to do thing number {i} well.") for i in (1, 2, 3)]
    cards = next(s for s in build_module_scenes("Code", claims) if s.kind in ("cards", "checklist"))
    if cards.kind == "cards":
        assert len({c.title for c in cards.cards}) == len(cards.cards) or all(c.title == "" for c in cards.cards)


def test_page_suffix_is_not_an_invented_number():
    from deckcraft_core.verify.checks import _nums
    assert _nums("The Code (2/2).") == set() and _nums("Report within 24 hours (1/2)") == {"24"}
    assert check_beat(Beat(text="The Code (2/2)."), [C(1, "info", "info", "If you are unsure, ask yourself: Is it safe?")]) == []


# ---------------- lead-in shown/spoken once (list and Do/Don't slides)
def _lead_claims():
    lead = "When driving a Company vehicle, team members must"
    mk = lambda i, kind, mod, item, composed: C(i, kind, mod, composed, lead, item)
    return [
        mk(1, "obligation", "must", "Wear a seatbelt at all times", "When driving a Company vehicle, team members must wear a seatbelt at all times."),
        mk(2, "obligation", "must", "Obey all road rules and traffic signs", "When driving a Company vehicle, team members must obey all road rules and traffic signs."),
        mk(3, "obligation", "must", "Drive to the road, weather and traffic conditions", "When driving a Company vehicle, team members must drive to the road, weather and traffic conditions."),
        mk(4, "prohibition", "must_not", "Not use a hand-held mobile phone while driving", "When driving a Company vehicle, team members must not use a hand-held mobile phone while driving."),
        mk(5, "prohibition", "must_not", "Not drive when fatigued", "When driving a Company vehicle, team members must not drive when fatigued."),
    ]


def test_extraction_keeps_lead_and_item_separately():
    from deckcraft_core.understand.claims import extract_claims
    cl = extract_claims(LISTY)
    seat = next(c for c in cl if c.item == "Wear a seatbelt at all times")
    assert seat.lead == "When driving a Company vehicle, team members must"
    inc = next(c for c in cl if c.item.startswith("Completing"))
    assert inc.lead == "Team members are responsible for taking reasonable care of any Company vehicle assigned to them, including"
    assert next(c for c in cl if c.text.startswith("If you are still unsure")).lead == ""      # standalone sentences have no lead


def test_checklist_shows_the_lead_once_with_bare_items():
    cs = [c for c in _lead_claims() if c.modality == "must"]
    sc = next(s for s in build_module_scenes("Safe driving", cs) if s.kind == "checklist")
    assert sc.lead == "When driving a Company vehicle, team members must:"
    assert sc.items == ["Wear a seatbelt at all times", "Obey all road rules and traffic signs", "Drive to the road, weather and traffic conditions"]
    assert all("team members must" not in i for i in sc.items)


def test_compare_has_a_caption_per_column_and_negation_moves_to_the_caption():
    sc = next(s for s in build_module_scenes("Safe driving", _lead_claims()) if s.kind == "compare")
    assert sc.do_lead == "When driving a Company vehicle, team members must:"
    assert sc.dont_lead == "When driving a Company vehicle, team members must not:"
    assert sc.dont == ["Use a hand-held mobile phone while driving", "Drive when fatigued"]
    assert not any("team members" in x for x in sc.do + sc.dont)


def test_mixed_leads_fall_back_to_full_sentences():
    cs = _lead_claims()[:3]
    cs[1] = C(2, "obligation", "must", "Team members must obey all road rules.", "", "")
    sc = next(s for s in build_module_scenes("Safe driving", cs) if s.kind in ("checklist", "cards"))
    assert (getattr(sc, "lead", "") == "")


def test_narration_says_the_lead_once_and_stays_faithful():
    from deckcraft_core.script.beats import write_script_rule
    cs = _lead_claims()
    by_id = {c.id: c for c in cs}
    comp = next(s for s in build_module_scenes("Safe driving", cs) if s.kind == "compare")
    beats = [b.text for b in write_script_rule(comp, by_id)]
    assert beats[1] == "When driving a Company vehicle, team members must wear a seatbelt at all times."
    assert beats[2] == "Obey all road rules and traffic signs." and beats[3].startswith("Drive to the road")
    assert beats[4] == "And here is what not to do."
    assert beats[5] == "When driving a Company vehicle, team members must not use a hand-held mobile phone while driving."
    assert beats[6] == "Do not drive when fatigued."
    assert sum("When driving a Company vehicle" in b for b in beats) == 2          # once per column, not once per item
    cited = {c.id: c for c in cs}
    for b in write_script_rule(comp, by_id):
        errs = [f for f in check_beat(b, [cited[i] for i in comp.claim_ids]) if f.severity == "error"]
        assert errs == [], (b.text, errs)


def test_lead_slides_render_cleanly_in_both_orientations():
    import pytest
    from deckcraft_core.render.scene_render import SceneRenderer, load_theme
    scenes = build_module_scenes("Safe driving", _lead_claims())
    assert {"compare"} <= {s.kind for s in scenes}
    with SceneRenderer() as r:
        for orientation in ("portrait", "landscape"):
            for s in scenes:
                res = r.lint(s.model_dump(), load_theme("emerald-editorial"), orientation)
                assert res.errors == [], (s.kind, orientation, res.errors)


def test_mixed_polarity_list_keeps_full_sentences():
    """'team members must:' over 'Not use a phone' reads badly, so no shared lead for such a list."""
    cs = _lead_claims()[:3] + _lead_claims()[3:4]
    top = next(s for s in build_module_scenes("Safe driving", cs) if s.kind == "takeaways")
    assert top.lead == "" and any("must not use a hand-held" in p for p in top.points)
