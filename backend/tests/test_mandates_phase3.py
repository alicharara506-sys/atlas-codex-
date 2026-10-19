from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


MANDATE_YAML = """
mandate:
  name: Atlas Macro Tilt
  version: 1.0.0
  universe:
    asset_class: commodity
    symbols: [XAUUSD, XAGUSD]
  desks:
    enabled: [technical, macro, pattern]
    weights:
      technical: 0.55
      macro: 0.25
      pattern: 0.15
    llm_enabled: [technical, macro]
  risk:
    max_position_pct: 0.30
    max_leverage: 1.0
    daily_drawdown_limit: 0.05
    weekly_drawdown_limit: 0.10
    correlation_threshold: 0.85
  execution:
    mode: paper
    rebalance_cadence: 1d
    exchange: research_only
  backtest:
    warmup_bars: 20
    eval_bars: 10
    benchmark: buy_and_hold
"""


def make_series(symbol: str, *, shock_from: int | None = None) -> object:
    from app.backtest.contracts import BacktestBar, BacktestSeries

    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[BacktestBar] = []
    for index in range(35):
        base = 100.0 + index * 0.4 + (2.0 if symbol == "XAGUSD" else 0.0)
        if shock_from is not None and index >= shock_from:
            base += 50.0 + index
        change = 0.8 if index % 3 else -0.6
        opening = base
        closing = base + change
        bars.append(
            BacktestBar(
                timestamp=start + timedelta(hours=index),
                open=opening,
                high=max(opening, closing) + 0.5,
                low=min(opening, closing) - 0.5,
                close=closing,
                volume=1_000.0 + index,
            )
        )
    return BacktestSeries(symbol=symbol, bars=bars)


class MandatePhase3Tests(unittest.TestCase):
    def test_mandate_validation_registration_and_promotion_remain_fail_closed(self) -> None:
        from app.core.store import MemoryRecordStore
        from app.mandates import MandateService, MandateValidationError, parse_mandate_yaml

        document = parse_mandate_yaml(MANDATE_YAML)
        self.assertEqual(document.mandate.version, "1.0.0")
        self.assertEqual(document.mandate.execution.mode, "paper")

        store = MemoryRecordStore()
        service = MandateService(store)
        first_key, _, first_status = service.validate_and_register(MANDATE_YAML)
        second_key, _, second_status = service.validate_and_register(MANDATE_YAML)
        self.assertEqual(first_key, second_key)
        self.assertEqual(first_status, second_status)
        self.assertFalse(first_status.paper_eligible)
        self.assertFalse(first_status.live_eligible)
        self.assertFalse(first_status.execution_enabled)
        self.assertEqual(len(store.values("phase3_mandates")), 1)

        for invalid in (
            "",
            "- not-an-object",
            MANDATE_YAML.replace("version: 1.0.0", "version: latest"),
            MANDATE_YAML.replace("symbols: [XAUUSD, XAGUSD]", "symbols: [XAUUSD, XAUUSD]"),
            MANDATE_YAML.replace("pattern", "unknown_desk"),
        ):
            with self.assertRaises(MandateValidationError):
                parse_mandate_yaml(invalid)

    def test_backtest_is_deterministic_next_open_and_no_lookahead(self) -> None:
        from app.backtest import BacktestReportWriter, run_backtest
        from app.mandates import parse_mandate_yaml

        document = parse_mandate_yaml(MANDATE_YAML)
        baseline_series = [make_series("XAUUSD"), make_series("XAGUSD")]
        baseline = run_backtest(document, baseline_series)  # type: ignore[arg-type]
        repeated = run_backtest(document, baseline_series)  # type: ignore[arg-type]
        shocked = run_backtest(
            document,
            [make_series("XAUUSD", shock_from=25), make_series("XAGUSD", shock_from=25)],  # type: ignore[list-item]
        )

        self.assertEqual(baseline, repeated)
        self.assertEqual(len(baseline.equity), 10)
        self.assertEqual(len(baseline.audit), 10)
        self.assertTrue(baseline.no_lookahead)
        self.assertTrue(baseline.research_only)
        self.assertFalse(baseline.execution_enabled)
        self.assertTrue(all(trade.entry_index == trade.signal_index + 1 for trade in baseline.trades))
        before_shock = [
            (row.symbol, row.side, row.signal_index, row.entry_index)
            for row in baseline.trades
            if row.entry_index <= 25
        ]
        shocked_before = [
            (row.symbol, row.side, row.signal_index, row.entry_index)
            for row in shocked.trades
            if row.entry_index <= 25
        ]
        self.assertEqual(before_shock, shocked_before)

        with tempfile.TemporaryDirectory() as directory:
            paths = BacktestReportWriter().write(baseline, directory)
            self.assertEqual(set(paths), {"summary", "trades", "equity", "html"})
            self.assertTrue(all(path.is_file() for path in paths.values()))
            self.assertIn("Execution disabled", paths["html"].read_text(encoding="utf-8"))

    def test_api_validates_and_backtests_idempotently(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database, SqlModelRecordStore

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "REDIS_URL": "redis://127.0.0.1:6379/0"}):
            settings = AtlasSettings.from_env()
            database = Database(settings.database_url)
            self.addCleanup(database.engine.dispose)
            application = create_app(settings, database, ReadyProbe())
            database.create_schema()

        series = [make_series("XAUUSD"), make_series("XAGUSD")]
        payload = {
            "yaml_text": MANDATE_YAML,
            "series": [row.model_dump(mode="json") for row in series],  # type: ignore[attr-defined]
        }
        with TestClient(application) as client:
            validation = client.post("/mandates/validate", json={"yaml_text": MANDATE_YAML})
            first = client.post("/backtests/run", json=payload)
            second = client.post("/backtests/run", json=payload)
            history = client.get("/backtests")

        self.assertEqual(validation.status_code, 200)
        self.assertEqual(validation.json()["phase"], 3)
        self.assertFalse(validation.json()["execution_enabled"])
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertFalse(first.json()["report"]["execution_enabled"])
        store = SqlModelRecordStore(database)
        self.assertEqual(len(store.values("phase3_mandates")), 1)
        self.assertEqual(len(store.values("phase3_backtest_runs")), 1)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["count"], 1)
        self.assertEqual(history.json()["runs"][0]["run_key"], first.json()["run_key"])
        self.assertTrue(history.json()["runs"][0]["research_only"])
        self.assertFalse(history.json()["execution_enabled"])


if __name__ == "__main__":
    unittest.main()
