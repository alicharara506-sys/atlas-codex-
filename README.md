# Atlas Quant

Atlas Quant is a local-first quantitative signal decision terminal for forex and commodities. Its active root route is ATLAS V4: a read-only Vinext presentation over the server-owned FastAPI V4 decision, lineage, and tracking chain. It has analytical LONG/SHORT publication authority only after every calibrated probability, Quant, expectancy, data, event, structural, exposure, and risk gate passes. It has no broker order-placement path and no demo or cloud fallback on the V4 route.

The current release is ATLAS V4 `4.0.0`, Phase 15. Server-side analytical signal authority is enabled; browser decision authority and broker order placement remain disabled.

## Active ATLAS V4 terminal

Open [http://localhost:3003](http://localhost:3003). The root route provides Command Center, Markets, Signals, Atlas AI, Calendar, Performance, Quant Lab, Models, MT5, and Settings. It reads a typed workspace from `GET /api/v4/terminal?instrument=XAUUSD` through a loopback-only proxy. Missing persisted evidence appears as `DATA UNAVAILABLE`, `DEGRADED`, or `INSUFFICIENT DATA`; the browser does not generate a replacement decision.

The current runtime topology is:

```text
MetaTrader 5 -> loopback bridge -> FastAPI V4 :8800
             -> persisted V4 evidence/decisions/tracking
             -> read-only Vinext terminal :3003
```

The preserved legacy terminal modules and `/console` route remain available for regression/reference only and are not V4 signal authority.

Operational health is available from `GET /api/v4/health/operational?instrument=XAUUSD`. It reports database, Redis, MT5/data, governed-model, and four-model Ollama readiness without inventing unavailable evidence or exposing secrets.

The backend now owns a persistent V4 background worker. Its live status is
available at `GET /api/v4/orchestration/status`, its durable run history at
`GET /api/v4/orchestration/runs`, and an authenticated/manual cycle at
`POST /api/v4/orchestration/run`. The Settings screen displays its cadence,
cycle state, next run, and per-market blocker codes. The production local
configuration enables a five-minute cadence across the full approved universe.
It continuously persists MT5 snapshots, features, explicit context, leakage-safe
experimental model training, and robustness evidence. A new model is not
silently promoted: it must satisfy the existing governed SHADOW approval before
it can emit a primary candidate. Calibration, Quant, and risk evidence must also
be complete before the sole publication gate can issue LONG or SHORT.

## ATLAS V3 migration status

The ATLAS V3 repository audit is recorded in `ATLAS_V3_REPOSITORY_AUDIT.md`, with a recoverable pre-change checkpoint under `checkpoints/`. Phase 1 of the V3 migration adds a pure, typed, fail-closed policy foundation under `app/lib/v3/`:

- unified live-data quality checks for stale, malformed, duplicate, gapped, demo, and provider-disagreement states;
- explicit net expectancy including spread, slippage, and commission costs;
- independent Quant validation with sample, Brier, ECE, out-of-sample, stability, drift, cost, and expectancy vetoes;
- exact 80% publication threshold and B/A/A+/A+++++ grading;
- a final publication gate where any critical failure returns WAIT;
- best-timeframe ranking that cannot prefer 15M merely because it is shortest.

These modules are deliberately policy-only in the first migration phase. They do not manufacture calibration evidence and do not enable execution. The legacy V2 route remains operational until server-side shadow evidence, model governance, and the authoritative V3 API are connected and parity-tested.

## Current capabilities

- Read-only MT5 quotes, contract metadata, broker ticks, and 260 closed bars for 15M, 1H, 4H, and 1D.
- Explainable LONG, SHORT, WAIT, and conditional pending-order research states with entry zone, invalidation/stop, and three targets.
- Eight-factor technical scoring plus regime routing, engineered microstructure/liquidity/volume features, and a deterministic local ML committee.
- Regime-aware research risk: 0.50% normally and 0.25% in high volatility; position size always respects broker constraints and rounds down.
- IndexedDB signal lifecycle tracking: generated, triggered, TP1/TP2/TP3, stopped, expired, and manually closed.
- Net outcome analytics with spread and round-turn friction, rolling performance, score buckets, symbol/regime views, calibration gates, and informational Kelly output.
- Optional four-model Ollama consensus on `127.0.0.1:11434` through `:11437`. All four numeric responses are required. If any endpoint is unavailable, Atlas reports that state and applies no sentiment adjustment.
- A loopback-only Python intelligence bridge adds anchored VWAP, bar-volume profile, tick-rule CVD/delta, footprint approximation, absorption, liquidity proxies, and role-routed local-LLM reviews. These outputs are advisory and cannot overwrite deterministic trade geometry or sizing.
- Signals, Analysis, Insights, and Chat workspaces with a responsive live signal card.

## Requirements

- Node.js 22.13 or newer
- npm 10 or newer
- MetaTrader 5 logged in on this Windows laptop
- Python environment containing the `MetaTrader5` package
- Optional: Ollama plus the four configured local models

## Install and run

```powershell
npm ci
npm run build
npm run start
```

This workspace is configured for [http://localhost:3003](http://localhost:3003).

For the MT5 Python dependency:

```powershell
C:\path\to\python.exe -m pip install -r requirements-mt5.txt
```

Relevant `.env.local` values:

```dotenv
PORT=3003
MT5_ENABLED=true
MT5_PYTHON_PATH=C:\path\to\python.exe
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_PORTABLE=false
V4_ORCHESTRATION_ENABLED=true
V4_ORCHESTRATION_INTERVAL_SECONDS=300
V4_ORCHESTRATION_TRAIN_MODELS=true
```

The server starts the loopback MT5 bridge and the production application together. The bridge token stays server-side; account login remains inside MT5.

## Commands

```powershell
npm run typecheck
npm run lint
npm run test:unit
npm test
npm run build
npm run start
```

## Persistence and calibration

The `atlas-trading-terminal` IndexedDB database stores settings, watchlists, journal records, imported calibration outcomes, and live `signalOutcomes`. Outcome probabilities remain uncalibrated until at least 200 resolved outcomes exist, including 40 wins, 40 losses, and 30 samples in each score bucket (40–49, 50–59, 60–69, 70+).

## Four local Ollama models

The sentiment panel expects these loopback models/endpoints:

| Port | Model |
| --- | --- |
| 11434 | `ministral-3:3b` |
| 11435 | `llama3.2:1b-instruct-q3_K_L` |
| 11436 | `qwen2.5:3b` |
| 11437 | `phi4-mini` |

The recommended Windows setup uses the official Ollama installer. With Ollama running, install the four models by running `install-atlas-models.cmd` from File Explorer or a terminal. The installer safely skips model tags that already exist and retries interrupted downloads.

For a portable pinned runtime instead, download the official `ollama-windows-amd64.zip` release to the Windows Downloads folder, then run:

```powershell
npm run llm:setup
```

The portable setup script verifies the pinned official SHA-256, extracts the runtime under `.atlas-local-llm`, starts a temporary loopback server, and pulls all four compact model tags into a shared local store.

The current native Ollama configuration uses:

```dotenv
LOCAL_LLM_ENABLED=true
OLLAMA_MANAGE_SERVERS=false
OLLAMA_PRIMARY_PORT=11434
OLLAMA_RELAY_PORTS=11435,11436,11437
```

Keep the native Ollama service running, then restart Atlas. `scripts/serve.mjs` owns loopback-only relays on ports 11435–11437, while Ollama serves port 11434. Atlas specifies a different installed model for each endpoint, so the committee still produces four independent model responses without loading four heavyweight server processes. Verify all endpoints and model inventory with `npm run llm:check`.

The alternative portable configuration can set `OLLAMA_MANAGE_SERVERS=true`, `OLLAMA_EXECUTABLE`, and `OLLAMA_MODELS_DIR`; in that mode Atlas owns four isolated Ollama processes.

If Ollama is absent or a model does not respond with one strict number, the panel shows consensus unavailable. Cold CPU inference is allowed up to five minutes. Market signals continue from MT5 plus deterministic local engines; no external AI service is contacted.

## Python order flow and LLM orchestrator

Set `ATLAS_INTELLIGENCE_ENABLED=true` to have `scripts/serve.mjs` start `scripts/intelligence_bridge.py` on loopback. The bridge exposes analysis-only `/orderflow`, `/llm/route`, `/llm/pipeline`, `/v2/signal`, and `/render-chart` routes. It has no connection to MT5 order functions and no order-submission route.

`scripts/orderflow_engine.py` calculates:

- anchored VWAP from closed bars;
- bar typical-price volume profile, POC, value area, high-volume nodes, and low-volume nodes;
- tick-rule buy/sell pressure, bar delta, and cumulative volume delta;
- price-bucket footprint, absorption, and swing/volume-node liquidity proxies;
- a bounded, explainable confluence score from `-100` to `+100`.

Spot forex and most broker CFDs have no consolidated tape. Atlas therefore labels every aggressor-side, footprint, and liquidity result as `TICK_RULE_APPROXIMATION`, never as Level 2. When MT5 reports zero trade volume, the engine explicitly changes its basis to `TICK_COUNT_PROXY`.

`scripts/llm_orchestrator.py` routes tasks as follows:

| Task | Local model | Endpoint |
| --- | --- | --- |
| Regime classification and news tagging | Llama 3.2 1B | `11435` |
| Confluence synthesis | Ministral 3B | `11434` |
| Strict signal JSON formatting | Qwen 2.5 3B | `11436` |
| Risk and position-sizing review | Phi-4 Mini | `11437` |

All endpoints are validated as loopback-only, responses must satisfy strict task schemas, and there is no cloud fallback. The TypeScript signal and risk engines remain authoritative. Qwen must copy deterministic action/entry/SL/TP/grade exactly; its displayed rationale is replaced with deterministic Atlas evidence so generated prose cannot contradict the geometry. Phi-4 reviews supplied calculations with `authoritative: false`; malformed or altered output fails closed.

## ATLAS Quant v2 institutional pipeline

`scripts/atlas_core.py` composes the deterministic Python v2 pipeline. It adds automated AVWAP anchoring, contiguous value-area profiles, OHLCV delta/CVD proxies, footprint patterns, OFII, BOS/CHoCH, macro and news availability states, a capped `-10` to `+10` confluence score, structural SL/TP, volatility-normalized exposure units, liquidity/news suppression, correlation warnings, scenario stress, and a local SQLite audit hash. The main terminal displays the v2 confluence/regime/exposure gate, while `GET /api/atlas/v2?symbol=XAUUSD&timeframe=1H` returns the complete schema.

### Wyckoff, SMC, and static chart export

`scripts/wyckoff_engine.py` detects closed-bar accumulation and distribution sequences (PS/SC/AR/ST/Spring and the mirrored supply sequence), with deterministic phase, confidence, event prices, and volume signatures. `scripts/smc_engine.py` labels order blocks, three-candle fair-value gaps, confirmed liquidity sweeps, breaker blocks, and the nearest active order block. These labels are OHLCV structural proxies; they are not claims of centralized order-book visibility.

Atlas runs both analyzers in parallel with its order-flow features. Aligned Wyckoff phases, untested order blocks, and unmitigated FVGs add bounded structural evidence; a confirmed opposing liquidity sweep applies a two-point penalty. The final score remains hard-capped within `-10` and `+10`.

The signal card's **Export Chart** action downloads a static PNG containing candles, volume, entry zone, SL, TP1/TP2/TP3, active order blocks, FVGs, AVWAP, Wyckoff events, and available BOS/CHoCH labels. Files are also written under the configurable `charts.output_directory` (default `data/charts`). `scripts/chart_renderer.py` uses Matplotlib's non-interactive Agg backend when available and otherwise uses Atlas's dependency-free static PNG renderer. No chart route can place or modify an order.

`config.yaml` owns instruments, timeframes, endpoints, prompt directory, profile parameters, and risk limits. The `prompts/` directory contains the specialist task templates. Local model output remains advisory: deterministic market prices, scores, exposure, SL, and TP cannot be invented or overwritten by an LLM. Optional Yahoo, RSS, NewsAPI-free-tier, calendar-page, and local-transformers adapters fail visibly when not configured; Atlas never fabricates missing macro or news data.

## Safety boundary

Atlas is research software, not a broker or financial adviser. `readOnly` is always true and `orderSubmission` is always false in the active API routes. The UI may describe a hypothetical position and track price outcomes, but it cannot submit, modify, or close an MT5 order.

## ATLAS V4 context foundation

The FastAPI V4 backend now exposes research-only context computation under `/api/v4/context/{instrument}`. Its provider-managed refresh combines official FRED numeric factors and date-only releases, a fixed allowlist of official Federal Reserve/ECB/Bank of Japan/BIS RSS feeds, official CFTC positioning, and persisted MT5/feature lineage. It records missing or failed sources explicitly and cannot emit signal direction, Atlas probability, or an order.

Provider refresh is disabled by default. To enable it, add these server-side values and restart the backend:

```dotenv
V4_CONTEXT_REFRESH_ENABLED=true
FRED_API_KEY=your_free_32_character_fred_key
OFFICIAL_NEWS_ENABLED=true
```

Without a FRED key, Atlas continues safely with `FRED_*_NOT_CONFIGURED` provider issues; it never substitutes estimated macro observations or release times.

## ATLAS V4 Quant Lab foundation

The backend also exposes research-only Quant Lab routes for next-bar triple-barrier labels, purged/embargoed walk-forward folds, deterministic cost-aware backtests, block-bootstrap robustness, experiment registration, and final-holdout sealing. The HTTP API can seal a holdout but cannot open it; model promotion and trade execution routes do not exist. DSR and PBO are reported unavailable until valid governed cross-variant OOS inputs exist, rather than being estimated from an unsuitable sample.

## ATLAS V4 primary-model research

The V4 backend now trains a deterministic logistic-regression baseline and randomized-stump challenger against chronological, disjoint research windows. Artifacts are immutable and include their exact feature schema, scope, windows, parameters, and raw OOS metrics. They start EXPERIMENTAL and can enter SHADOW only through a named manual review backed by positive OOS and persisted robustness evidence.

There is no ACTIVE-model route. SHADOW inference returns only an explicitly uncalibrated research candidate with no Atlas probability, grade, signal-publication authority, or execution authority. This model layer is evidence for later V4 validation phases; it does not replace the active terminal or publish a trade.

## ATLAS V4 non-voting AI review

Phase 6 adds a local-only `AIReviewOrchestrator` after the SHADOW primary candidate. Llama 3.2 1B summarizes context, Ministral 3B reviews macro evidence, Qwen 2.5 3B checks market structure, and Phi-4 Mini is the mandatory adversarial critic. They do not vote. Their strict records contain supports, contradictions, event risks, missing data, invalidations, and status; malformed or direction-changing output is marked unavailable.

Calls are event-thresholded, concurrent, bounded, and hash-deduplicated. Identical evidence is reused instead of sent repeatedly. Every bundle remains non-publishable with no Atlas probability, model-promotion authority, or execution authority. Enable the local review service only after the four Ollama models are healthy:

```dotenv
V4_AI_REVIEW_ENABLED=true
V4_AI_CANDIDATE_THRESHOLD=0.55
```

The threshold controls whether an AI review is worth running; it is not a trading probability. There is no cloud fallback or AI decision endpoint.

## ATLAS V4 meta-label research

Phase 7 adds an `EvidenceDependencyEngine` that identifies repeated evidence sharing the same upstream source, plus a logistic `MetaLabelTrainer` that learns whether a primary candidate historically deserved trust. Meta training uses purged/embargoed chronological walk-forward folds and a fixed untuned research threshold. It records OOS Brier, log loss, balanced accuracy, and net-return improvement against accepting every primary candidate.

All training and prediction inputs require persisted candidate and dependency lineage. The artifact remains EXPERIMENTAL and has no promotion endpoint. Its accept/reject probability is raw and uncalibrated, so it is not Atlas probability and cannot create a grade, signal, entry, stop, target, or order.

## ATLAS V4 probability calibration

Phase 8 adds a governed `ProbabilityCalibrationEngine` for persisted chronological OOS meta predictions. Its probability means exactly `P(TP before SL | validated setup and available evidence)`. It requires triple-barrier label lineage, validated setups, net-of-cost outcomes, at least 200 OOS samples, and at least 40 outcomes in each class. Insufficient evidence returns no probability.

The fixed policy uses Platt scaling for 200-499 samples and isotonic regression from 500 samples, with a chronological 70/30 fit/evaluation split. Artifacts include Brier score, log loss, ECE, ten reliability bins, Wilson uncertainty intervals, and explicit audit flags. Raw primary probabilities, LLM confidence, and committee votes are never calibration inputs. A calibrated output still cannot assign a B/A/A+/A+++++ grade, issue Quant PASS, publish a signal, or execute an order.

## ATLAS V4 Quant validation

Phase 9 adds the deterministic, non-LLM statistical authority. It derives regime-stability and prediction-drift reports from persisted OOS/current model records, then validates eleven areas: OOS evidence, sample sufficiency, calibration, robustness, regime stability, multiple-testing risk, MT5 data quality, drift, uncertainty, transaction costs, and net-return evidence. VETO has absolute precedence over a calibrated probability or any AI opinion. Multiple tested variants require valid DSR/PBO evidence; Atlas reports insufficiency instead of inventing those values.

V4 is the planned replacement for the legacy Atlas signal procedure. After the remaining expectancy/risk, canonical-signal, server-tracking, and UI phases pass, the main terminal will consume this V4 chain. Research-only prohibits broker order placement; it does not mean V4 will remain disconnected from the Atlas product.

## ATLAS V4 expectancy and risk

Phase 10 adds a deterministic risk-authority chain. `ExpectancyEngine` requires sufficient two-class evidence and computes net expectancy after observed spread, slippage, commission, and swap friction. `StructuralTargetEngine` derives entry, invalidation, and TP1/TP2/TP3 from market structure and ATR, caps stop distance at 2.5 ATR, and requires at least 1.5R/2R/3R. Fixed-pip, Elliott Wave, and Fibonacci targets are not used.

`PortfolioExposureEngine` evaluates currency and commodity factors, rolling correlation, portfolio heat, and USD concentration. `RiskOverlay` combines those results with Quant status, data/model health, event/liquidity conditions, drawdown, and consecutive-loss limits. Any hard veto returns zero approved risk, regardless of probability or AI opinion. These APIs remain analytical and cannot publish a canonical signal or place a broker order; Phase 11 connects them to the new single V4 publication authority.

## ATLAS V4 canonical signals

Phase 11 adds the single authoritative V4 `SignalPublicationGate`. It resolves the full persisted evidence chain and publishes only when calibrated probability is at least 80%, Quant is exactly PASS, net expectancy is positive, MT5 data is healthy, event/liquidity/model state is acceptable, structural entry/SL/TP are valid, exposure is acceptable, and risk is PASS. Otherwise it returns an explicit WAIT, NO_TRADE, QUANT_VETO, RISK_VETO, EVENT_BLOCK, DATA_DEGRADED, INSUFFICIENT_DATA, or MODEL_UNCERTAIN decision.

Grades are assigned only to published signals: B at 80-<85%, A at 85-<90%, A+ at 90-<95%, and A+++++ at 95-100%. The canonical signal freezes geometry, probabilities, grade, expected R, risk, model/artifact lineage, creation, and expiry. Any later correction is an append-only revision and does not alter the original. This is Atlas analytical signal authority, but it cannot place, modify, or close an MT5 order.

## ATLAS V4 server-side tracking

Phase 12 moves lifecycle authority out of the browser. `SignalTrackingEngine` consumes persisted, healthy MT5 snapshots after publication, uses executable bid/ask sides for entry and outcome detection, and persists append-only pending, active, target, stop, expiry, invalidation, and cancellation events. Its server-owned projection exposes live price, pips, R, MFE, MAE, spread, distances, and elapsed time; repeated snapshots are idempotent and terminal history cannot be rewritten.

The lineage endpoint reconstructs the canonical publication plus its MT5, feature, context, primary/meta model, optional non-voting AI, calibration, Quant, backtest, expectancy, structural-target, exposure, risk, revision, and tracking evidence. The browser remains display-only and no V4 order endpoint exists.

## ATLAS V4 presentation

Phase 13 makes the V4 terminal the active root application. `V4PresentationService` assembles the 15-market universe, 15M/1H/4H/1D evidence, canonical signals, explicit rejection decisions, economic-event assessments, governed model artifacts, optional structured AI reviews, and server-side tracking into one read-only contract. The interface exposes exact B/A/A+/A+++++ grades only for published signals, structural entry/SL/TP geometry, Quant/risk status, immutable history, live MFE/MAE/R/pips, data health, and evidence contradictions.

## ATLAS V4 performance and Quant dashboards

Phase 14 adds persisted-evidence dashboards to Performance and Quant Lab. Reliability bins, calibration diagnostics, performance by instrument/timeframe/regime, deterministic backtest costs and drawdown, experiment registrations, robustness/Monte Carlo runs, DSR/PBO availability, model comparisons, and publication/veto analytics are displayed only with stored trace IDs and sample counts.

When evidence is absent, Atlas displays `DATA_UNAVAILABLE` or `PARTIAL` and no claim; it does not substitute zero metrics or fabricated charts. No UI code computes Atlas probability, publishes a signal, tracks an outcome, promotes a model, submits an order, or falls back to legacy/demo values.

## ATLAS V4 production hardening

Phase 15 completes the V4 migration with an Alembic baseline that safely adopts the existing SQLModel schema, migration validation before backend startup, a recovery runbook, loopback-only V4 access, bounded mutation requests, process-local mutation rate limiting, optional constant-time admin-token authorization, security/no-store headers, and sensitive-setting redaction. The operational health contract distinguishes healthy, degraded, disabled, unavailable, and insufficient-data states across PostgreSQL, Redis, MT5, persisted evidence, governed models, and all four local LLM endpoints.

See `docs/ATLAS_V4_RECOVERY.md`, `docs/ATLAS_V4_SECURITY_AUDIT.md`, and `docs/ATLAS_V4_FINAL_REPORT.md` for the observed Phase 15 evidence and recovery procedure.

## Hedge Fund OS Phase 3 mandate and backtest engine

The [backend Phase 3 package](backend/README.md) lives beside this active terminal. In addition to the Phase 2 desks, debate, arbitration, and Risk Guard, it provides strict YAML mandates, an OHLCV-only no-look-ahead backtester, institutional metrics, static report export, PostgreSQL audit persistence, and typed validation/backtest APIs. Existing `npm run test:python` executes both the terminal intelligence tests and `backend/tests`.

The agent state is LangGraph-compatible, while the coordinator and backtester are deterministic for reproducibility. Portfolio and backtest outputs are non-executable research artifacts. `config/policy.default.json` keeps paper and live execution disabled, and no lifecycle route can promote a mandate while that policy remains active.

Local PostgreSQL 16 and Redis 7 can be provisioned after Docker Desktop is installed by running `scripts/setup-infrastructure.cmd`. The generated credentials remain in the ignored `.env.infrastructure` file, both service ports bind to loopback only, and `scripts/stop-infrastructure.cmd` stops the containers without deleting their volumes.
