"""Static JSON, JSONL, and HTML report export for Phase 3 research runs."""

from __future__ import annotations

import html
import json
from pathlib import Path

from app.backtest.contracts import BacktestReport


class BacktestReportWriter:
    def write(self, report: BacktestReport, output_directory: str | Path) -> dict[str, Path]:
        output = Path(output_directory).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        summary = output / "summary.json"
        trades = output / "trades.jsonl"
        equity = output / "equity.jsonl"
        dashboard = output / "report.html"
        summary.write_text(
            json.dumps(report.model_dump(mode="json", exclude={"trades", "equity", "audit"}), indent=2),
            encoding="utf-8",
        )
        trades.write_text(
            "".join(json.dumps(row.model_dump(mode="json"), separators=(",", ":")) + "\n" for row in report.trades),
            encoding="utf-8",
        )
        equity.write_text(
            "".join(json.dumps(row.model_dump(mode="json"), separators=(",", ":")) + "\n" for row in report.equity),
            encoding="utf-8",
        )
        metrics = report.metrics
        rows = {
            "Total return": metrics.total_return,
            "Sharpe": metrics.sharpe,
            "Sortino": metrics.sortino,
            "Max drawdown": metrics.max_drawdown,
            "Profit factor": metrics.profit_factor,
            "Win rate": metrics.win_rate,
            "Benchmark return": metrics.benchmark_return,
            "Excess return": metrics.excess_return,
            "Trade count": metrics.trade_count,
        }
        table = "".join(
            f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(value))}</td></tr>" for label, value in rows.items()
        )
        dashboard.write_text(
            "<!doctype html><html><head><meta charset='utf-8'><title>Atlas Backtest</title>"
            "<style>body{font-family:system-ui;background:#07111f;color:#e5edf7;padding:2rem}"
            "table{border-collapse:collapse}th,td{padding:.6rem 1rem;border:1px solid #334155;text-align:left}"
            "strong{color:#f59e0b}</style></head><body>"
            f"<h1>{html.escape(report.mandate_name)} {html.escape(report.mandate_version)}</h1>"
            "<p><strong>Research only. Execution disabled.</strong></p>"
            f"<table>{table}</table></body></html>",
            encoding="utf-8",
        )
        return {"summary": summary, "trades": trades, "equity": equity, "html": dashboard}


__all__ = ["BacktestReportWriter"]
