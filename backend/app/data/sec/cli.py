"""Daily SEC ingestion entry point used by the Phase 1 workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.core.config import AtlasSettings
from app.core.store import JsonlRecordStore
from app.data.sec.pipeline import (
    LLMSectorResolver,
    LLMTickerResolver,
    OpenFigiTickerResolver,
    SecEdgarClient,
    SecFilingPipeline,
    SectorResolver,
    SectorResolverChain,
    TickerResolver,
    TickerResolverChain,
    TradingViewTickerResolver,
    YahooSectorResolver,
    YahooTickerResolver,
)
from app.llm.client import LLMClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch configured SEC filings into an idempotent JSONL staging store")
    parser.add_argument("--output", default="outputs/sec-records.jsonl")
    parser.add_argument("--funds-json", default=os.environ.get("SEC_FUNDS_JSON", "[]"), help='JSON list: [{"cik":"...","name":"..."}]')
    args = parser.parse_args()
    settings = AtlasSettings.from_env()
    if not settings.sec_user_agent:
        parser.error("SEC_USER_AGENT is required")
    try:
        funds = json.loads(args.funds_json)
    except json.JSONDecodeError as exc:
        parser.error(f"Invalid --funds-json: {exc}")
    if not isinstance(funds, list) or not all(isinstance(row, dict) and row.get("cik") and row.get("name") for row in funds):
        parser.error("--funds-json must be a list of objects with cik and name")
    llm_provider = os.environ.get("SEC_LLM_PROVIDER", "").strip()
    llm_client = LLMClient(settings) if llm_provider else None
    ticker_rows: list[TickerResolver] = [
        YahooTickerResolver(),
        OpenFigiTickerResolver(os.environ.get("OPENFIGI_API_KEY")),
        TradingViewTickerResolver(),
    ]
    sector_rows: list[SectorResolver] = [YahooSectorResolver()]
    if llm_client and llm_provider:
        ticker_rows.append(LLMTickerResolver(llm_client, llm_provider))
        sector_rows.append(LLMSectorResolver(llm_client, llm_provider))
    pipeline = SecFilingPipeline(SecEdgarClient(settings.sec_user_agent), JsonlRecordStore(Path(args.output)), TickerResolverChain(ticker_rows), SectorResolverChain(sector_rows))
    summary = {str(row["cik"]): pipeline.ingest_fund(str(row["cik"]), str(row["name"])) for row in funds}
    print(json.dumps({"status": "complete", "funds": summary, "output": str(Path(args.output).resolve())}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
