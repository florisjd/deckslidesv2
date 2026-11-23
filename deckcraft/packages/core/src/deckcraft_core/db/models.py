from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import ForeignKey, Integer, String, Text, Float, DateTime, JSON, event, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _now(): return datetime.now(timezone.utc)


class Base(DeclarativeBase): pass


class Project(Base):
    __tablename__ = "project"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(8), default="en")
    locale: Mapped[str] = mapped_column(String(8), default="AU")  # AU/UK/US spelling
    orientation: Mapped[str] = mapped_column(String(10), default="portrait")
    org_name: Mapped[str] = mapped_column(String(200), default="")
    series_name: Mapped[str] = mapped_column(String(200), default="")
    outro_text: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Source(Base):
    __tablename__ = "source"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    filename: Mapped[str] = mapped_column(String(300))
    blob_sha: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(100))
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sections: Mapped[list["Section"]] = relationship(cascade="all, delete-orphan")


class Section(Base):
    __tablename__ = "section"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"))
    position: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[str] = mapped_column(String(500))
    level: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer, default=1)
    page_end: Mapped[int] = mapped_column(Integer, default=1)


class Claim(Base):
    __tablename__ = "claim"
    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("section.id"))
    kind: Mapped[str] = mapped_column(String(20))
    modality: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    page: Mapped[int] = mapped_column(Integer, default=1)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    risk: Mapped[float] = mapped_column(Float, default=0.0)
    lead: Mapped[str] = mapped_column(Text, default="")
    item: Mapped[str] = mapped_column(Text, default="")


class Module(Base):
    __tablename__ = "module"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    target_seconds: Mapped[int] = mapped_column(Integer, default=90)
    language: Mapped[str] = mapped_column(String(8), default="en")
    variant_of: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ModuleClaim(Base):
    __tablename__ = "module_claim"
    module_id: Mapped[int] = mapped_column(ForeignKey("module.id"), primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), default="primary")  # primary/context/excluded
    reason: Mapped[str] = mapped_column(String(300), default="")


class SceneRow(Base):
    __tablename__ = "scene"
    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("module.id"))
    position: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    beats: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft/approved
    origin: Mapped[str] = mapped_column(String(8), default="rule")    # rule | llm
    draft_text: Mapped[str] = mapped_column(Text, default="")        # original AI draft narration
    rev: Mapped[int] = mapped_column(Integer, default=1)


class SourceVersion(Base):
    __tablename__ = "source_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"))
    version: Mapped[int] = mapped_column(Integer)
    blob_sha: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class Job(Base):
    __tablename__ = "job"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(12), default="queued")  # queued/running/done/failed/cancelled
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    actor: Mapped[str] = mapped_column(String(40), default="user")
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


def make_session_factory(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA foreign_keys=ON"); c.close()

    from .. import memory  # noqa: F401  (registers style tables)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
