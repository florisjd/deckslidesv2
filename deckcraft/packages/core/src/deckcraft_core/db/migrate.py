"""Programmatic Alembic migration runner (handles fresh DBs and pre-Alembic legacy DBs)."""
from __future__ import annotations
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
import sqlalchemy as sa
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        if "alembic_version" not in sa.inspect(conn).get_table_names():
            return None
        return MigrationContext.configure(conn).get_current_revision()


def upgrade_db(engine: Engine, target: str = "head") -> str | None:
    cfg = alembic_config(str(engine.url))
    with engine.connect() as conn:
        is_sqlite = conn.dialect.name == "sqlite"
        if is_sqlite and not conn.in_transaction():
            conn.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            tables = set(sa.inspect(conn).get_table_names())
            if "alembic_version" not in tables and "project" in tables:
                claim_cols = {c["name"] for c in sa.inspect(conn).get_columns("claim")} if "claim" in tables else set()
                sec_cols = {c["name"] for c in sa.inspect(conn).get_columns("section")} if "section" in tables else set()
                if "image_sha" in sec_cols and "notes" in sec_cols:
                    rev = "0003_section_image_notes"
                elif "lead" in claim_cols and "item" in claim_cols:
                    rev = "0002_claim_lead_item"
                else:
                    rev = "0001_initial"
                cfg.attributes["connection"] = conn
                command.stamp(cfg, rev)
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, target)
            if conn.in_transaction():
                conn.commit()
        finally:
            if is_sqlite:
                if conn.in_transaction():
                    conn.rollback()
                conn.connection.execute("PRAGMA foreign_keys=ON")
    return current_revision(engine)


def downgrade_db(engine: Engine, target: str) -> str | None:
    cfg = alembic_config(str(engine.url))
    with engine.connect() as conn:
        is_sqlite = conn.dialect.name == "sqlite"
        if is_sqlite and not conn.in_transaction():
            conn.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, target)
            if conn.in_transaction():
                conn.commit()
        finally:
            if is_sqlite:
                if conn.in_transaction():
                    conn.rollback()
                conn.connection.execute("PRAGMA foreign_keys=ON")
    return current_revision(engine)
