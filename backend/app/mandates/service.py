"""Idempotent mandate registration and lifecycle audit service."""

from __future__ import annotations

from typing import Any

from app.backtest.contracts import BacktestReport
from app.core.store import RecordStore, stable_key
from app.mandates.contracts import MandateDocument, PromotionStatus, parse_mandate_yaml, promotion_status


class MandateService:
    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def validate_and_register(self, yaml_text: str) -> tuple[str, MandateDocument, PromotionStatus]:
        document = parse_mandate_yaml(yaml_text)
        mandate = document.mandate
        mandate_key = stable_key(mandate.name, mandate.version)
        status = promotion_status(backtest_complete=False)
        self.store.upsert(
            "phase3_mandates",
            mandate_key,
            {
                "mandate_key": mandate_key,
                "document": document.model_dump(mode="json"),
                "lifecycle": status.model_dump(mode="json"),
            },
        )
        return mandate_key, document, status

    def record_backtest(
        self,
        mandate_key: str,
        document: MandateDocument,
        report: BacktestReport,
    ) -> tuple[str, PromotionStatus]:
        status = promotion_status(backtest_complete=True)
        run_key = stable_key(mandate_key, report.model_dump(mode="json"))
        self.store.upsert(
            "phase3_backtest_runs",
            run_key,
            {
                "run_key": run_key,
                "mandate_key": mandate_key,
                "mandate": document.mandate.model_dump(mode="json"),
                "report": report.model_dump(mode="json"),
                "lifecycle": status.model_dump(mode="json"),
            },
        )
        return run_key, status

    def list_backtests(self, *, limit: int = 50) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for row in self.store.values("phase3_backtest_runs"):
            report = row.get("report", {})
            equity = report.get("equity", [])
            summaries.append(
                {
                    "run_key": row["run_key"],
                    "mandate_key": row["mandate_key"],
                    "mandate_name": report.get("mandate_name", "Unknown mandate"),
                    "mandate_version": report.get("mandate_version", "0.0.0"),
                    "symbols": report.get("symbols", []),
                    "metrics": report.get("metrics", {}),
                    "lifecycle": row.get("lifecycle", {}),
                    "evaluated_at": equity[-1].get("timestamp") if equity else None,
                    "research_only": True,
                    "execution_enabled": False,
                }
            )
        summaries.sort(
            key=lambda item: (
                -float(item.get("metrics", {}).get("sharpe", 0.0)),
                str(item.get("mandate_name", "")),
                str(item.get("run_key", "")),
            )
        )
        return summaries[:limit]


__all__ = ["MandateService"]
