# Atlas Hedge Fund OS backend - Phase 3

This package adds deterministic agentic research, mandate validation, and
OHLCV-only backtesting beside the existing Atlas Quant terminal. It does not
replace the live Next/Vinext application or enable paper/live execution.

## Implemented

- Unified LLM registry for OpenAI, Anthropic, Google, Groq, Atlas Cloud, and
  loopback Ollama.
- Environment discovery without import-time network calls.
- Provider-specific structured-output requests, local JSON Schema validation,
  a maximum of three exponential retries, fail-closed errors, and normalized
  token accounting.
- SEC EDGAR recent-filing discovery and deterministic 13F, Schedule 13D, and
  Form 4 parsers.
- Ordered ticker resolution: Yahoo → OpenFIGI → TradingView → optional LLM.
- Sector/industry resolution through Yahoo with an optional LLM fallback.
- Yahoo and Binance OHLCV, FMP fundamentals, and FRED economic adapters.
- Finnhub news, EODHD social sentiment, licensed-feed, and configurable
  on-chain adapters normalized to `[-1, 1]`.
- Stable natural keys and idempotent memory, append-only JSONL, and SQLModel stores.
- SQLModel definitions for funds, holdings, assets, price bars, sentiment,
  mandates, runs, and audited decision summaries.
- Native PostgreSQL monthly range partitions for price bars.
- Typed FastAPI `/health` and `/ready` surfaces that keep all execution flags
  disabled.
- Typed `AtlasState`, seven specialist desks, deterministic bull/bear debate,
  weighted convergence arbitration, and strict output contracts.
- A mandatory Risk Guard for universe, asset class, position size, leverage,
  daily/weekly drawdown, correlation, volatility, and missing risk inputs.
- Bounded risk-parity portfolio proposals, idempotent decision-summary audit
  persistence, and a typed research-only `POST /research/evaluate` endpoint.
- `execution_plan` is always empty; manager notes are auditable but cannot
  bypass a failed risk check.
- Strict semantic-versioned YAML mandates with validated universe, desk,
  risk, execution-intent, and backtest sections.
- Completed-bar signals, next-open fills, no-look-ahead tests, buy-and-hold
  comparison, Sharpe, Sortino, drawdown, profit factor, win rate, and excess return.
- Typed `POST /mandates/validate` and `POST /backtests/run` endpoints with
  idempotent PostgreSQL records and research-only lifecycle results.

## Offline verification

The business-logic tests use injected transports and static filing fixtures;
they never call a cloud model, SEC, Yahoo, Binance, or a paid API.

From the delivery root:

```powershell
npm run test:python
```

Or run the backend suite directly with Python 3.12:

```powershell
python -m unittest discover -s backend/tests -p "test_*.py" -v
```

## Provisioned backend setup

When package installation is available, run the checked setup script from the
delivery root:

```powershell
scripts\setup-backend.ps1
Copy-Item backend\.env.example backend\.env
.venv\Scripts\uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8800
```

### V4 market-data foundation

Phases 1-2 expose a read-only, fail-closed MT5 and deterministic feature boundary under `/api/v4`:

- `GET /api/v4/health`
- `GET /api/v4/health/operational?instrument=XAUUSD`
- `GET /api/v4/health/mt5?instrument=XAUUSD`
- `GET /api/v4/health/data?instrument=XAUUSD`
- `POST /api/v4/market/{instrument}/snapshot`
- `GET /api/v4/market/{instrument}/snapshot`
- `POST /api/v4/features/{instrument}/compute`
- `GET /api/v4/features/{instrument}/latest`
- `POST /api/v4/context/{instrument}/compute`
- `GET /api/v4/context/{instrument}/latest`

Snapshots cover the official `15M`, `1H`, `4H`, and `1D` timeframes and are
stored immutably by content identity. No V4 order endpoint exists. A snapshot
with stale, invalid, incomplete, or degraded data is never publication eligible.
Phase 2 feature sets are versioned, tied to one snapshot, and checked for
future leakage. They describe structure, volatility, regime, timeframe
alignment, broker-volume auction context, and Level-1 MT5 liquidity with
explicit provenance and evidence-family tags. They make no consolidated-volume,
order-book, probability, signal, or execution claim. Elliott Wave and Fibonacci
research switches are disabled by default.

### V4 Phase 15 operations

Backend startup applies the Alembic baseline before Uvicorn. The migration adopts an existing SQLModel schema without dropping data, creates the measured PostgreSQL price partitions when supported, and intentionally blocks destructive downgrade. Validate the live migration head with:

```powershell
scripts\validate-migrations.ps1
```

All V4 clients must be loopback. State-changing V4 requests are size-bounded and rate-limited and, when `ATLAS_ADMIN_TOKEN` is configured, require that token. Secret-bearing settings are excluded from object representations and health responses. `GET /api/v4/health/operational` reports database, Redis, MT5/data, governed-model, and local-LLM readiness with explicit degraded/disabled states. Recovery steps are documented in `docs/ATLAS_V4_RECOVERY.md`.

Phase 3 context endpoints currently accept typed research inputs for numeric
macro series, gold relationships, official CFTC COT reports, defensive news,
and economic events. The free official CFTC Legacy Futures Only adapter uses
`https://publicreporting.cftc.gov/resource/6dca-aqww.json`. Unconfigured
derivatives evidence is returned as `DATA_UNAVAILABLE`; no IV, skew, gamma, or
term structure is inferred. Context may issue an event/news candidate veto but
cannot publish a direction or signal.

Run the complete quality gate with:

```powershell
scripts\run-backend-gate.ps1
```

The gate runs Ruff, strict mypy, unit and database/API integration tests, and
requires at least 80 percent coverage across backend business logic. The same
gate is defined in `.github/workflows/backend-test.yml` with isolated
PostgreSQL 16 and Redis 7 services.

## Local PostgreSQL and Redis

Atlas includes a loopback-only Docker Compose stack. After Docker Desktop is
installed and running, launch this from the delivery root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\setup-infrastructure.ps1"
```

The script generates separate random PostgreSQL and Redis passwords in the
ignored `.env.infrastructure` file, starts both persistent containers, waits
for real service probes, and reruns the backend gate against PostgreSQL and
Redis. It never enables paper or live execution.

Start the Phase 3 research API with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start-backend.ps1"
```

The API binds only to `127.0.0.1:8800`. To stop the infrastructure without
deleting its persistent volumes, run `scripts\stop-infrastructure.ps1` using
the same execution-policy command form.

Populate only the providers you intend to use. Merely setting a key marks that
provider configured; code must still select it explicitly. The Risk Guard is
active and its mandatory veto tests pass. Keep every execution flag in
`policy.default.json` disabled.

## SEC daily refresh

`.github/workflows/sec-fetch.yml` runs the idempotent staging command. Configure
`SEC_USER_AGENT` as a repository secret and `SEC_FUNDS_JSON` as a repository
variable, for example:

```json
[{"cik":"1067983","name":"Berkshire Hathaway"}]
```

The workflow writes an artifact; it does not commit generated holdings into the
repository.

## Phase gate

`backend/app/agents/` contains a LangGraph-compatible `TypedDict` state and a
deterministic graph coordinator. PostgreSQL/Redis, unit, strict mypy, and
coverage gates passed before Phase 2 activation. Phase 3 adds strict mandates
and deterministic backtesting without changing that safety boundary. Optional
LLM enhancement may not alter a hard Risk Guard result or enable execution.
