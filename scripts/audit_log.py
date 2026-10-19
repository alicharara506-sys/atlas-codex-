#!/usr/bin/env python3
"""Local SQLite/JSON structured audit logging for ATLAS v2 signals."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, database_path: str | Path):
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._logger = logging.getLogger("atlas.audit")
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    signal_hash TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    confluence_score REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_signal_audit_created_at ON signal_audit(created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_signal_audit_instrument ON signal_audit(instrument)")
            connection.commit()

    def write_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(signal, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        metadata = signal.get("metadata", {})
        row = signal.get("signal", {})
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                "INSERT INTO signal_audit(created_at, signal_hash, instrument, timeframe, direction, confluence_score, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    created_at,
                    digest,
                    str(metadata.get("instrument", "UNKNOWN")),
                    str(metadata.get("timeframe", "UNKNOWN")),
                    str(row.get("direction", "NO_TRADE")),
                    float(row.get("confluence_score", 0)),
                    encoded,
                ),
            )
            audit_id = cursor.lastrowid
            connection.commit()
        self._logger.info(json.dumps({"event": "signal_generated", "auditId": audit_id, "hash": digest, "instrument": metadata.get("instrument"), "direction": row.get("direction"), "llmLatencyMs": signal.get("llm_attribution", {}).get("total_latency_ms", 0)}))
        return {"auditId": audit_id, "sha256": digest, "database": str(self.path)}

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = min(500, max(1, int(limit)))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, created_at, signal_hash, payload_json FROM signal_audit ORDER BY id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return [{"auditId": row[0], "createdAt": row[1], "sha256": row[2], "signal": json.loads(row[3])} for row in rows]


__all__ = ["AuditLogger"]
