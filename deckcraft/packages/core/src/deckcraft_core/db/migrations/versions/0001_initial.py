"""Initial schema (12 tables matching pre-lead/image DeckCraft core).

Revision ID: 0001_initial
Revises:
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("orientation", sa.String(length=10), nullable=False),
        sa.Column("org_name", sa.String(length=200), nullable=False),
        sa.Column("series_name", sa.String(length=200), nullable=False),
        sa.Column("outro_text", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("blob_sha", sa.String(length=64), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "section",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("source.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.String(length=500), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
    )
    op.create_table(
        "claim",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("section_id", sa.Integer(), sa.ForeignKey("section.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("modality", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("risk", sa.Float(), nullable=False),
    )
    op.create_table(
        "module",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("target_seconds", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("variant_of", sa.Integer(), nullable=True),
    )
    op.create_table(
        "module_claim",
        sa.Column("module_id", sa.Integer(), sa.ForeignKey("module.id"), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("claim.id"), primary_key=True),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
    )
    op.create_table(
        "scene",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("module_id", sa.Integer(), sa.ForeignKey("module.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("beats", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
    )
    op.create_table(
        "source_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("source.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("blob_sha", sa.String(length=64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
    )
    op.create_table(
        "job",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("actor", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("entity", sa.String(length=80), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_table(
        "style_example",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("scene_kind", sa.String(length=20), nullable=False),
        sa.Column("draft", sa.Text(), nullable=False),
        sa.Column("final", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("edit_ratio", sa.Float(), nullable=False),
        sa.Column("approved", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "style_guide",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    for tbl in (
        "style_guide", "style_example", "audit_log", "job", "source_version",
        "scene", "module_claim", "module", "claim", "section", "source", "project",
    ):
        op.drop_table(tbl)
