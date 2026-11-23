"""Add image_sha and notes columns to section.

Revision ID: 0003_section_image_notes
Revises: 0002_claim_lead_item
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0003_section_image_notes"
down_revision = "0002_claim_lead_item"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("section")}
    if "image_sha" not in cols:
        op.add_column("section", sa.Column("image_sha", sa.String(length=64), nullable=False, server_default=""))
    if "notes" not in cols:
        op.add_column("section", sa.Column("notes", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("section") as batch:
        batch.drop_column("notes")
        batch.drop_column("image_sha")
