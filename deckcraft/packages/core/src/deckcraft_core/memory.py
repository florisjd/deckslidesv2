"""Style memory: capture (AI draft, human final) pairs, a house style guide, retrieved few-shots,
and edit_ratio as the headline quality metric. No fine-tuning; no embeddings needed yet."""
from __future__ import annotations
import difflib
import re

from sqlalchemy import Float, Integer, JSON, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .db.models import Base, _now


class StyleExample(Base):
    __tablename__ = "style_example"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scene_kind: Mapped[str] = mapped_column(String(20))
    draft: Mapped[str] = mapped_column(Text)
    final: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(10), default="ai")     # ai | rule | human
    edit_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    approved: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[object] = mapped_column(DateTime, default=_now)


class StyleGuide(Base):
    __tablename__ = "style_guide"
    project_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")


def edit_ratio(draft: str, final: str) -> float:
    """Share of words changed between draft and final (0 = identical, 1 = fully rewritten)."""
    a, b = draft.split(), final.split()
    if not a and not b:
        return 0.0
    return round(1 - difflib.SequenceMatcher(None, a, b).ratio(), 4)


def record_edit(s, project_id: int, kind: str, draft: str, final: str, origin: str = "ai") -> None:
    if draft.strip() == final.strip():
        return
    s.add(StyleExample(project_id=project_id, scene_kind=kind, draft=draft, final=final, origin=origin,
                       edit_ratio=edit_ratio(draft, final)))


def mark_approved(s, project_id: int, kind: str, final: str) -> None:
    """An approved final is a trusted example even if the human changed nothing."""
    ex = (s.query(StyleExample).filter(StyleExample.project_id == project_id, StyleExample.scene_kind == kind,
                                       StyleExample.final == final).first())
    if ex:
        ex.approved = 1
    else:
        s.add(StyleExample(project_id=project_id, scene_kind=kind, draft=final, final=final, origin="human",
                           edit_ratio=0.0, approved=1))


def get_style_guide(s, project_id: int) -> str:
    g = s.get(StyleGuide, project_id)
    return g.text if g else ""


def set_style_guide(s, project_id: int, text: str) -> None:
    g = s.get(StyleGuide, project_id)
    if g:
        g.text = text
    else:
        s.add(StyleGuide(project_id=project_id, text=text))


def _toks(t: str) -> set[str]:
    return set(re.findall(r"[a-z']{3,}", t.lower()))


def few_shots(s, project_id: int, kind: str, query: str, k: int = 2) -> list[tuple[str, str]]:
    """Approved or edited finals of the same scene kind, ranked by word overlap with the query."""
    rows = (s.query(StyleExample).filter(StyleExample.project_id == project_id, StyleExample.scene_kind == kind,
                                         (StyleExample.approved == 1) | (StyleExample.edit_ratio > 0)).all())
    q = _toks(query)
    rows.sort(key=lambda r: (len(q & _toks(r.final)) / (len(q | _toks(r.final)) or 1), r.approved), reverse=True)
    return [(r.draft, r.final) for r in rows[:k]]


def metrics(s, project_id: int) -> dict:
    rows = s.query(StyleExample).filter(StyleExample.project_id == project_id).all()
    ai = [r for r in rows if r.origin in ("ai", "rule")]
    return {"examples": len(rows), "approved": sum(r.approved for r in rows),
            "mean_edit_ratio": round(sum(r.edit_ratio for r in ai) / len(ai), 3) if ai else None}


def render_prompt_block(style_guide: str, shots: list[tuple[str, str]]) -> str:
    parts = []
    if style_guide.strip():
        parts.append("House style (follow strictly):\n" + style_guide.strip())
    if shots:
        parts.append("Examples of narration the reviewer preferred (match the tone, not the facts):\n" +
                     "\n".join(f"- {f}" for _, f in shots))
    return "\n\n".join(parts)
