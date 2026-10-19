"""Adopt the existing Atlas schema without deleting persisted evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
from sqlalchemy import text
from sqlmodel import SQLModel

from app.models import tables as atlas_tables  # noqa: F401
from app.models.partitioning import postgres_partition_ddl, price_partition_spec

revision = "20260820_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create missing tables and adopt existing ones via check-first semantics."""

    bind = op.get_bind()
    SQLModel.metadata.create_all(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        now = datetime.now(UTC)
        for offset in range(-1, 3):
            bind.execute(text(postgres_partition_ddl(price_partition_spec(now, offset))))


def downgrade() -> None:
    """Intentionally preserve all trading research and audit data."""

    # The baseline is an adoption boundary. Destructive rollback is forbidden;
    # recovery uses a verified database backup and a forward migration.
    raise RuntimeError("ATLAS baseline downgrade is intentionally disabled; restore a verified backup instead")
