# Data Providers

Atlas includes a loopback-only MetaTrader 5 bridge, plus protected server adapters for Twelve Data market history, Trading Economics news/calendar data, and OANDA v20 specifications. Demo mode remains available without any connection.

## Configuration

Copy `.env.example` to `.env.local`, supply only the services you use, and restart Atlas:

```dotenv
TWELVE_DATA_API_KEY=
TWELVE_DATA_SYMBOL_MAP=
MT5_ENABLED=true
MT5_PYTHON_PATH=C:\path\to\mt5-venv\Scripts\python.exe
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_PORTABLE=false
MT5_BRIDGE_PORT=8765
MT5_BRIDGE_TOKEN=
MT5_SYMBOL_MAP=
TRADING_ECONOMICS_API_KEY=
OANDA_API_TOKEN=
OANDA_ACCOUNT_ID=
OANDA_ENVIRONMENT=practice
OANDA_SYMBOL_MAP=
```

The optional symbol maps are JSON objects keyed by Atlas symbols. For example, `{"EURUSD":"EURUSD.a"}`. MT5 also attempts to discover common broker suffixes automatically. Secrets are never returned by `/api/integrations/status`.

## MetaTrader 5 bridge

`scripts/mt5_bridge.py` binds only to `127.0.0.1`, connects through MetaQuotes' official Python package, and intentionally exposes no order-submission function. `scripts/serve.mjs` starts and stops it with the local Atlas server when `MT5_ENABLED=true`.

- Latest broker bid/ask supplies the displayed quote and freshness.
- Indicators and signals use closed candles only; position zero (the forming bar) is excluded.
- `symbol_info` supplies lot step, min/max volume, contract size, tick size/value, digits, and profit currency.
- Deal history can become calibration evidence only when a position contains `ATLAS:signal_id:score`; generic broker history is ignored because its original model score is unknowable.
- MT5 must be installed, open, and logged in. Broker symbol availability and retained chart history determine what Atlas can load.

## Endpoints and behavior

- `GET /api/market?provider=mt5&symbols=EURUSD&timeframe=1H&limit=240` combines an MT5 tick with normalized closed candles.
- `GET /api/market?provider=twelve-data&symbols=EURUSD&timeframe=1H&limit=240` normalizes Twelve Data OHLC.
- `GET /api/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD` retains the Trading Economics event ID, source, update time, values, and importance.
- `GET /api/news` retains provider timestamps, summaries, affected assets, and URLs. Sentiment stays neutral until it is explicitly analyzed.
- `GET /api/broker/instruments?provider=mt5&symbols=EURUSD` reads broker-specific MT5 contract rules. `provider=oanda` remains available.
- `GET /api/mt5/outcomes?from=YYYY-MM-DD&to=YYYY-MM-DD` returns validated Atlas-linked resolved outcomes.
- `GET /api/integrations/status` reports configuration without credentials.

Routes apply same-origin checks, input validation, bounded in-memory rate limits, upstream timeouts, and short-lived caches. A provider failure is shown as unavailable; Atlas never silently relabels demo content as live.

## Provider policy

Every adapter maps vendor objects into `app/lib/types.ts`. It validates required bars and numeric fields, orders candles chronologically, preserves source timestamps, and refuses unsupported symbols or timeframes. Instrument symbol overrides allow account-specific naming without contaminating the quantitative engines.

Official references: [MT5 Python integration](https://www.mql5.com/en/docs/python_metatrader5), [Twelve Data API documentation](https://twelvedata.com/docs/advanced), [Trading Economics calendar](https://docs.tradingeconomics.com/economic_calendar/snapshot/), [Trading Economics news](https://docs.tradingeconomics.com/news/latest/), and [OANDA account instruments](https://developer.oanda.com/rest-live-v20/account-ep/).
