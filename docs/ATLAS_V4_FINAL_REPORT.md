# ATLAS V4 implementation report

Report date: 2026-08-21  
Status: COMPLETE - ATLAS V4 `4.0.0`, PHASE 15.

All fifteen implementation phases passed their exit gates. V4 owns the active analytical signal procedure and can publish a server-authoritative LONG or SHORT only when every governed gate passes. This completion does not enable broker order placement, manufacture missing market evidence, or convert a degraded runtime dependency into a healthy one.

## Completion matrix

| Area | Status | Verified result |
|---|---|---|
| Phase 0 audit | COMPLETE | Repository, active routes, data sources, AI logic, tests, security boundary, and migration gaps documented. |
| Phase 1 MT5/data quality | COMPLETE | Read-only loopback gateway, canonical universe, 15M/1H/4H/1D closed bars, health states, fail-closed quality, immutable snapshots, API, tests, and live DEMO proof. |
| Phase 2 feature foundation | COMPLETE | Versioned deterministic structure, volatility, auction, regime, MTF, and Level-1 liquidity evidence with lineage, provenance, leakage checks, API, and tests. |
| Phase 3 macro/events/positioning | COMPLETE | Numeric-first engines, provider-managed FRED/RSS/CFTC/MT5 ingestion, typed research API, explicit availability, event/news veto state, and unavailable derivatives contract are verified. |
| Phase 4 Quant Lab validation | COMPLETE | Next-bar triple barrier, purged/embargoed rolling and expanding folds, sealed holdout controls, experiment registry, cost-aware backtest, and block-bootstrap robustness are verified. |
| Phase 5 primary ML | COMPLETE | Deterministic logistic baseline and randomized-stump challenger, immutable artifacts, disjoint OOS validation, manual SHADOW-only governance, typed API, and non-publishable raw candidates are verified. |
| Phase 6 AI review | COMPLETE | Four local specialist roles, mandatory adversarial criticism, strict structured output, event thresholds, deduplication, fail-closed availability, typed API, and non-voting authority boundaries are verified. |
| Phase 7 meta-labeling | COMPLETE | Evidence-dependency grouping, typed trust features, purged/embargoed walk-forward logistic meta model, OOS net-improvement metrics, strict lineage, typed API, and raw non-publishable predictions are verified. |
| Phase 8 calibration | COMPLETE | Governed Platt/isotonic policy, chronological OOS fit/evaluation split, exact TP-before-SL semantics, sample sufficiency, reliability bins, uncertainty, diagnostics, typed API, and non-authoritative calibrated outputs are verified. |
| Phase 9 Quant validation | COMPLETE | Grounded regime stability and prediction drift, eleven persisted statistical checks, deterministic PASS/CAUTION/VETO/INSUFFICIENT precedence, absolute VETO authority, typed API, and non-publication boundary are verified. |
| Phase 10 expectancy/risk | COMPLETE | Cost-adjusted expectancy, structural entry/invalidation/targets, factor/correlation/heat exposure, thirteen-check hard risk overlay, typed API, and immutable veto precedence are verified. |
| Phase 11 signal engine | COMPLETE | Sole publication gate, exact calibrated grades, explicit rejection states, immutable canonical schema/lineage/revisions, typed APIs, and acceptance cases A-F are verified. |
| Phase 12 server tracking | COMPLETE | Validated post-publication MT5 snapshots drive append-only lifecycle events, executable-side TP/SL detection, server metrics, audited controls, reload persistence, and complete lineage reconstruction. |
| Phase 13 V4 UI/UX | COMPLETE | Active root route is a responsive, read-only V4 terminal over one typed persisted-evidence projection; no browser signal/tracking authority or demo fallback exists. |
| Phase 14 dashboards | COMPLETE | Persisted-only calibration, reliability, performance slices, backtest/cost, experiment, robustness/DSR/PBO, model-comparison, and veto evidence are traceable to IDs and sample counts. |
| Phase 15 hardening | COMPLETE | Alembic adoption/recovery, loopback/auth/input/rate/security/redaction controls, component health, failure tests, persistent Compose policy, and final observed-behavior reconciliation are verified. |

## Live evidence

- MT5 source: genuine local MT5 DEMO account, read-only.
- Healthy immutable snapshot: `2da2bf9dd0aeea16e1fc0703a2a0ccbf15d24b6d10c6caaa332ddc7221d47cbd`.
- Snapshot coverage: 260 closed bars on each official timeframe and 1,000 ticks.
- Feature artifact: `d8f7c73c8dda04974ea98b1217229aff33355b89343b54017950c68da96f6f37` (`feature-foundation-v3`).
- Feature output: 144 records across seven explicit evidence families.
- Observed MTF alignment: `MIXED`.
- Observed auction states: 15M/1H `BALANCED`; 4H/1D `ACCEPTANCE`.
- Liquidity: Level 1, `UNAVAILABLE` for the persisted historical snapshot because its older schema did not retain the tick-window start. Futures volume and order book are explicitly false.
- Canonical analytical signal authority: implemented; no live signal is claimed by this saved snapshot evidence.
- Execution: false.
- Official CFTC adapter live check: two GOLD Legacy Futures Only rows returned from the CFTC public-reporting API; latest report date `2026-08-11`, open interest `400309`.
- Official RSS live check: 87 recent items parsed across Federal Reserve, ECB, Bank of Japan, and BIS feeds with zero provider errors.
- FRED numeric/release adapters: contract-tested; runtime correctly reports missing configuration until a valid free `FRED_API_KEY` is supplied.
- Four local LLMs: exact inventory/listener checks and real generation passed for `ministral-3:3b`, `llama3.2:1b-instruct-q3_K_L`, `qwen2.5:3b`, and `phi4-mini` on ports 11434-11437.
- PostgreSQL and Redis: observed live on loopback; backend readiness reported both healthy after the Alembic head was applied.

## Verification gates

- Backend: 142 tests collected, 141 passed, 1 optional integration skipped; Ruff passed; strict mypy passed across 92 source files; coverage 86%.
- Phase 3 focused gate: 16 tests passed; Ruff and strict mypy passed.
- Phase 4 focused gate: 10 tests passed, including the typed research API; no holdout-open, promotion, signal, or execution endpoint exists.
- Phase 5 focused gate: 5 tests passed, including reproducible model artifacts, leakage/schema rejection, governed manual SHADOW review, raw-candidate restrictions, and absence of an ACTIVE endpoint.
- Phase 6 focused gate: 5 tests passed, including four-role routing, mandatory adversarial review, threshold skipping, disabled/unavailable behavior, deduplication, direction-tamper rejection, typed persistence API, and absence of an AI decision endpoint.
- Phase 7 focused gate: 5 tests passed, including evidence redundancy grouping, deterministic purged walk-forward training, OOS net improvement, lineage/time-order rejection, raw meta prediction restrictions, typed APIs, and absence of a promotion endpoint.
- Phase 8 focused gate: 5 tests passed, including exact outcome semantics, governed method selection, chronological OOS evaluation, reliability diagnostics and audit flags, insufficient-evidence behavior, calibrated-output authority restrictions, typed APIs, and absence of a publication endpoint.
- Phase 9 focused gate: 5 tests passed, including grounded regime/drift evidence, an all-pass statistical result, absolute Quant VETO despite calibrated probability, missing DSR/PBO insufficiency for multiple variants, typed APIs, and absence of publish/order routes.
- Phase 10 focused gate: 5 tests passed, including exact net expectancy, structural ATR-bounded targets, repeated-factor/correlation vetoes, hard risk veto despite high probability, typed APIs, and absence of signal/order routes.
- Phase 11 focused gate: 7 tests passed, including critical acceptance cases A-F, exact grade boundaries, successful canonical publication, immutable revisions, typed APIs, and absence of an order route.
- Phase 12 focused gate: 5 tests passed, including acceptance case G, post-reload TP1 persistence, conservative stop handling, degraded-data cancellation, audited manual invalidation, full lineage reconstruction, typed APIs, and absence of an order route.
- Phase 13 focused gate: 4 backend presentation tests and 3 frontend policy tests passed, including persisted-only aggregation, corrupt/missing evidence handling, strict loopback proxying, exact market filters, missing-probability display, and decision-state styling.
- Phase 14 focused gate: 4 performance-service tests passed, including no-fabrication empty state, calibration reliability/sample/run lineage, canonical publication/veto traceability, deterministic backtest cost drag, and corrupt-row reporting.
- Phase 15 focused gate: security boundary, request limits, admin token, mutation rate limit, redaction, component health, infrastructure failure, migration idempotency/data preservation, and destructive-downgrade rejection passed.
- Legacy Python compatibility: 29 tests passed.
- TypeScript: 55 tests passed; typecheck and ESLint passed.
- Frontend: Vinext production build passed; rendered local-first terminal test passed.
- Browser E2E: all ten V4 workspaces, 15 market rows, instrument switching, Phase 15 component health and authority labels, desktop and 390x844 mobile layouts without horizontal overflow, explicit unavailable evidence, and zero console warnings/errors verified at `http://localhost:3003/`.

## Safety and truthfulness

There is no V4 order endpoint or broker order method. Elliott Wave and Fibonacci are disabled by default. Broker tick volume is never presented as consolidated institutional volume. Existing legacy LONG/SHORT construction, four-LLM consensus, seven-desk voting, and browser outcome tracking remain preserved only for regression/reference and are not V4 authority.

V4 now replaces those legacy decision surfaces on the active port-3003 root route. The browser consumes the server-owned V4 decision, lineage, tracking, calibration, and performance projection and cannot synthesize a fallback. Dashboard metrics are available only where persisted evidence and trace IDs exist.

Phase 5 primary-model and Phase 7 meta-model probabilities are raw, explicitly uncalibrated research outputs. Phase 8 can produce a calibrated Atlas probability only from sufficient chronological OOS meta evidence with exact TP-before-SL semantics. Phase 9 supplies the final statistical Quant result, Phase 10 supplies net-expectancy, structural-target, exposure, and risk authority, Phase 11 is the sole canonical analytical publication authority, Phase 12 owns lifecycle tracking and outcome state, Phase 13 projects those records into the active terminal without changing them, Phase 14 projects traceable calibration/performance research without gaining signal or execution authority, and Phase 15 secures and monitors the boundary without changing decision precedence.

## Known limitations

- The Alembic baseline intentionally adopts the existing generic SQLModel record collection instead of destructively normalizing historical evidence; future schema changes must remain additive and provenance-aware.
- The saved live snapshot predates the `first_tick_at` field, so its tick-frequency liquidity measure correctly degrades to unavailable.
- The active port-3003 root route consumes `/api/v4/terminal`; preserved `/console` and legacy APIs have no V4 authority.
- The current live database contains no persisted V4 MT5 snapshot or performance artifacts, so live verification correctly shows `INSUFFICIENT DATA` and `DATA UNAVAILABLE` rather than substituting demo data or charts.
- Provider-managed refresh is disabled by default and requires `V4_CONTEXT_REFRESH_ENABLED=true`; FRED factors/calendar additionally require a free 32-character key.
- Quant Lab persistence currently uses the generic record store. The holdout is sealed by application policy and hidden from HTTP; operating-system and PostgreSQL account controls remain deployment responsibilities.
- V4 AI review is disabled by default and uses only locally installed Ollama models when enabled. It has no cloud fallback, decision route, or authority when a reviewer is unavailable.
- One Starlette test-client deprecation warning remains; it does not affect the passing gate but should be removed with the next dependency refresh.
- `ATLAS_ADMIN_TOKEN` is optional for the local single-user loopback deployment. Any multi-user or non-loopback deployment requires a real identity layer and a shared rate limiter before exposure.
- Direct Docker CLI execution is denied by the Codex sandbox. Compose policy was verified statically, while the already-running PostgreSQL/Redis services and backend readiness were verified live; no unobserved CLI build/start is claimed.

## Operational next step

Accumulate governed OOS evidence, keep MT5 and the four local model services healthy, and monitor `GET /api/v4/health/operational`. Atlas will publish analytical signals only when the full evidence chain passes; absence of a signal is a governed result, not a reason to weaken thresholds. Broker order execution remains disabled.
