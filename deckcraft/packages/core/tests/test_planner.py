from deckcraft_core.plan.modules import coverage, module_title, plan_modules


def _doc(spec):
    """spec: list of (heading_path, n_claims). Returns (sections, claims) with ids in document order."""
    sections, claims, cid = [], [], 0
    for sid, (path, n) in enumerate(spec, 1):
        sections.append({"id": sid, "heading_path": path, "text": "word " * 40})
        for _ in range(n):
            cid += 1
            claims.append({"id": cid, "section_id": sid, "modality": "must"})
    return sections, claims


def _plan(spec, **kw):
    secs, cl = _doc(spec)
    return plan_modules(secs, cl, **kw), cl


def test_tiny_module_is_absorbed_by_its_neighbour_in_the_same_chapter():
    plan, _ = _plan([("DOC > Vehicles > Driving", 10), ("DOC > Vehicles > Care", 3)])   # 13 > 12 -> split, then merged
    assert len(plan) == 1 and len(plan[0].claim_ids) == 13
    assert plan[0].title == "Vehicles"                       # the module holds every section under that chapter


def test_tiny_module_is_never_merged_across_chapters():
    plan, _ = _plan([("DOC > Vehicles > Driving", 10), ("DOC > Uniforms > Dress", 3)])
    assert [len(m.claim_ids) for m in plan] == [10, 3]


def test_grouping_never_spans_two_chapters():
    plan, _ = _plan([("DOC > A > a1", 3), ("DOC > B > b1", 3), ("DOC > B > b2", 3)])
    assert len(plan) == 2 and plan[0].title == "a1" and plan[1].title == "B"


def test_split_parts_are_never_merged_and_stay_balanced():
    plan, _ = _plan([("DOC > Code > The Code", 14), ("DOC > Code > Extra", 2)])
    sizes = [len(m.claim_ids) for m in plan]
    assert sizes[:2] == [7, 7]                               # balanced, not 12 + 2
    assert [m.part for m in plan[:2]] == [True, True]
    assert plan[0].title.endswith("(1/2)") and plan[1].title.endswith("(2/2)")


def test_merge_respects_the_cap_plus_slack():
    plan, _ = _plan([("DOC > C > a", 12), ("DOC > C > b", 4)])                 # 16 > 12 + 3
    assert [len(m.claim_ids) for m in plan] == [12, 4]
    plan, _ = _plan([("DOC > C > a", 12), ("DOC > C > b", 3)])                 # 15 <= 12 + 3
    assert [len(m.claim_ids) for m in plan] == [15]


def test_every_claim_appears_once_in_order_and_coverage_is_complete():
    spec = [("DOC > A > a1", 2), ("DOC > A > a2", 11), ("DOC > A > a3", 1), ("DOC > B > b1", 4), ("DOC > C > c1", 20), ("DOC > C > c2", 2)]
    secs, cl = _doc(spec)
    plan = plan_modules(secs, cl)
    flat = [c for m in plan for c in m.claim_ids]
    assert flat == [c["id"] for c in cl]
    assert coverage(plan, cl).ok and coverage(plan, cl).covered == len(cl)


def test_titles_are_unique_cleaned_and_describe_their_sections():
    secs, cl = _doc([("DOC > 1 WELCOME > 1.1 Intro", 7), ("DOC > 1 WELCOME > 1.2 Rules", 7), ("DOC > 2 SAFETY > 2.1 Vests", 6)])
    plan = plan_modules(secs, cl)
    titles = [m.title for m in plan]
    assert len(set(titles)) == len(titles)
    assert titles[0] == "Intro" and titles[1] == "Rules" and titles[2] == "Vests"       # numbering removed
    assert module_title(["DOC > X > 1.1 Intro", "DOC > X > 1.3 Wrap up"], ["DOC > X > 1.1 Intro", "DOC > X > 1.2 Mid", "DOC > X > 1.3 Wrap up"]) == "Intro – Wrap up"


def test_modules_of_a_real_sized_document_are_roughly_ninety_seconds():
    spec = [(f"DOC > Ch{c} > s{s}", n) for c in range(1, 5) for s, n in enumerate([5, 3, 6, 2, 7], 1)]
    plan, cl = _plan(spec)
    sizes = [len(m.claim_ids) for m in plan]
    assert min(sizes) >= 5 and max(sizes) <= 15, sizes
    assert len(plan) < len(spec)                 # sections were combined, not one module each
