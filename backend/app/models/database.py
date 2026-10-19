"""SQLModel engine lifecycle and an idempotent PostgreSQL-backed record store."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.core.config import AtlasSettings
from app.core.store import RecordStore, UpsertResult
from app.models.partitioning import postgres_partition_ddl, price_partition_spec
from app.models.tables import IngestionRecord


def _json_copy(payload: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(json.dumps(payload, ensure_ascii=False, default=str)))


class Database:
    """Own the SQLAlchemy engine without connecting during module import."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        kwargs: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in {"sqlite://", "sqlite:///:memory:"}:
                kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **kwargs)

    def create_schema(self, *, partition_months_before: int = 1, partition_months_ahead: int = 2) -> None:
        SQLModel.metadata.create_all(self.engine)
        if self.engine.dialect.name != "postgresql":
            return
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            for offset in range(-partition_months_before, partition_months_ahead + 1):
                connection.execute(text(postgres_partition_ddl(price_partition_spec(now, offset))))

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            yield session

    def healthy(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False


class SqlModelRecordStore(RecordStore):
    """Persist normalized pipeline envelopes using a database-enforced natural key."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert(self, collection: str, key: str, payload: dict[str, Any]) -> UpsertResult:
        normalized = _json_copy(payload)
        with self.database.session() as session:
            existing = session.exec(
                select(IngestionRecord).where(
                    IngestionRecord.collection == collection,
                    IngestionRecord.natural_key == key,
                )
            ).one_or_none()
            if existing is not None:
                existing.payload_json = normalized
                existing.updated_at = datetime.now(UTC)
                session.add(existing)
                session.commit()
                return UpsertResult(inserted=False, key=key)

            session.add(IngestionRecord(collection=collection, natural_key=key, payload_json=normalized))
            try:
                session.commit()
                return UpsertResult(inserted=True, key=key)
            except IntegrityError:
                # A concurrent writer won the unique-key race. The durable result
                # is still idempotent, and callers must count it as a duplicate.
                session.rollback()
                return UpsertResult(inserted=False, key=key)

    def values(self, collection: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.exec(
                select(IngestionRecord)
                .where(IngestionRecord.collection == collection)
                .order_by(IngestionRecord.natural_key)
            ).all()
            return [_json_copy(row.payload_json) for row in rows]

    def recent_values(self, collection: str, limit: int) -> list[dict[str, Any]]:
        """Load a bounded newest-first slice without hydrating the full collection."""

        if limit < 1:
            return []
        with self.database.session() as session:
            rows = session.exec(
                select(IngestionRecord)
                .where(IngestionRecord.collection == collection)
                .order_by(col(IngestionRecord.updated_at).desc())
                .limit(limit)
            ).all()
            return [_json_copy(row.payload_json) for row in rows]

    def latest_values(
        self,
        collection: str,
        group_field: str,
        time_field: str,
    ) -> list[dict[str, Any]]:
        """Load only the newest JSON record per group on PostgreSQL."""

        if self.database.engine.dialect.name != "postgresql":
            payload_rows = self.values(collection)
            latest: dict[str, dict[str, Any]] = {}
            for payload in payload_rows:
                group = str(payload.get(group_field, ""))
                current = latest.get(group)
                if current is None or str(payload.get(time_field, "")) > str(current.get(time_field, "")):
                    latest[group] = payload
            return [payload for _, payload in sorted(latest.items())]

        group_expression = IngestionRecord.payload_json[group_field].as_string()
        time_expression = IngestionRecord.payload_json[time_field].as_string()
        with self.database.session() as session:
            record_rows = session.exec(
                select(IngestionRecord)
                .where(IngestionRecord.collection == collection)
                .distinct(group_expression)
                .order_by(
                    group_expression,
                    time_expression.desc(),
                    col(IngestionRecord.updated_at).desc(),
                )
            ).all()
            return [_json_copy(row.payload_json) for row in record_rows]


def database_from_settings(settings: AtlasSettings | None = None) -> Database:
    resolved = settings or AtlasSettings.from_env()
    return Database(resolved.database_url)


__all__ = ["Database", "SqlModelRecordStore", "database_from_settings"]
