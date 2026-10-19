# ATLAS V4 gap analysis

Audit date: 2026-08-19  
Audited root: `CLAUDE_ATLAS_TRADING_TERMINAL/atlas-delivery`  
Scope: frontend, APIs, FastAPI backend, PostgreSQL/Redis, MT5 bridges, analytics, AI/ML, persistence, backtests, tests, runtime, security, and UI.

## Executive finding

Atlas is a working local-first, read-only market-research terminal. Its strongest production-quality properties are the real MT5 bridge, explicit no-execution boundary, closed-bar normalization, deterministic calculations, truthful proxy labeling, strict local-LLM parsing, responsive UI, and tested fail-closed V3 policy primitives.

Atlas is not yet ATLAS V4. The active `/api/atlas` route still generates a 1H research direction from weighted deterministic factors plus a small rule/pattern committee. That output can be LONG or SHORT while `historicalWinProbability` is null. Browser code then creates and tracks a signal outcome from that uncalibrated state. The V3 Quant/publication modules are tested but are not authoritative in the live route. The FastAPI seven-desk workflow is a separate weighted arbitration system and therefore cannot become the V4 decision authority.

The safe migration is progressive: keep MT5 and presentation working, introduce one authoritative versioned backend pipeline, run it in shadow mode, and only publish after primary ML, meta-labeling, calibration, Quant validation, positive expectancy, and hard risk gates all have verified evidence.

## Current system facts

### What Atlas currently does

- React 19/Vinext terminal on `http://localhost:3003`.
- FastAPI research backend on `http://127.0.0.1:8800`.
- Read-only MT5 quotes, closed OHLC bars, ticks, broker metadata, account mode, and account state.
- Exactly four analytical timeframes are fetched: 15M, 1H, 4H, and 1D (`D1` internally today).
- Deterministic structure, trend, momentum, volatility, volume, support/resistance, regime, order-flow proxy, risk geometry, and chart export.
- Optional local Ollama tasks and a separate four-model sentiment consensus surface.
- Browser-side signal tracking and IndexedDB outcomes.
- PostgreSQL-backed research records, mandate validation, deterministic backtests, seven desk traces, Risk Guard, and a Sharpe leaderboard.

### MT5 integration and provenance

`scripts/mt5_bridge.py` uses the official `MetaTrader5` Python package on Windows. It calls only initialization, terminal/account information, symbol lookup, quote, closed rates, and tick-copy functions. No order placement, modification, or cancellation API exists. `scripts/mt5_file_bridge.py` is a fallback that serves files exported by `mt5/AtlasReadOnlyBridge.mq5`, which likewise writes only status, symbols, closed bars, and ticks.

The application is configured for the file bridge on this laptop. A live audit request returned:

```text
provider=MT5
quoteSource=MT5
accountMode=DEMO
readOnly=true
orderSubmission=false
```

Therefore current live prices are genuine broker/MT5 observations, not demo values. When MT5 is disabled, the old route can generate deterministic demo series; V4 must keep that capability strictly outside production publication.

### Current signal and confidence calculation

`app/lib/signal-engine.ts` determines direction from weighted D1/4H/1H/15M trend leans. It calculates an alignment score from eight factor families. `app/lib/ml-committee.ts` adds a 15% adjustment from a deterministic decision-stump forest and nearest-pattern vote. `confidenceFromScore` maps alignment/conflict thresholds to LOW/MODERATE/HIGH. None of these is a statistically calibrated probability.

The active route explicitly sets `historicalWinProbability` to null. `app/lib/calibration.ts` contains an evidence-gated isotonic utility, but server outcomes are not connected to it and it is not part of publication. The live audited XAUUSD response was a LONG research direction with a 46.9 alignment score, LOW qualitative confidence, and no historical win probability. That is not a V4 signal.

### Current ML, validation, and leakage assessment

- Existing ML: rule stumps plus nearest historical return patterns. No persisted model artifact, governed training run, feature registry, walk-forward model selection, or calibration artifact exists.
- TypeScript backtest: chronological, next-bar-open, conservative same-bar ordering, but no spread/slippage/commission/swap, purge, embargo, or untouched holdout.
- Backend backtest: completed-bar moving-average signal and next-open fill with deterministic metrics. It is a research harness, not V4 validation.
- Proper purged walk-forward OOS validation: missing.
- True untouched holdout: missing.
- Multiple-testing controls, Deflated Sharpe, and PBO: missing.

### Current AI design

`scripts/llm_orchestrator.py` has useful specialized model routing and requires structured, non-authoritative output. Preserve that boundary. However, `LocalLLMConsensusEngine` averages four numeric model scores, and the FastAPI Fund OS weighs seven desk signals into a trade intent. Both are voting/arbitration designs and must not influence the V4 decision probability or publication gate. They may remain temporarily as research diagnostics until replaced by macro review, market review, adversarial critique, and context summarization.

## Preserve, retire, and refactor

### Preserve

- Loopback-only read-only MT5 integration and explicit `orderSubmission=false`.
- Closed-bar chronology checks and broker-symbol discovery.
- Pure TypeScript indicators and reusable deterministic structure/order-flow functions.
- Honest `TICK_RULE_APPROXIMATION` and broker-tick-volume language.
- Structural risk geometry and broker-aware sizing utilities, subject to V4 validation.
- Strict Pydantic contracts and structured LLM schema validation.
- PostgreSQL/Redis infrastructure and append-only/idempotent research records.
- Risk Guard concept and empty execution plan.
- Existing premium dark UI, instrument selector, MT5 status, geometry, multi-timeframe views, analytics, Fund OS research views, and responsive CSS.
- Existing tests as regression coverage.

### Retire from decision authority

- Demo series in any production/publication path.
- Uncalibrated LONG/SHORT/pending outputs as canonical signals.
- Browser-created signal records and browser-side TP/SL authority.
- Alignment score presented as a probability surrogate.
- Four-model numeric sentiment consensus as a gate or score booster.
- Seven-desk majority/weighted arbitration as a directional authority.
- Equity, crypto, SEC, insider, and on-chain scope in the V4 FX/commodities production pipeline.
- Direct schema creation as a substitute for migrations.

### Refactor

- One authoritative server-side pipeline: snapshot -> quality -> features -> primary ML -> reviews -> meta -> calibration -> Quant -> expectancy -> risk -> publication.
- Canonical symbols and official timeframe names at domain boundaries.
- MT5 health into explicit CONNECTED/DEGRADED/RECONNECTING/DISCONNECTED/MARKET_CLOSED/AUTH_FAILED states.
- Data quality into scored, issue-coded, fail-closed reports.
- Signal lifecycle into immutable server-side events and revisions.
- Existing agent UI into non-voting review evidence.
- Backend data ingestion toward numeric macro/events/COT for FX and commodities.
- Navigation and screens toward the V4 information architecture only after the authoritative APIs exist.

## Database migration requirements

No migration framework exists; `SQLModel.metadata.create_all` is used. Add Alembic and make every change additive. Preserve existing tables and `ingestion_records`.

Required migration families:

1. Reference and bridge: `instruments`, `broker_symbols`, `mt5_connections`, `provider_health`.
2. Immutable market evidence: `market_snapshots`, `market_bars`, `feature_snapshots`, `data_quality_events`, `regime_snapshots`.
3. Numeric context: `macro_snapshots`, `economic_events`, `positioning_snapshots`, `news_events`.
4. Research governance: `experiments`, `model_versions`, `training_runs`, `validation_folds`, `calibration_runs`, `robustness_runs`.
5. Decision lineage: `predictions`, `ai_reviews`, `meta_predictions`, `quant_validations`, `expectancy_results`, `risk_decisions`, `publication_decisions`.
6. Immutable signals: `signals`, `signal_targets`, `signal_events`, `signal_revisions`, `signal_outcomes`.
7. Portfolio and operations: `portfolio_exposures`, `cost_events`, `audit_logs`.

Foreign keys must allow reconstruction from a signal to its snapshot, feature set, model/calibration versions, all gates, and outcome. Published geometry and probabilities must be immutable; changes create revision rows.

## Requirement classification

Status meanings: **EXISTING** is usable now; **PARTIAL** has reusable code but does not satisfy V4; **BROKEN** contradicts V4 in the active path; **MISSING** has no implementation; **NEEDS_REFACTOR** works in another scope/design; **DEFERRED** is intentionally later in the mandated sequence.

| # | Requirement | Status | Evidence / gap |
|---:|---|---|---|
| 1 | V4 mission | PARTIAL | Research-only and rejection language exist; statistically defensible publication does not. |
| 2 | Mandatory decision hierarchy | MISSING | No authoritative ML -> meta -> calibration -> Quant -> EV -> risk chain. |
| 3 | Repository audit/docs | EXISTING | This audit plus architecture and plan documents. |
| 4 | FX/commodity universe | PARTIAL | Most symbols exist; EURJPY/GBPJPY/AUDJPY absent and oil naming differs. |
| 5 | Exact 15M/1H/4H/1D | EXISTING | Fetched and analyzed; internal daily label is `D1`. |
| 6 | MultiTimeframeEngine | NEEDS_REFACTOR | Weighted trend bias exists; no per-TF ML/calibration/liquidity/event model or independent-evidence accounting. |
| 7 | MT5 primary bridge | EXISTING | Live read-only bridge is authoritative when enabled. |
| 8 | Symbol normalization | PARTIAL | Suffix search/map exists; mappings are duplicated across Python/MQL/TS. |
| 9 | MT5 connection health states | PARTIAL | Basic connected status exists; V4 state machine, latency and freshness are absent. |
| 10 | DataQualityEngine | PARTIAL | Pure V3 bar validator exists; quote/spread/frozen/symbol/market-state coverage is incomplete and not active. |
| 11 | Immutable MarketSnapshot | MISSING | Responses are transient; no canonical snapshot record. |
| 12 | Versioned FeatureStore | MISSING | Features are calculated but not versioned/persisted. |
| 13 | Algorithmic market structure | PARTIAL | Simple range structure plus Python BOS/CHoCH/SMC exist; parameters and canonical output need consolidation. |
| 14 | Auction market theory | PARTIAL | AVWAP/profile/value-area proxies exist in Python; not a canonical context engine. |
| 15 | Volume provenance | PARTIAL | Proxy labels exist; provenance is not carried by every feature record. |
| 16 | Elliott experimental only | PARTIAL | Not in active decision path; explicit feature flag/A-B governance missing. |
| 17 | Fibonacci experimental only | PARTIAL | Not authoritative; explicit feature flag/A-B governance missing. |
| 18 | Probabilistic regime engine | PARTIAL | Deterministic labels exist; probabilities/change-point/HMM governance absent. |
| 19 | Regime-conditioned behavior | PARTIAL | Factor weights change by regime; reliability by regime is unmeasured. |
| 20 | Volatility engine | PARTIAL | ATR/realized/percentile utilities exist; no canonical service/record. |
| 21 | Tiered liquidity engine | PARTIAL | Level-1/proxy evidence exists; explicit tiers and fail-closed contracts missing. |
| 22 | Session intelligence | PARTIAL | Session heuristics exist in Python; holidays/rollover/event integration incomplete. |
| 23 | Numeric-first macro | NEEDS_REFACTOR | FRED/Yahoo utilities and optional Python macro exist; no authoritative instrument-specific numeric macro snapshots. |
| 24 | Gold specialist | PARTIAL | XAU data paths and generic macro exist; rolling relationship engine missing. |
| 25 | CFTC positioning | MISSING | No production COT ingestion/normalization. |
| 26 | Derivatives interface | MISSING | Must return DATA_UNAVAILABLE until reliable sources exist. |
| 27 | Defensive news risk | NEEDS_REFACTOR | Sentiment/news utilities exist; deduplicated defensive risk engine missing. |
| 28 | Economic event risk | MISSING | No authoritative calendar/event veto service. |
| 29 | Primary ML engine | MISSING | Existing committee is not a trained/governed primary model. |
| 30 | Triple-barrier labeling | PARTIAL | A simple label helper exists; V4 volatility/structure labels and persistence are missing. |
| 31 | Purged walk-forward | MISSING | No purge/embargo fold engine. |
| 32 | Untouched holdout | MISSING | No protected data partition or access policy. |
| 33 | Multiple-testing control | MISSING | No variant ledger, DSR, or PBO. |
| 34 | Robustness validator | MISSING | Stress helpers are not a statistical robustness engine. |
| 35 | Non-voting AI review | BROKEN | Specialized routing exists, but voting/weighted arbitration surfaces remain. |
| 36 | Macro reviewer | PARTIAL | Local tasks exist; must consume numeric macro state only. |
| 37 | Market reviewer | PARTIAL | Structured advisory tasks exist; canonical review contract missing. |
| 38 | Adversarial critic | MISSING | Mandatory contradiction-first reviewer absent. |
| 39 | Context summarizer | PARTIAL | Local role routing can be adapted. |
| 40 | Structured AI output | EXISTING | Strict JSON schemas and validation exist. |
| 41 | AI cost control | PARTIAL | Local caching and event calls exist; global budgets/cost ledger not wired. |
| 42 | MetaLabelEngine | MISSING | No trained meta-model. |
| 43 | Evidence dependency | MISSING | No family tagging/redundancy accounting. |
| 44 | Probability calibration | PARTIAL | Isotonic/Brier/ECE utilities exist; no governed server artifact or live integration. |
| 45 | Probability semantics | BROKEN | UI correctly says uncalibrated, but old directions can still become tracked signals. |
| 46 | 80% threshold/grades | PARTIAL | Tested V3 gate exists; not authoritative live. |
| 47 | Quant validator | PARTIAL | Pure tested gate exists; robustness/OOS inputs are not produced and route bypasses it. |
| 48 | Expectancy engine | PARTIAL | Pure EV utility exists; live costs and validated distributions are not connected. |
| 49 | Structural SL/TP | PARTIAL | ATR/structure geometry exists; canonical validation and liquidity/expected-move inputs incomplete. |
| 50 | Risk engine | PARTIAL | Broker sizing and Risk Guard exist; portfolio-level uncertainty/heat needs consolidation. |
| 51 | Hard risk veto | PARTIAL | Research Risk Guard exists; active signal route does not pass one authoritative overlay. |
| 52 | Portfolio exposure | PARTIAL | Correlation checks exist; currency-factor decomposition/heat is missing. |
| 53 | Single publication gate | PARTIAL | Pure V3 gate exists but is not the active authority. |
| 54 | Canonical versioned signal | MISSING | Current TS/Python/Fund OS schemas differ. |
| 55 | Full signal lifecycle | NEEDS_REFACTOR | Browser lifecycle exists with different states and mutable records. |
| 56 | Server-side live tracking | BROKEN | Browser currently detects TP/SL. |
| 57 | Immutable signal history | BROKEN | IndexedDB records can be updated in place. |
| 58 | Signal lineage | MISSING | Partial hashes/audits cannot reconstruct the complete V4 chain. |
| 59 | Model registry | MISSING | No governed model-version entities. |
| 60 | Shadow mode | PARTIAL | Research-only separation exists; no model/feature shadow comparisons. |
| 61 | Drift monitor | MISSING | No feature/prediction/calibration/expectancy drift service. |
| 62 | Research vs production | PARTIAL | Safety boundary exists; code/data namespaces and promotion workflow do not. |
| 63 | Deterministic execution-aware backtest | PARTIAL | Two backtest engines exist; costs/sessions/gaps/delay/swap are incomplete. |
| 64 | Performance analytics | PARTIAL | Core returns/Sharpe/win/expectancy exist; calibration/DSR/PBO/veto breakdowns missing. |
| 65 | Calibration dashboard | MISSING | No reliability curve/bin dashboard. |
| 66 | GTI-inspired original UX | PARTIAL | Original compact signal UI exists; full V4 information architecture does not. |
| 67 | Premium dark design | EXISTING | Current visual language is suitable and original. |
| 68 | V4 navigation | MISSING | Current tabs are Signals/Analysis/Insights/Chat/Fund OS. |
| 69 | Command Center | MISSING | Individual status cards exist, not the specified center. |
| 70 | Market Scanner | MISSING | No universe-wide V4 scanner table/filters. |
| 71 | MTF matrix | PARTIAL | Prices/bar states shown; full feature/probability/Quant matrix missing. |
| 72 | Flagship signal experience | PARTIAL | Geometry/live price exist; calibrated probability/EV/Quant/event authority missing. |
| 73 | Live signal track | PARTIAL | Browser tracker exists; must move server-side. |
| 74 | Structured explainability | PARTIAL | Evidence lists exist; V4 question set/lineage grounding incomplete. |
| 75 | Professional WAIT/NO TRADE | PARTIAL | WAIT is visible; exact gate/reason/required-threshold experience needs V4 response. |
| 76 | Calendar UI | MISSING | No authoritative event calendar screen. |
| 77 | Immutable signal history UI | PARTIAL | Outcome analytics exist; missing full tabs/server immutability and revisions. |
| 78 | Quant Lab | PARTIAL | Fund OS backtest exists; full experiments/walk-forward/calibration/DSR/PBO lab missing. |
| 79 | Grounded Atlas AI | PARTIAL | Chat/local tasks exist; no lineage-grounded retrieval contract. |
| 80 | Cost monitoring | MISSING | Usage ledger exists in backend client; no provider cost model/dashboard/budget gate. |
| 81 | Observability/health endpoints | PARTIAL | Backend health/readiness exists; MT5/data/models/LLM/database split endpoints incomplete. |
| 82 | Zero fabrication | PARTIAL | Missing data is often explicit; deterministic demo mode must be isolated more strongly. |
| 83 | V4 database entities/migrations | NEEDS_REFACTOR | PostgreSQL exists; schema is equity-oriented and has no migrations. |
| 84 | Security audit | PARTIAL | Loopback, bearer options, secret redaction exist; no app auth/rate limiting/admin authorization. |
| 85 | Comprehensive testing | PARTIAL | Strong unit/integration coverage; statistical/failure/E2E V4 suite incomplete. |
| 86 | Critical acceptance tests | PARTIAL | A-F largely covered in pure V3 policy; G-H need server tracking/model promotion tests. |
| 87 | Sequential phases | EXISTING | This plan follows phases 0-15. |
| 88 | Prefer simple validated models | EXISTING | Adopted as implementation policy. |
| 89 | Definition of done | DEFERRED | Not satisfied; must remain an explicit checklist. |
| 90 | Final verification | DEFERRED | Run per phase and fully at V4 completion. |
| 91 | Final report | DEFERRED | Create only when implementation evidence exists. |
| 92 | Engineering principles | EXISTING | Adopted as non-negotiable decision rules. |
| 93 | Absolute gate hierarchy | MISSING | Pure pieces exist; authoritative end-to-end implementation does not. |
| 94 | Begin with audit then implement | EXISTING | Audit completed; implementation starts at Phase 1. |

## Baseline verification evidence

Most recent pre-V4 verification on this repository:

- TypeScript unit tests: 52 passed.
- Legacy Python tests: 29 passed.
- FastAPI/backend tests: 39 passed, 1 environment-dependent test skipped.
- Typecheck, ESLint, production build, and rendered-HTML test: passed.
- Browser: no errors/warnings; 390px layout had no horizontal overflow.
- Live endpoints: frontend, backend health, and research history returned HTTP 200.

These are regression baselines, not proof of the missing V4 statistical claims.

## Immediate safety decision

Until V4 model/calibration/Quant evidence exists, the old route may remain visible only as explicitly labeled research diagnostics. It must not be upgraded in name, assigned a V4 grade, or treated as a publishable signal. Phase 1 will first create authoritative MT5/data-quality/snapshot foundations and fail closed on stale or invalid evidence.
