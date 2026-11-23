"""Deterministic module planner with coverage accounting (LLM only names modules later)."""
from __future__ import annotations
from dataclasses import dataclass, field

WPM = 150


@dataclass
class PlannedModule:
    title: str
    section_ids: list[int]
    claim_ids: list[int]
    words: int = 0
    paths: list[str] = field(default_factory=list)   # heading paths of the sections it covers
    part: bool = False                                # one piece of an oversized section ("(1/2)"): never merged

    @property
    def seconds(self) -> int:
        return int(self.words / WPM * 60)


@dataclass
class CoverageReport:
    total: int
    covered: int
    unassigned_must: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unassigned_must


def module_title(paths: list[str], universe: list[str] | None = None) -> str:
    """One section -> its own heading. Several sections -> the shared parent heading when the module holds every
    section under that parent, otherwise 'first section – last section'."""
    from ..storyboard.build import clean_title
    parts = [p.split(" > ") for p in paths]
    if len(paths) == 1:
        return clean_title(parts[0][-1])
    common = []
    for level in zip(*parts):
        if len(set(level)) != 1:
            break
        common.append(level[0])
    if len(common) >= 2:
        under = [u for u in (universe or paths) if u.split(" > ")[:len(common)] == common]
        if all(u in set(paths) for u in under):
            return clean_title(common[-1])
    first, last = clean_title(parts[0][-1]), clean_title(parts[-1][-1])
    return first if first == last else f"{first} \u2013 {last}"


def _chapter(path: str) -> str:
    """Top-level chapter of a heading path (element 0 is the document title when there is more than one)."""
    parts = path.split(" > ")
    return parts[1] if len(parts) > 1 else parts[0]


def merge_tiny(mods: list[PlannedModule], min_claims: int, max_claims: int,
               universe: list[str] | None = None) -> list[PlannedModule]:
    """Merge a module with fewer than min_claims into an ADJACENT one from the same chapter when the result still
    fits max_claims. Split parts are never merged; claims keep their order."""
    mods = list(mods)
    changed = True
    while changed:
        changed = False
        for i, m in enumerate(mods):
            if m.part or len(m.claim_ids) >= min_claims:
                continue
            chap = {_chapter(p) for p in m.paths}
            best = None
            for j in (i - 1, i + 1):
                if 0 <= j < len(mods):
                    n = mods[j]
                    if (not n.part and {_chapter(p) for p in n.paths} == chap
                            and len(m.claim_ids) + len(n.claim_ids) <= max_claims):
                        if best is None or len(n.claim_ids) < len(mods[best].claim_ids):
                            best = j
            if best is None:
                continue
            a, b = sorted((i, best))
            x, y = mods[a], mods[b]
            paths = x.paths + y.paths
            mods[a:b + 1] = [PlannedModule(module_title(paths, universe), x.section_ids + y.section_ids,
                                           x.claim_ids + y.claim_ids, x.words + y.words, paths)]
            changed = True
            break
    return mods


def plan_modules(sections: list[dict], claims: list[dict], target_seconds: int = 90, max_claims: int = 12,
                 min_claims: int = 5, merge_slack: int = 3) -> list[PlannedModule]:
    """sections: {id, heading_path, text}; claims: {id, section_id}.
    Groups adjacent sections within a word/claim budget (12 claims ~ a 90 s video), splits oversized groups
    into balanced parts, then merges tiny modules into a neighbour from the same chapter."""
    by_sec: dict[int, list[int]] = {}
    for c in claims:
        by_sec.setdefault(c["section_id"], []).append(c["id"])
    budget_words = int(target_seconds / 60 * WPM * 1.6)
    universe = [s["heading_path"] for s in sections]
    groups: list[PlannedModule] = []
    cur: PlannedModule | None = None
    for s in sections:
        cids, w = by_sec.get(s["id"], []), len(s["text"].split())
        if not cids:
            continue
        if cur and (cur.words + w > budget_words or len(cur.claim_ids) + len(cids) > max_claims
                    or _chapter(s["heading_path"]) != _chapter(cur.paths[0])):       # never span two chapters
            groups.append(cur)
            cur = None
        if cur is None:
            cur = PlannedModule("", [], [])
        cur.paths.append(s["heading_path"])
        cur.title = module_title(cur.paths, universe)
        cur.section_ids.append(s["id"])
        cur.claim_ids += cids
        cur.words += w
    if cur:
        groups.append(cur)
    out: list[PlannedModule] = []
    for m in groups:
        n = -(-len(m.claim_ids) // max_claims)
        size = -(-len(m.claim_ids) // n)                      # balanced: 13 claims -> 7 + 6, not 12 + 1
        for i in range(n):
            part = m.claim_ids[i * size:(i + 1) * size]
            out.append(PlannedModule(m.title if n == 1 else f"{m.title} ({i + 1}/{n})", m.section_ids, part,
                                     m.words // n, list(m.paths), part=n > 1))
    out = merge_tiny(out, min_claims, max_claims + merge_slack, universe)   # absorbing a tiny module may overshoot a little
    totals: dict[str, int] = {}
    for m in out:                                  # two modules must never share a title: number them "(i/k)"
        if not m.part:
            totals[m.title] = totals.get(m.title, 0) + 1
    index: dict[str, int] = {}
    for m in out:
        if not m.part and totals[m.title] > 1:
            index[m.title] = index.get(m.title, 0) + 1
            m.title = f"{m.title} ({index[m.title]}/{totals[m.title]})"
    return out


def coverage(plan: list[PlannedModule], claims: list[dict], excluded: set[int] = frozenset()) -> CoverageReport:
    assigned = {c for m in plan for c in m.claim_ids} | set(excluded)
    miss = [c["id"] for c in claims if c["id"] not in assigned and c.get("modality") in ("must", "must_not")]
    return CoverageReport(len(claims), len([c for c in claims if c["id"] in assigned]), miss)
