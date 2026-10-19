"""Pure helpers for native PostgreSQL monthly price-bar partitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class PartitionSpec:
    name: str
    starts_at: datetime
    ends_at: datetime


def month_start(value: datetime) -> datetime:
    """Return the UTC first instant of the month containing ``value``."""

    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    normalized = normalized.astimezone(UTC)
    return normalized.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def add_months(value: datetime, months: int) -> datetime:
    """Add whole calendar months without relying on a third-party date library."""

    base = month_start(value)
    offset = base.year * 12 + base.month - 1 + months
    year, zero_based_month = divmod(offset, 12)
    if year < 1:
        raise ValueError("Partition year must be positive")
    return base.replace(year=year, month=zero_based_month + 1)


def price_partition_spec(value: datetime, offset_months: int = 0) -> PartitionSpec:
    starts_at = add_months(value, offset_months)
    ends_at = add_months(starts_at, 1)
    return PartitionSpec(f"price_bars_{starts_at:%Y_%m}", starts_at, ends_at)


def postgres_partition_ddl(spec: PartitionSpec) -> str:
    """Build safe DDL; the identifier is generated exclusively from a UTC date."""

    start = spec.starts_at.isoformat()
    end = spec.ends_at.isoformat()
    return (
        f"CREATE TABLE IF NOT EXISTS {spec.name} PARTITION OF price_bars "
        f"FOR VALUES FROM ('{start}') TO ('{end}')"
    )


__all__ = ["PartitionSpec", "add_months", "month_start", "postgres_partition_ddl", "price_partition_spec"]
