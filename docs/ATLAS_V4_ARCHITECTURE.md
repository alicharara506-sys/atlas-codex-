# ATLAS V4 target architecture

Architecture status: target plus incremental implementation contracts. This document describes the intended authority boundaries; it does not claim every box is implemented.

Implementation checkpoint (2026-08-20): Phases 0-14 are implemented. The live, read-only MT5/data-quality boundary, deterministic feature foundation, provider-managed context layer, leakage-resistant Quant Lab foundation, governed primary-model research layer, non-voting local AI review layer, evidence-dependency control, time-series meta-label research layer, probability calibration, final statistical Quant authority, cost-adjusted expectancy, structural trade geometry, portfolio exposure controls, hard risk overlay, one canonical signal-publication authority, server-owned signal lifecycle tracking, grounded V4 decision terminal, and traceable performance/Quant dashboards are operational in research mode. FRED factors and release dates require a user-supplied free FRED API key; missing configuration is an explicit degraded/unavailable state. Primary artifacts begin EXPERIMENTAL and may enter SHADOW only through a named, logged manual review after positive OOS and robustness evidence. No V4 model can become ACTIVE. AI review has no decision authority. Quant and risk vetoes are absolute. The legacy port-3003 signal presentation is retired from the active root route; preserved legacy code has no V4 authority.

V4 is the replacement architecture for Atlas, not a permanent sidecar. Phases 10-14 have passed their gates, the canonical Atlas terminal consumes the V4 decision chain, and the legacy signal procedure is retired from authority. Research-only means analytical signal generation without broker order placement; it does not mean keeping the old Atlas procedure as the product's authority.

## Non-negotiable authority chain

```text
MT5 live data
  -> DataQualityEngine
  -> immutable MarketSnapshot
  -> versioned FeatureStore
  -> structure / MTF / regime / volatility / liquidity / macro / event / positioning
  -> PrimaryMLEngine directional candidate
  -> structured non-voting AI reviews + mandatory adversarial critic
  -> MetaLabelEngine accept/reject
  -> ProbabilityCalibrationEngine
  -> QuantValidationEngine
  -> ExpectancyEngine
  -> RiskOverlay + PortfolioExposureEngine
  -> StructuralTargetEngine
  -> SignalPublicationGate
  -> immutable Signal or explicit rejection state
  -> server-side SignalTrackingEngine
  -> immutable outcomes, calibration, and drift loops
```

No downstream stage may bypass or repair an upstream rejection. AI output is evidence review, never Atlas probability.

## Runtime topology

```text
MetaTrader 5 (broker terminal)
  -> AtlasReadOnlyBridge.mq5 or MetaTrader5 Python API
  -> loopback MT5 bridge
  -> FastAPI V4 backend
       - gateway and quality services
       - feature/research services
       - decision pipeline
       - PostgreSQL append-only lineage
       - Redis bounded cache/coordination
       - local Ollama/cloud adapters behind explicit budgets
  -> versioned local REST/SSE APIs
  -> React/Vinext presentation client
```

The browser does not calculate authoritative signals, resolve TP/SL, write final outcomes, or access provider secrets. It renders server-owned state.

## Domain boundaries

### Production scope

- Forex: EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD, EURJPY, GBPJPY, EURGBP, AUDJPY.
- Commodities: XAUUSD, XAGUSD, WTI, BRENT.
- Official timeframes: `15M`, `1H`, `4H`, `1D`.

Broker aliases are resolved only by `SymbolMappingService`. Internal records use canonical symbols. Compatibility adapters may translate current `USOIL`/`UKOIL` and `D1` names at boundaries.

### Research-only legacy scope

Equity, crypto, SEC, insider, on-chain, seven-desk arbitration, and four-model consensus modules may remain isolated for regression/reference. They cannot feed V4 publication.

## Phase 1 service contracts

### `SymbolMappingService`

- Canonical allowlist and asset classes.
- Explicit aliases for WTI/USOIL and BRENT/UKOIL.
- Broker candidate generation without leaking broker names into domain logic.
- Records the selected broker symbol and mapping method.

### `MT5Gateway`

- Thin async client for health, account, quote, bars, ticks, and symbol metadata.
- Loopback-only URL validation and optional bearer authentication.
- Bounded timeouts and typed failures.
- No execution methods in its protocol or implementation.

### `MT5HealthMonitor`

Produces:

```text
CONNECTED | DEGRADED | RECONNECTING | DISCONNECTED | MARKET_CLOSED | AUTH_FAILED
```

with broker/server/account mode, terminal status, last tick, tick age, latency, symbol availability, and issues. Credentials and account login are not returned to the browser.

### `MT5MarketDataService`

- Resolves canonical symbols.
- Fetches exactly the requested official timeframes.
- Excludes forming bars.
- Tags volume as `BROKER_TICK_VOLUME` unless better provenance is proven.
- Builds immutable normalized inputs for the quality engine.

### `DataQualityEngine`

Checks quote validity, crossed bid/ask, stale/future ticks, spread spikes, insufficient/missing/duplicate/non-chronological bars, timestamp gaps, impossible OHLC, frozen feeds, mismatched symbols, abnormal gaps, market state, and timeframe completeness.

Output:

```json
{
  "score": 0,
  "status": "HEALTHY|DEGRADED|STALE|INCOMPLETE|INVALID",
  "issues": [],
  "critical": true
}
```

Any critical report prevents candidate publication.

### `MarketSnapshot`

Frozen, versioned, hash-addressed evidence containing canonical/broker symbol, timestamp, bid, ask, spread, closed bars for all four timeframes, volume provenance, session, health, quality, broker reference, and source timestamps. Identical evidence is idempotent; records are never edited after decision use.

## Feature and research architecture

`FeatureStore` persists typed feature values with `name`, `version`, `timestamp`, `instrument`, `timeframe`, `source`, `value`, `quality`, and `evidence_family`. Every calculation uses only data available at its timestamp.

Core engines are pure and separately tested:

- `MarketStructureEngine`: pivots, swings, BOS, CHoCH, support/resistance, break/retest.
- `MultiTimeframeEngine`: correlated top-down context, explicit conflict rules, selected timeframe and reason.
- `AuctionMarketEngine`: AVWAP, POC/value area, acceptance/rejection, balance/imbalance.
- `MarketRegimeEngine`: probabilistic state and uncertainty.
- `VolatilityEngine`: ATR, realized/rolling volatility, percentile and regime.
- `LiquidityEngine`: explicit evidence level and provenance.
- `MacroIntelligenceEngine`: numeric observations first.
- `GoldIntelligenceEngine`: rolling XAU relationships without fixed causal assumptions.
- `EventRiskEngine`, `NewsRiskEngine`, and `PositioningEngine`: defensive, timestamped evidence.
- Elliott/Fibonacci: disabled experimental features, shadow/A-B only.

The implemented `feature-foundation-v3` stores seven explicit evidence families and includes market structure, volatility, probabilistic baseline regime, multi-timeframe alignment, broker-volume auction context, and Level-1 MT5 liquidity. It makes no global-volume, order-book, futures-volume, probability, direction, or execution claim. Elliott and Fibonacci are configuration-gated off by default and have no implemented production authority.

### Provider-managed context boundary

`V4ContextIngestionService` assembles immutable context from a fixed trusted-source boundary:

- official FRED observations for explicitly named factors, including the broad USD index without mislabeling it DXY;
- official FRED release dates marked `DATE_ONLY`, so Atlas never invents a release time;
- a fixed HTTPS allowlist of Federal Reserve, ECB, Bank of Japan, and BIS RSS feeds;
- official CFTC Legacy Futures Only positioning for unambiguous direct commodity contracts;
- the latest persisted MT5 D1 XAU close history and deterministic liquidity state.

Every provider failure is isolated and recorded in `provider_issues`. The aggregate is `AVAILABLE`, `DEGRADED`, or `DATA_UNAVAILABLE`; it is never silently backfilled. News remains defensive risk evidence, CFTC remains non-directional positioning evidence, and the entire context contract hard-codes `signal_authority=false` and `execution_enabled=false`.

## Quant Lab validation foundation

The Phase 4 Quant Lab is separated from production authority:

- `TripleBarrierLabeler` enters on the next bar and constructs volatility/structure barriers only from observations available at the signal timestamp. STOP_FIRST is the conservative default when OHLC cannot resolve same-bar ordering.
- `WalkForwardValidator` creates expanding or rolling chronological folds, purges labels that overlap the following window, and records explicit embargo gaps. Ordinary shuffled K-fold is prohibited by contract.
- `HoldoutVault` seals the final chronological period, returns only development sample IDs through the API, exposes no holdout-open route, and permits one explicitly confirmed/logged final-evaluation access at the service layer.
- `V4BacktestEngine` models spread, adverse slippage, commission, swap, session blocks, gaps, execution delay, holding limits, and ambiguous barrier ordering with reproducible hash-addressed runs.
- `ExperimentRegistry` counts feature, model, parameter, and threshold variants but has no automatic-promotion state.
- `RobustnessValidator` uses deterministic moving-block bootstrap intervals and cost stress without independently shuffling returns. DSR and PBO remain unavailable until governed OOS cross-variant inputs satisfy their assumptions.

All Quant Lab outputs are research-only. They contain neither signal authority nor execution authority and cannot activate a model.

## Primary-model research foundation

The Phase 5 primary-model layer is deliberately bounded:

- `PrimaryMLTrainer` trains a deterministic standardized logistic-regression baseline and an extremely randomized decision-stump ensemble challenger on CPU without a paid inference or data dependency.
- Train, validation, and test windows must be chronological and disjoint. Label horizons must end before the following window starts, sample IDs must be unique, and the feature schema must match exactly.
- Immutable `PrimaryModelArtifact` records include the feature schema, model family/configuration, supported instrument/timeframe/regime scopes, input windows, raw OOS metrics, and model parameters.
- Every artifact starts `EXPERIMENTAL`. Manual promotion can create only a `SHADOW` status record and requires a named reviewer, a reason, at least 30 test observations, positive and superior net OOS performance, and persisted robustness evidence whose interval remains above zero.
- There is no service or HTTP method that can promote a V4 model to `ACTIVE`. The registry records that automatic promotion and decision influence are false.
- `PrimaryMLEngine` can emit only a `PrimaryCandidate` from a supported SHADOW artifact. Its probability is raw and explicitly uncalibrated; `atlas_probability` is null, and publication/execution authority are false.

This phase supplies governed model evidence for later phases. It does not create a trade recommendation, probability grade, Quant decision, entry, stop, or target.

## Model research architecture

### Dataset rules

- Triple-barrier labels are volatility/structure aware.
- Observation horizons are recorded so overlapping samples can be purged.
- Walk-forward folds use chronological train/validation/test windows plus embargo.
- A final holdout is sealed before tuning and accessed only for the formal release evaluation.
- Experiment registry records every feature/model/threshold variant.

### Primary and meta models

- Primary baseline: logistic regression.
- Challengers: ExtraTrees/Random Forest and LightGBM/XGBoost only if the dependency and OOS evidence justify them.
- Primary output: direction candidate and raw probability.
- Meta model output: accept/reject probability for the primary candidate under current conditions.
- Neither model can be promoted automatically.

### Calibration and Quant

Calibration is fitted only on appropriate out-of-sample/meta predictions. Platt or isotonic is chosen by sample size and validation. Store Brier, log loss, ECE, reliability bins, sample count, training window, and artifact version.

Quant validation consumes real validation artifacts: OOS sample, robustness intervals, regime stability, sensitivity, multiple-testing risk, transaction costs, drift, and calibration. It returns PASS, PASS_WITH_CAUTION, VETO, or INSUFFICIENT_EVIDENCE. VETO is absolute.

## AI review architecture

Specialized reviews run only after a qualified primary candidate exists:

- Macro reviewer interprets structured numeric macro evidence.
- Market reviewer checks candidate versus deterministic structure.
- Adversarial critic assumes the candidate is wrong and finds contradictions.
- Context summarizer compresses news and missing-data state.

All reviews use strict JSON with supports, contradictions, event risks, missing data, invalidations, and review status. Model confidence is never copied into Atlas probability. Calls are cached/deduplicated and bounded by provider budgets.

The implemented Phase 6 layer is local-only and event-driven. It reads a persisted SHADOW `PrimaryCandidate`, refuses an unknown candidate ID, and skips provider calls when raw candidate quality is below the configurable review threshold. That threshold only controls cost and is explicitly not Atlas probability. The four roles are routed to the installed local models as follows:

- Llama 3.2 1B: context summarizer;
- Ministral 3B: macro reviewer;
- Qwen 2.5 3B: market reviewer;
- Phi-4 Mini: mandatory adversarial critic.

Provider calls run concurrently with bounded tokens/timeouts and strict Pydantic/JSON-schema validation. Identical evidence is hash-addressed and deduplicated. Direction tampering or malformed/unavailable output becomes an explicit `UNAVAILABLE` specialist record and an `INCOMPLETE` bundle. Prompts treat all evidence as untrusted data to contain prompt injection. Every record hard-codes non-voting, probability-authority false, decision-authority false, zero external API cost, publication false, model-promotion false, and execution false.

## Meta-label and evidence-dependency foundation

Phase 7 adds two research-only controls:

- `EvidenceDependencyEngine` groups observations that share an upstream input or measure so RSI, a derived momentum feature, and an AI summary of that same evidence cannot masquerade as independent confirmations. It reports evidence families, connected components, redundant groups, and a redundancy ratio; it deliberately creates no confirmation score.
- `MetaLabelTrainer` asks whether an existing primary candidate should be trusted under structured conditions. Its typed inputs include raw primary probability, model disagreement, regime, volatility, spread, session, event risk, macro state, MTF conflict, data quality, AI contradictions, positioning, liquidity, and a persisted dependency-report ID.

The meta baseline is logistic regression, not an arbitrary weighted score. It trains and evaluates on purged/embargoed chronological walk-forward folds, uses a fixed 0.5 research threshold without test-set tuning, stores fold and aggregate OOS accuracy/Brier/log-loss/net-return metrics, and remains EXPERIMENTAL. Historical samples require persisted primary-candidate and dependency lineage; AI-derived inputs additionally require a persisted review bundle. The resulting `MetaPrediction` is raw and uncalibrated, has null Atlas probability, and has no decision, publication, promotion, or execution authority.

## Probability-calibration foundation

Phase 8 calibrates only persisted chronological OOS meta predictions whose labels mean exactly `P(TP before SL | validated setup and available evidence)`. Samples must reference triple-barrier labels, be validated setups, and include net-of-cost outcomes. Raw primary-model probabilities, LLM confidence, and committee votes are prohibited calibration inputs.

The engine requires at least 200 OOS samples with at least 40 TP-before-SL and 40 SL-before-TP outcomes. It uses Platt scaling from 200 through 499 samples and isotonic regression from 500 samples. Calibration fitting and evaluation use a strict chronological 70/30 split. Stored artifacts include pre/post Brier score, log loss, ECE, ten reliability bins, Wilson uncertainty intervals, and explicit audit flags for high-confidence predictions and poor calibration. Insufficient evidence produces `INSUFFICIENT_EVIDENCE` with a null probability rather than a fallback estimate. Even an available `CalibratedProbability` has `grade=null`, `quant_validated=false`, `publication_eligible=false`, and `execution_enabled=false` until later authority stages pass.

## Quant statistical authority

Phase 9 implements the non-LLM `QuantValidationEngine`. It resolves persisted lineage from calibrated probability through calibration, meta prediction/model, primary candidate, robustness, regime stability, prediction drift, cost-aware backtest, and the current MT5 snapshot. Eleven named checks cover OOS evidence, sample size, calibration, robustness, regime stability, multiple-testing risk, data quality, model drift, uncertainty, transaction costs, and net-return evidence.

`RegimeStabilityEngine` derives per-regime sample counts, TP-before-SL rates, mean net returns, worst-regime return, and dispersion directly from chronological meta-model OOS records. `PredictionDriftEngine` compares persisted current meta predictions with the model's OOS reference using PSI and mean-probability shift, and requires the current window to begin after reference labels end. DSR/PBO are required only when multiple strategy variants make them statistically appropriate; missing evidence then yields `INSUFFICIENT_EVIDENCE` rather than a fabricated statistic.

Final precedence is deterministic: any failed hard check produces `VETO`; otherwise missing evidence produces `INSUFFICIENT_EVIDENCE`; otherwise caution produces `PASS_WITH_CAUTION`; only all-pass evidence produces `PASS`. A VETO cannot be overridden by AI or probability. Quant validation is statistical authority, but Phase 9 still cannot assign a grade, construct entry/SL/TP, publish a signal, or submit an order.

## Expectancy and risk authority

Phase 10 implements the deterministic, non-LLM risk chain. `ExpectancyEngine` computes net expectancy from the calibrated TP-before-SL probability, historical gross wins/losses, and observed spread, slippage, commission, and swap friction. It requires sufficient two-class evidence and rejects non-positive net expectancy.

`StructuralTargetEngine` builds an entry zone, structural invalidation, and three targets from support/resistance, swing, auction, and ATR evidence. Stops are not fixed-pip values, cannot exceed the 2.5 ATR hard cap, and targets must satisfy at least 1.5R, 2R, and 3R. Elliott Wave and Fibonacci have no production role.

`PortfolioExposureEngine` measures currency-factor and commodity exposure, rolling correlation, portfolio heat, and USD concentration. `RiskOverlay` evaluates thirteen named checks covering Quant, expectancy, targets, daily and weekly drawdown, consecutive losses, heat, factor concentration, correlation, data, model health, event risk, and liquidity. Any hard failure produces immutable `RISK_VETO` with zero approved risk. Calibrated probability can only reduce or modestly scale risk inside policy caps; it cannot override a veto. Phase 10 still cannot assign a probability grade, publish a canonical signal, or submit an order.

## Publication and risk architecture

`SignalPublicationGate` is the sole constructor of canonical publishable signals. Required inputs:

- calibrated probability >= 0.80;
- Quant PASS (PASS_WITH_CAUTION policy must be explicit and cannot masquerade as PASS);
- positive net expectancy after costs;
- acceptable data, event, spread, liquidity, model health, and portfolio exposure;
- Risk Overlay PASS;
- validated structural stop and targets.

Otherwise the decision is one of WAIT, NO_TRADE, QUANT_VETO, RISK_VETO, EVENT_BLOCK, DATA_DEGRADED, INSUFFICIENT_DATA, or MODEL_UNCERTAIN.

Phase 11 implements this gate as the sole constructor of `CanonicalSignal`. It resolves and cross-checks persisted primary, meta, calibration, Quant, expectancy, target, exposure, risk, context, feature, snapshot, and optional non-voting AI lineage. Rejection precedence preserves Quant VETO and distinguishes stale data, event blocks, risk vetoes, insufficiency, model uncertainty, meta rejection, negative expectancy, and sub-80% WAIT states. Grades are assigned only after every gate passes, at exact boundaries: B from 80%, A from 85%, A+ from 90%, and A+++++ from 95%.

Published analytical signals contain immutable entry, stop, TP1/TP2/TP3, probability, grade, expected R, approved bounded risk, evidence drivers/contradictions/invalidations, artifact lineage, versions, creation, and expiry. Corrections create append-only `SignalRevision` records and never mutate the original signal. Publication is Atlas research authority, not broker execution: every signal and decision hard-codes execution and order submission false.

## Immutable signal and tracking architecture

A published `Signal` references every lineage artifact. Entry, stop, targets, calibrated probability, grade, model versions, creation, and expiry are immutable. Corrections create `SignalRevision` records.

Phase 12 implements `SignalTrackingEngine` server-side from validated MT5 snapshots. It uses ask for long entry and bid for short entry, then the executable exit side for live outcomes. It stores append-only events for pending entry, activation, TP1/TP2/TP3, stop, expiry, invalidation, cancellation, and event/data/risk cancellation, while the server-owned projection records MFE, MAE, live R, pips, target/stop distance, spread, and elapsed time. Reusing a snapshot is idempotent, terminal states cannot be rewritten, and browser authority and execution remain false.

Phase 13 adds `V4PresentationService` as a read-only projection over persisted V4 records and makes the root Vinext route the canonical V4 terminal. Command Center, Markets, Signals, Atlas AI, Calendar, Performance, Quant Lab, Models, MT5, and Settings consume one typed workspace contract. Missing or corrupt evidence is reported as `DATA_UNAVAILABLE` or `DEGRADED`; the presentation service and browser never substitute demo prices, legacy decisions, probabilities, grades, outcomes, or performance. The browser has neither signal nor tracking authority.

Phase 14 adds `V4PerformanceService`, a persisted-evidence projection for reliability bins, calibration diagnostics, performance slices by instrument/timeframe/regime, deterministic backtest costs and drawdown, experiment registration, robustness/Monte Carlo results, DSR/PBO availability, model comparisons, and publication/veto analytics. Each row carries its stored run, signal, tracking, calibration, model, or publication-attempt identifiers plus sample counts. Missing evidence remains `DATA_UNAVAILABLE`; the service never converts absence into a zero metric or a fabricated chart. Research artifacts cannot promote a model, publish a signal, or submit a broker order.

Phase 15 hardens that completed chain. A loopback-only middleware boundary adds mutation request-size and rate bounds, optional constant-time admin-token authorization, security/no-store headers, and explicit execution-disabled headers. `OperationalHealthService` reports PostgreSQL, Redis, MT5/data, governed-model, and exact four-model Ollama inventory states without granting any component decision authority. Analytical signal authority remains exclusively server-side in `SignalPublicationGate`; browser authority and broker order placement remain false.

`SignalLineageService` reconstructs the canonical signal and its publication, MT5 snapshot, features, context, primary candidate, optional AI review, meta prediction/model, calibration evidence, Quant validation, backtest, expectancy, targets, exposure, risk overlay, revisions, lifecycle state, and append-only events. The browser is a subscriber only.

## Persistence and migrations

Alembic now owns the schema baseline. The first migration safely adopts existing SQLModel tables, creates measured PostgreSQL monthly price partitions, preserves existing rows, and blocks destructive downgrade. Migration policy:

- additive by default;
- no drop/rename of existing data in early phases;
- backfill with explicit version/provenance;
- immutable evidence and decision tables;
- uniqueness constraints for natural/idempotency keys;
- indexes on instrument/timeframe/timestamp/status/model version;
- PostgreSQL partitions only where measured volume warrants them.

## API architecture

Version V4 routes under `/api/v4` on FastAPI. Presentation routes in the Vinext app proxy only when necessary.

Initial endpoints:

```text
GET  /api/v4/health
GET  /api/v4/health/operational?instrument={instrument}
GET  /api/v4/health/mt5
GET  /api/v4/health/data
GET  /api/v4/terminal?instrument={instrument}
GET  /api/v4/market/{instrument}/snapshot
POST /api/v4/market/{instrument}/snapshot
POST /api/v4/context/{instrument}/compute
POST /api/v4/context/{instrument}/refresh
GET  /api/v4/context/{instrument}/latest
POST /api/v4/quant/labels
POST /api/v4/quant/walk-forward
POST /api/v4/quant/backtests
POST /api/v4/quant/robustness
POST /api/v4/quant/experiments
POST /api/v4/quant/holdouts/seal
POST /api/v4/models/train
GET  /api/v4/models/{model_id}
GET  /api/v4/models/{model_id}/status
POST /api/v4/models/{model_id}/shadow
POST /api/v4/models/candidates
POST /api/v4/ai/reviews
GET  /api/v4/ai/reviews/{bundle_id}
POST /api/v4/evidence/dependencies
POST /api/v4/meta/train
GET  /api/v4/meta/models/{meta_model_id}
POST /api/v4/meta/predictions
POST /api/v4/calibration/train
GET  /api/v4/calibration/{calibration_id}
POST /api/v4/calibration/apply
POST /api/v4/quant/regime-stability
POST /api/v4/quant/drift
POST /api/v4/quant/validate
POST /api/v4/risk/expectancy
POST /api/v4/risk/targets
POST /api/v4/risk/exposure
POST /api/v4/risk/overlay
POST /api/v4/signals/evaluate
GET  /api/v4/signals/{signal_id}
POST /api/v4/signals/{signal_id}/revisions
GET  /api/v4/signals/{signal_id}/revisions
POST /api/v4/signals/{signal_id}/track
GET  /api/v4/signals/{signal_id}/tracking
GET  /api/v4/signals/{signal_id}/events
POST /api/v4/signals/{signal_id}/control
GET  /api/v4/signals/{signal_id}/lineage
```

There is deliberately no API for opening a sealed holdout, promoting a model to ACTIVE, bypassing the publication gate, or submitting an order. The SHADOW route records an explicitly manual research review and cannot grant decision authority. Phase 14 metrics and Phase 15 operational health are exposed through read-only projections. Responses are typed, no-store where live, and never include secrets.

## UI migration architecture

Keep the existing visual system. Introduce screens only when their authoritative data exists:

1. Command Center and MT5/data health.
2. Markets scanner and MTF matrix.
3. Signals/WAIT decisions and immutable history.
4. Calendar and event risk.
5. Performance/calibration.
6. Quant Lab and Models.
7. Grounded Atlas AI.
8. Settings/cost/health.

Mobile uses the same data contracts with a bottom navigation. No flashing actions, gamification, or fabricated placeholder production metrics.

## Security and operations

- Bind all local services to loopback by default.
- Secrets remain environment/server-side and are redacted from errors/logs.
- V4 mutation requests have explicit size bounds, schema validation, process-local rate limiting, and optional admin-token authorization. A multi-user deployment would still require a distributed identity and rate-limit layer.
- Log structured request IDs, snapshot IDs, pipeline latency, provider/model latency, cost, and gate decisions.
- Docker remains optional infrastructure for PostgreSQL/Redis; MT5 itself stays a Windows host dependency.
- Execution and paper execution remain disabled during V4 construction.

## Truthfulness rule

Unavailable evidence is `UNKNOWN`, `DATA_UNAVAILABLE`, or `INSUFFICIENT_DATA`. Demo data may power tests/demos only and must carry a non-production provenance that the publication gate rejects.
