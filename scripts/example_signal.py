#!/usr/bin/env python3
"""Run an offline deterministic ATLAS v2 signal example."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from atlas_core import ATLASQuantEngine


def demo_bars(count: int = 200) -> list[dict[str, float]]:
    rows = []
    price = 2_400.0
    for index in range(count):
        drift = 0.35 + ((index % 9) - 4) * 0.08
        open_price = price
        close = max(1.0, open_price + drift)
        rows.append({"time": 1_720_000_000 + index * 3_600, "open": open_price, "high": max(open_price, close) + 1.2, "low": min(open_price, close) - 1.0, "close": close, "volume": 1_000 + (index % 17) * 80})
        price = close
    return rows


async def main() -> None:
    engine = ATLASQuantEngine(Path(__file__).resolve().parents[1] / "config.yaml")
    signal = await engine.generate_signal_from_data(instrument="XAUUSD", timeframe="1H", bars=demo_bars(), run_llm=False, write_audit=False)
    print(json.dumps(signal, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
