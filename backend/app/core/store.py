"""Idempotent persistence contracts used before SQLModel activation in Phase 1.3."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Protocol


def stable_key(*parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class UpsertResult:
    inserted: bool
    key: str


class RecordStore(Protocol):
    def upsert(self, collection: str, key: str, payload: dict[str, Any]) -> UpsertResult: ...
    def values(self, collection: str) -> list[dict[str, Any]]: ...
    def recent_values(self, collection: str, limit: int) -> list[dict[str, Any]]: ...
    def latest_values(
        self,
        collection: str,
        group_field: str,
        time_field: str,
    ) -> list[dict[str, Any]]: ...


class MemoryRecordStore:
    """Thread-safe natural-key store used by deterministic tests and local demos."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = RLock()

    def upsert(self, collection: str, key: str, payload: dict[str, Any]) -> UpsertResult:
        with self._lock:
            bucket = self._rows.setdefault(collection, {})
            inserted = key not in bucket
            bucket[key] = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
            return UpsertResult(inserted=inserted, key=key)

    def values(self, collection: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for _, row in sorted(self._rows.get(collection, {}).items())]

    def recent_values(self, collection: str, limit: int) -> list[dict[str, Any]]:
        """Return the most recently inserted/upserted bounded records."""

        if limit < 1:
            return []
        with self._lock:
            rows = list(self._rows.get(collection, {}).values())
            return [dict(row) for row in reversed(rows[-limit:])]

    def latest_values(
        self,
        collection: str,
        group_field: str,
        time_field: str,
    ) -> list[dict[str, Any]]:
        """Return only the latest record for each logical group."""

        latest: dict[str, dict[str, Any]] = {}
        with self._lock:
            for payload in self._rows.get(collection, {}).values():
                group = str(payload.get(group_field, ""))
                current = latest.get(group)
                if current is None or str(payload.get(time_field, "")) > str(current.get(time_field, "")):
                    latest[group] = payload
        return [dict(payload) for _, payload in sorted(latest.items())]


class JsonlRecordStore(MemoryRecordStore):
    """Append-only local persistence with idempotent replay semantics."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path).expanduser().resolve()
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                envelope = json.loads(line)
                super().upsert(envelope["collection"], envelope["key"], envelope["payload"])

    def upsert(self, collection: str, key: str, payload: dict[str, Any]) -> UpsertResult:
        with self._lock:
            result = super().upsert(collection, key, payload)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            envelope = {"collection": collection, "key": key, "payload": payload}
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
            return result


__all__ = ["JsonlRecordStore", "MemoryRecordStore", "RecordStore", "UpsertResult", "stable_key"]
