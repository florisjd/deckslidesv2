"""Add lead and item columns to claim.

Revision ID: 0002_claim_lead_item
Revises: 0001_initial
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0002_claim_lead_item"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("claim")}
    if "lead" not in cols:
        op.add_column("claim", sa.Column("lead", sa.Text(), nullable=False, server_default=""))
    if "item" not in cols:
        op.add_column("claim", sa.Column("item", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("claim") as batch:
        batch.drop_column("item")
        batch.drop_column("lead")
