# Build status

## Implemented and verified

- Production terminal at `http://localhost:3003`.
- Phase 3 backend research API at `http://127.0.0.1:8800`, with seven desks,
  deterministic debate/arbitration, hard Risk Guard, strict YAML mandates,
  no-look-ahead backtests, and non-executable portfolio proposals.
- Read-only MT5 bridge and live quote route.
- 15M, 1H, 4H, and 1D closed-bar analysis.
- LONG, SHORT, WAIT, and conditional pending-order research states.
- Regime overlay and routed trend/mean-reversion weighting.
- RSI z-score, directional efficiency, tick-volume ratio, bid/ask pressure, anchored-VWAP deviation, and ATR-percentile features.
- Deterministic decision-stump plus nearest-pattern ML committee.
- Regime-aware research risk and ATR stop adjustment.
- IndexedDB schema v3 with persisted signal lifecycle outcomes.
- Trigger, TP1/TP2/TP3, stop, expiry, and manual-close lifecycle handling.
- Friction-adjusted performance dashboard, rolling results, score buckets, equity curve, calibration gate, and informational Kelly output.
- Four-endpoint loopback Ollama adapter with strict 4/4 consensus and no cloud fallback.
- Responsive Signals, Analysis, Insights, and Chat workspaces.
- Python order-flow engine with AVWAP, volume profile, tick-rule CVD/delta, footprint, absorption, liquidity proxies, and bounded confluence.
- Python LLM orchestrator with exact four-model task routing, strict JSON contracts, immutable deterministic signal fields, and non-authoritative Phi-4 risk review.
- ATLAS Quant v2 pipeline with automated AVWAP anchors, volume-profile value area/nodes, OHLCV delta/CVD, footprint/OFII, BOS/CHoCH, macro/news health states, `-10` to `+10` confluence, volatility exposure, structural SL/TP, correlation guard, scenario stress, and SQLite attribution.
- Full v2 API at `/api/atlas/v2`, plus a visible institutional v2 validation card in the terminal.
- Config-driven endpoints and prompt directory with five-minute local LLM caching and per-task deterministic degradation.
- FastAPI-lifespan V4 background orchestration with immediate/fixed-delay
  cycles, full-universe MT5 ingestion, feature/context persistence,
  leakage-safe experimental primary-model training, OOS robustness evidence,
  per-instrument failure isolation, durable status/run contracts, and visible
  blocker codes in the Settings UI.

## Verification result

- TypeScript: clean.
- Unit tests: 41 TypeScript and 23 Python passed.
- Rendered application test: passed.
- Production build: passed.
- Browser console: no warnings or errors.
- Live MT5 check: connected in DEMO account mode, read-only, order submission disabled.

The V4 orchestration addition is covered by four focused tests for chronological
purged training, EXPERIMENTAL-only model governance, idempotent evidence,
per-market failure isolation, API status, and absence of order submission. The
full backend suite passes with one environment-specific skip; TypeScript unit
tests, typecheck, ESLint, Ruff, the production build, and targeted strict mypy
for every changed Python module pass.

## Live local-LLM verification (August 13, 2026)

Ollama and all four configured model tags are installed. The native service listens on port 11434, and Atlas owns loopback-only relays on ports 11435–11437. Each endpoint was warmed independently and returned the required strict numeric response from its assigned model. A real `POST /api/local-sentiment` request then completed with `availableModels: 4`, `requiredModels: 4`, `consensus: true`, and `cloudFallback: false`; all four endpoint results were available. The neutral verification note produced a neutral aggregate score of `0.00`, as expected.

## Live v2 verification (August 14, 2026)

- MT5, the Python intelligence bridge, Atlas on port 3003, and Ollama ports 11434–11437 all responded live.
- A real four-model sentiment request returned 4/4 available with consensus and no cloud fallback.
- The v2 orchestrator completed schema-valid Llama `micro_regime`, Qwen `signal_json`/`risk_validation`, and Phi `confluence_math_review`/`scenario_stress` tasks. An explicit Ministral `confluence_synthesis` check also passed.
- The XAUUSD H1 snapshot returned raw confluence `-5`, `BALANCED` micro regime, `ADEQUATE` liquidity, broker-tick orderflow, and final `NO_TRADE` because the v2 weak-signal gate suppressed the setup outside discovery.
- Desktop and 390px browser checks passed with no horizontal overflow and no console warnings/errors.

## Non-negotiable boundary

Atlas performs research only. There is no MT5 order-send, order-modify, or order-close path in the application or bridge.
# ATLAS V3 controlled migration (2026-08-18)

- Completed evidence-based repository audit and saved `ATLAS_V3_REPOSITORY_AUDIT.md`.
- Created `checkpoints/atlas-v3-prechange-20260818.zip` because this delivery has no Git history.
- Added pure TypeScript V3 data-quality, expectancy, Quant validation, publication-gate, probability-grade, and timeframe-selection modules.
- Added mandatory policy tests for 79.99% rejection, 80.00% threshold behavior, Quant veto, negative expectancy, event/data vetoes, grade boundaries, and timeframe selection.
- Fixed ESLint third-party directory exclusions and made Python tests prefer the project virtual environment.
- Full `npm test` gate passes after the foundation changes.

Remaining: server-side shadow evidence, model registry/governance, authoritative V3 API integration, portfolio/event/macro services, UI V3 fields, and retirement of duplicated V2 decision paths.
