"""Schema-first SQLModel definitions for Atlas's core audit database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class Fund(SQLModel, table=True):
    __tablename__ = "funds"
    id: int | None = Field(default=None, primary_key=True)
    cik: str = Field(index=True, unique=True, max_length=10)
    name: str = Field(index=True, max_length=255)
    manager: str | None = Field(default=None, max_length=255)
    denomination: str = Field(default="USD", max_length=12)
    related_ciks: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    url: str | None = Field(default=None, max_length=1_024)


class Holding(SQLModel, table=True):
    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("fund_id", "accession", "cusip", "share_class", name="uq_holding_filing_security"),)
    id: int | None = Field(default=None, primary_key=True)
    fund_id: int = Field(foreign_key="funds.id", index=True)
    ticker: str | None = Field(default=None, index=True, max_length=32)
    cusip: str = Field(index=True, max_length=16)
    issuer: str = Field(max_length=255)
    share_class: str = Field(default="", max_length=64)
    shares: float = 0
    value: float = 0
    portfolio_pct: float | None = None
    quarter: str = Field(index=True, max_length=8)
    filing_type: str = Field(index=True, max_length=16)
    accession: str = Field(index=True, max_length=32)


class Asset(SQLModel, table=True):
    __tablename__ = "assets"
    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True, unique=True, max_length=32)
    name: str = Field(max_length=255)
    sector: str | None = Field(default=None, index=True, max_length=128)
    industry: str | None = Field(default=None, max_length=128)
    asset_class: str = Field(index=True, max_length=32)
    logo_url: str | None = Field(default=None, max_length=1_024)


class PriceBar(SQLModel, table=True):
    __tablename__ = "price_bars"
    __table_args__ = {"postgresql_partition_by": "RANGE (timestamp)"}
    ticker: str = Field(primary_key=True, index=True, max_length=32)
    timestamp: datetime = Field(primary_key=True, index=True)
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = Field(primary_key=True, index=True, max_length=32)


class SentimentSnapshot(SQLModel, table=True):
    __tablename__ = "sentiment_snapshots"
    __table_args__ = (UniqueConstraint("ticker", "source", "timestamp", "source_key", name="uq_sentiment_source_event"),)
    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True, max_length=32)
    source: str = Field(index=True, max_length=64)
    source_key: str = Field(max_length=128)
    score: float
    timestamp: datetime = Field(index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class Mandate(SQLModel, table=True):
    __tablename__ = "mandates"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_mandate_version"),)
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=255)
    config_yaml: str = Field(sa_column=Column(Text, nullable=False))
    version: str = Field(max_length=32)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class Run(SQLModel, table=True):
    __tablename__ = "runs"
    id: int | None = Field(default=None, primary_key=True)
    mandate_id: int = Field(foreign_key="mandates.id", index=True)
    mode: str = Field(index=True, max_length=16)
    status: str = Field(index=True, max_length=32)
    metrics_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class Decision(SQLModel, table=True):
    __tablename__ = "decisions"
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    timestamp: datetime = Field(default_factory=utc_now, index=True)
    agent: str = Field(index=True, max_length=64)
    reasoning_summary: str = Field(sa_column=Column(Text, nullable=False))
    signal: str = Field(index=True, max_length=16)
    confidence: float
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class IngestionRecord(SQLModel, table=True):
    """Durable idempotency envelope for normalized ingestion records."""

    __tablename__ = "ingestion_records"
    __table_args__ = (UniqueConstraint("collection", "natural_key", name="uq_ingestion_record_natural_key"),)
    id: int | None = Field(default=None, primary_key=True)
    collection: str = Field(index=True, max_length=64)
    natural_key: str = Field(index=True, max_length=64)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


__all__ = [
    "Asset", "Decision", "Fund", "Holding", "IngestionRecord", "Mandate", "PriceBar", "Run",
    "SentimentSnapshot",
]
