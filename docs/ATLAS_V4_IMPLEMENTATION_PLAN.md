# ATLAS V4 implementation plan

Plan date: 2026-08-20  
Migration style: incremental, fail-closed, research-first, no destructive replacement.

## Working rules

1. Preserve the working terminal and MT5 bridge throughout migration.
2. Build the new authority in FastAPI under versioned contracts.
3. Run every new engine in shadow/research mode until its validation gate passes.
4. Never convert alignment, LLM confidence, or desk consensus into displayed probability.
5. Never promote a model or feature automatically.
6. Every phase ends with tests, documentation, and an honest status update.
7. Paper/live order submission remains disabled for this implementation.

## Phase 0 — audit and controls

Status: COMPLETE.

Deliverables:

- `docs/ATLAS_V4_GAP_ANALYSIS.md`
- `docs/ATLAS_V4_ARCHITECTURE.md`
- this plan
- pre-V4 verification baseline
- explicit preserve/retire/refactor decisions

Exit gate: the active route is acknowledged as uncalibrated research and no V4 completion claim is made.

## Phase 1 - MT5 and data quality

Status: COMPLETE (research-only foundation, verified 2026-08-19).

Deliverables:

- Canonical V4 instruments/timeframes and `SymbolMappingService`.
- Typed loopback-only `MT5Gateway` with bounded errors and no execution protocol.
- `MT5HealthMonitor` with V4 states, latency, tick age, account mode, and symbol availability.
- `MT5MarketDataService` for quote, broker metadata, ticks, and four closed-bar series.
- `DataQualityEngine` with score/status/issues and critical veto semantics.
- Immutable/hash-addressed `MarketSnapshot` contract and persistence service.
- `/api/v4/health/mt5`, `/api/v4/health/data`, and market snapshot endpoints.
- Unit/integration/failure tests for stale ticks, impossible/crossed quotes, missing/duplicate/gapped bars, symbol mismatch, market closed, bridge failure, and real MT5 smoke behavior.

Exit gate: a stale/invalid MT5 snapshot cannot progress beyond the quality layer, and no fallback fabricates production evidence.

Evidence: focused tests cover the required failure modes; strict typing/lint pass; a live MT5 DEMO capture produced four complete closed-bar series, 1,000 ticks, `HEALTHY` quality score 100, immutable snapshot
`2da2bf9dd0aeea16e1fc0703a2a0ccbf15d24b6d10c6caaa332ddc7221d47cbd`, and `execution_enabled=false`.

## Phase 2 - feature foundation

Status: COMPLETE (deterministic shadow/research layer, verified 2026-08-19).

Deliverables:

- Versioned FeatureStore and evidence-family tags.
- Deterministic MarketStructureEngine.
- MultiTimeframeEngine with conflict/tie-break rules and evidence dependency handling.
- VolatilityEngine and probabilistic MarketRegimeEngine.
- AuctionMarketEngine and tiered LiquidityEngine with volume provenance.
- Elliott/Fibonacci flags disabled by default and shadow-only.

Exit gate: every feature is reproducible from a snapshot, versioned, quality-tagged, and leakage-tested.

Evidence: `feature-foundation-v3` reproducibly generated 144 immutable records from the healthy live snapshot with explicit STRUCTURE, AUCTION, VOLATILITY, VOLUME, REGIME, MULTI_TIMEFRAME, and LIQUIDITY families. Higher-tier liquidity stayed unavailable, as required, because the stored snapshot did not preserve a measurable tick-window start. Focused tests, Ruff, and strict mypy pass. `signal_authority=false` and `execution_enabled=false` remain hard contract fields.

## Phase 3 - macro, events, positioning

Status: COMPLETE (research-only context foundation, verified 2026-08-19).

Deliverables:

- Numeric-first MacroIntelligenceEngine.
- GoldIntelligenceEngine with rolling relationship estimation.
- Defensive NewsRiskEngine with deduplication.
- EventRiskEngine and calendar records.
- CFTC PositioningEngine.
- Derivatives provider interface returning DATA_UNAVAILABLE without a valid source.
- Provider-managed refresh using official FRED numeric observations/release dates, fixed official central-bank/BIS RSS feeds, CFTC public positioning, and persisted MT5/feature lineage.
- Explicit provider availability/issues and date-only event precision; no fabricated release time, DXY label, options metric, or news direction.

Exit gate: missing/late context is explicit and HIGH/EXTREME events can veto candidates.

Evidence: numeric macro statistics, rolling 20/60/120-observation gold relationships, defensive deduplicated news risk, event proximity/importance/liquidity risk, non-directional CFTC positioning statistics, and an honest unavailable derivatives provider are implemented. Sixteen focused tests, Ruff, and strict mypy pass. Live read-only verification parsed 87 recent official items across the Federal Reserve, ECB, Bank of Japan, and BIS feeds with zero adapter errors; the official CFTC public dataset returned two current GOLD records. FRED adapters are contract-tested and remain explicitly unavailable until a valid free 32-character `FRED_API_KEY` is configured. Provider refresh is configuration-gated and cannot publish a signal or submit an order.

## Phase 4 - Quant Lab validation infrastructure

Status: COMPLETE (research-only validation foundation, verified 2026-08-19).

Deliverables:

- Volatility/structure-aware TripleBarrierLabeler.
- Purged, embargoed rolling/expanding WalkForwardValidator.
- Sealed untouched holdout policy and access logging.
- Deterministic cost-aware BacktestEngine.
- Experiment/variant registry.
- RobustnessValidator with block/bootstrap and stress tests.
- DSR/PBO only where sample assumptions are met.

Exit gate: fold boundaries prove no label overlap leakage, every run is reproducible, and holdout data is not repeatedly inspected.

Evidence: the next-bar `TripleBarrierLabeler` records its input cutoff and proves future bars cannot alter barrier geometry; conservative same-bar collisions are explicit. Expanding/rolling validation tests prove all retained train and validation labels end before the next window, with separate purged and embargoed IDs. The sealed holdout API never returns holdout samples and no open endpoint exists; the service permits one confirmed final evaluation and logs it. Cost-aware backtests are hash-addressed/idempotent and cover adverse spread/slippage, commission, swap, sessions, gaps, delay, timeout, and barrier ordering. Moving-block bootstrap is seeded/reproducible and refuses individual-return shuffling. DSR/PBO are explicitly unavailable until their governed OOS matrices meet assumptions. Ten focused tests and the full backend gate pass.

## Phase 5 - primary ML

Status: COMPLETE (governed research-only model foundation, verified 2026-08-19).

Deliverables:

- Logistic-regression baseline.
- ExtraTrees/Random Forest challenger; LightGBM/XGBoost optional.
- Model artifacts, feature schema, supported universe/regimes, latency, and OOS metrics.
- Model registry states EXPERIMENTAL/SHADOW/ACTIVE/DEGRADED/RETIRED.

Exit gate: only a model with superior net OOS evidence enters SHADOW; none is automatically ACTIVE.

Evidence: `PrimaryMLTrainer` reproducibly trains a standardized logistic-regression baseline and an extremely randomized stump-ensemble challenger with exact feature-schema and chronological label-horizon validation. Immutable artifacts record model configuration, supported scope, disjoint train/validation/test windows, raw OOS metrics, and parameters. All models start EXPERIMENTAL. A named manual review may create SHADOW status only after at least 30 OOS test samples, positive and superior net OOS performance, and persisted positive robustness evidence. No ACTIVE service/API method exists. SHADOW inference returns a raw, explicitly uncalibrated candidate with `atlas_probability=null`, `publication_authority=false`, and `execution_enabled=false`. Five focused Phase 5 tests pass. The full gate passes 88 backend tests (87 passed and one optional Redis integration skipped), Ruff, strict mypy across 71 source files, 87% coverage, 52 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 6 - non-voting AI review

Status: COMPLETE (local structured review foundation, verified 2026-08-19).

Deliverables:

- AIReviewOrchestrator with macro, market, adversarial, and context roles.
- Strict common output contract.
- Event-driven calls after candidate qualification.
- Cache, deduplication, timeouts, provider budgets, usage/cost ledger.
- Removal of sentiment consensus and desk arbitration from decision authority.

Exit gate: tests prove unanimous AI cannot bypass weak primary evidence or any later veto.

Evidence: `AIReviewOrchestrator` accepts only a persisted SHADOW candidate and bounded structured evidence. Raw candidate quality below the configured event threshold causes `SKIPPED` without a provider call. Llama 3.2 1B, Ministral 3B, Qwen 2.5 3B, and Phi-4 Mini have distinct context, macro, market, and mandatory adversarial roles; calls are concurrent, token/timeout bounded, hash-deduplicated, and local Ollama only. Strict output records contain supports, contradictions, event risks, missing data, invalidations, and review status. Direction tampering and malformed/unavailable output fail closed. Five focused tests prove four-role routing, mandatory criticism, disabled/unavailable states, deduplication, prompt-data isolation, typed API persistence, and absence of an AI decision route. Every bundle keeps `atlas_probability=null`, `publication_eligible=false`, `model_promotion_authority=false`, and `execution_enabled=false`. The full gate passes 93 backend tests (92 passed and one optional Redis integration skipped), Ruff, strict mypy across 73 source files, 88% coverage, 52 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 7 - meta-labeling

Status: COMPLETE (research-only trust-model foundation, verified 2026-08-19).

Deliverables:

- Meta-label dataset and model.
- Inputs include primary output, disagreements, regime, spread, session, events, macro, MTF conflict, quality, review contradictions, positioning, and liquidity.
- Purged walk-forward validation and registry artifact.

Exit gate: reject/accept behavior is OOS validated and materially improves net decisions without threshold overfitting.

Evidence: `EvidenceDependencyEngine` uses shared upstream lineage to collapse repeated representations into connected components and reports redundancy without creating an arbitrary confirmation score. Typed meta features cover all required candidate, regime, cost, session, event, macro, MTF, quality, AI, positioning, and liquidity inputs. `MetaLabelTrainer` uses logistic regression with purged/embargoed chronological walk-forward folds and a fixed untuned 0.5 threshold; it records fold and aggregate OOS Brier, log loss, balanced accuracy, accept-all return, filtered return, and net improvement. Training and prediction require persisted primary-candidate/dependency lineage, and AI contradiction inputs require a persisted review. The artifact stays EXPERIMENTAL with no promotion route. Five focused tests prove reproducibility, OOS net improvement on a controlled dataset, leakage/lineage rejection, raw non-publishable predictions, typed persistence APIs, and absence of a promotion endpoint. The full gate passes 98 backend tests (97 passed and one optional Redis integration skipped), Ruff, strict mypy across 75 source files, 88% coverage, 52 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 8 - calibration

Status: COMPLETE (research-only probability-calibration foundation, verified 2026-08-19).

Deliverables:

- Platt/isotonic selection policy.
- Calibration artifacts and reliability bins.
- Brier, log loss, ECE, sample counts, and confidence intervals.
- Exact probability semantics: TP before SL conditional on the validated setup.

Exit gate: 80% means observed reliability within documented uncertainty; otherwise probability stays unavailable.

Evidence: `ProbabilityCalibrationEngine` accepts only the persisted chronological OOS predictions from a meta-model artifact, whose labels are lineage-bound triple-barrier outcomes with exact semantics `P(TP before SL | validated setup and available evidence)`. It requires at least 200 samples, 40 TP-before-SL outcomes, and 40 SL-before-TP outcomes; otherwise it stores `INSUFFICIENT_EVIDENCE` and returns no probability. The governed policy selects Platt scaling for 200-499 samples and isotonic regression from 500 samples, fits on the first 70% chronologically, and evaluates only on the later 30%. Artifacts store pre/post Brier score, log loss, ECE, ten reliability bins, Wilson intervals, uncertainty notes, and calibration-audit flags. Raw primary probability, LLM confidence, and committee voting are explicitly prohibited inputs. Calibrated outputs still have no grade, Quant validation, publication eligibility, or execution authority. Five focused Phase 8 tests pass. The full gate passes 103 backend tests (102 passed and one optional Redis integration skipped), Ruff, strict mypy across 77 source files, 87% coverage, 52 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 9 - Quant validation

Status: COMPLETE (final statistical-authority foundation, verified 2026-08-20).

Deliverables:

- QuantValidationEngine consuming real OOS/calibration/robustness/drift/cost artifacts.
- PASS/PASS_WITH_CAUTION/VETO/INSUFFICIENT_EVIDENCE.
- Multiple-testing and regime-stability checks.

Exit gate: VETO is immutable and overrides all AI/primary/meta confidence.

Evidence: `RegimeStabilityEngine` computes per-regime OOS sample counts, TP-before-SL rates, net returns, worst-regime return, and dispersion from persisted meta-model OOS lineage. `PredictionDriftEngine` compares persisted current meta predictions with the strictly earlier OOS reference using PSI and mean-probability shift. `QuantValidationEngine` resolves the full persisted chain and emits exactly eleven checks for OOS evidence, sample size, calibration, robustness, regime stability, multiple testing, data quality, model drift, uncertainty, transaction costs, and net-return evidence. Decision precedence is hard-coded as VETO before INSUFFICIENT_EVIDENCE before PASS_WITH_CAUTION before PASS. Multiple variants fail closed when governed DSR/PBO evidence is absent. A calibrated probability and unanimous AI review cannot override VETO. Five focused tests cover all-pass evidence, grounded regime/drift calculations, absolute veto precedence, multiple-testing insufficiency, typed APIs, and absence of publish/order routes. The full gate passes 108 backend tests (107 passed and one optional Redis integration skipped), Ruff, strict mypy across 79 source files, 87% coverage, 52 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 10 - expectancy and risk

Status: COMPLETE (deterministic risk-authority foundation, verified 2026-08-20).

Deliverables:

- Net ExpectancyEngine with spread/slippage/commission/swap.
- StructuralTargetEngine.
- PortfolioExposureEngine for currency factors, commodity exposure, correlation, heat, and USD concentration.
- Hard RiskOverlay and conservative sizing.

Exit gate: non-positive EV or any risk veto blocks publication.

Evidence: `ExpectancyEngine` computes the exact cost-adjusted expectancy `P(win) * AvgWin - P(loss) * AvgLoss - Costs` from persisted calibrated probability and historical net/gross outcomes, with minimum total and per-class sample requirements. `StructuralTargetEngine` uses entry zones, support/resistance, swing, auction, and ATR evidence to create a structural invalidation and TP1/TP2/TP3 at minimum 1.5R/2R/3R; fixed-pip, Elliott, and Fibonacci geometry is prohibited. `PortfolioExposureEngine` detects repeated currency and commodity factors, excessive rolling correlation, portfolio heat, and USD concentration. `RiskOverlay` evaluates thirteen named checks and makes any hard failure an immutable `RISK_VETO` with zero approved risk; probability can affect sizing only inside conservative policy caps. Four typed risk endpoints persist the resulting artifacts, but no signal or order route exists. Five focused Phase 10 tests pass. The full gate passes 113 backend tests (112 passed and one optional Redis integration skipped), Ruff, strict mypy across 81 source files, 86% coverage, 52 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 11 - canonical signal engine

Status: COMPLETE (canonical analytical authority, verified 2026-08-20).

Deliverables:

- One canonical versioned Signal schema.
- Single authoritative SignalPublicationGate.
- Exact B/A/A+/A+++++ thresholds.
- Explicit WAIT/NO_TRADE/QUANT_VETO/RISK_VETO/EVENT_BLOCK/DATA_DEGRADED/INSUFFICIENT_DATA/MODEL_UNCERTAIN results.
- Immutable lineage and revisions.

Exit gate: all critical acceptance cases A-F pass end to end.

Evidence: `SignalPublicationGate` is the sole V4 constructor of a versioned `CanonicalSignal`. It validates the complete persisted lineage, requires calibrated probability at or above 80%, exact Quant PASS, positive net expectancy, healthy MT5 evidence, no event veto, valid structural geometry, acceptable exposure, and hard risk PASS. It returns PUBLISHED or one explicit WAIT/NO_TRADE/QUANT_VETO/RISK_VETO/EVENT_BLOCK/DATA_DEGRADED/INSUFFICIENT_DATA/MODEL_UNCERTAIN state. B/A/A+/A+++++ are assigned only to published signals at exact 80/85/90/95 boundaries. Optional AI evidence remains non-voting and cannot override meta, Quant, data, expectancy, or risk results. Canonical geometry and lineage are frozen; revisions are append-only annotations/corrections/retractions that do not mutate the original. Seven focused tests pass acceptance cases A-F, exact boundaries, successful publication, immutable revisions, typed APIs, and absence of an order route. The full gate passes 120 backend tests (119 passed and one optional Redis integration skipped), Ruff, strict mypy across 83 source files, 86% coverage, 52 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 12 - server-side MT5 tracking

Status: COMPLETE.

Deliverables:

- SignalTrackingEngine using validated MT5 ticks.
- Pending/active/TP/stop/expiry/cancellation events.
- MFE, MAE, pips, R, distance, elapsed time.
- Append-only outcomes and browser subscription.

Exit gate: acceptance case G persists TP1 server-side and updates the UI after reload.

Evidence: `SignalTrackingEngine` accepts only persisted canonical signals and post-publication, validated MT5 snapshots. Entry uses the executable ask for LONG and bid for SHORT; outcomes use the opposite executable side. Pending, active, TP1/TP2/TP3, stop, expiry, invalidation, cancellation, event cancellation, data cancellation, and risk cancellation are append-only audited events. A server-owned projection records live price, spread, pips, R, MFE, MAE, stop/target distance, and elapsed time; repeated snapshots are idempotent and terminal history cannot be rewritten. `SignalLineageService` reconstructs every publication, market, feature, context, model, calibration, Quant, backtest, expectancy, target, exposure, risk, revision, and tracking artifact without exposing hidden chain-of-thought. Five focused tests pass acceptance case G, reload persistence, stop ordering, data cancellation, audited control, full lineage, typed APIs, and absence of an order route. The full gate passes 125 backend tests (124 passed and one optional Redis integration skipped), Ruff, strict mypy across 85 source files, 86% coverage, 52 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 13 - V4 UI/UX

Status: COMPLETE.

Deliverables:

- Desktop left navigation, top status, workspace, optional intelligence panel.
- Mobile top/bottom navigation.
- Command Center, Markets, Signals, Atlas AI, Calendar, Performance, Quant Lab, Models, MT5, Settings.
- Market scanner, full MTF matrix, flagship signal/WAIT states, event countdown, immutable history, live track, structured explanations.

Exit gate: desktop/mobile accessibility and browser E2E pass with no fabricated production metrics.

Evidence: `V4PresentationService` builds a typed, read-only 15-market workspace exclusively from persisted V4 artifacts and records missing/corrupt sources without falling back to legacy or demo data. The active root route now renders Command Center, Markets, Signals, Atlas AI, Calendar, Performance, Quant Lab, Models, MT5, and Settings with desktop navigation, mobile bottom navigation, explicit 15M/1H/4H/1D evidence, exact canonical grades, immutable signal history, server tracking, Quant/risk status, and fail-closed unavailable states. The Vinext proxy accepts only loopback V4 backend URLs and exposes no mutation or order method. Four focused presentation tests and three frontend policy tests pass. Browser E2E verified all ten workspaces, 15 market rows, instrument switching, responsive 390x844 layout without page overflow, explicit empty evidence, and zero console warnings/errors. The full gate passes 129 backend tests (128 passed and one optional Redis integration skipped), Ruff, strict mypy across 87 source files, 86% coverage, 55 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 14 - performance and Quant dashboards

Status: COMPLETE.

Deliverables:

- Reliability/calibration dashboard.
- Performance by instrument/timeframe/regime.
- Experiments, A/B, walk-forward, Monte Carlo, DSR/PBO, model comparisons, shadow results.
- Cost and veto analytics.

Exit gate: every chart is traceable to stored run IDs and sample counts.

Evidence: `V4PerformanceService` reads only persisted canonical signals, server tracking states, calibration artifacts, deterministic backtests, robustness runs, experiment records, governed model artifacts/statuses, and publication attempts. It publishes typed performance slices for all signals and by instrument, timeframe, and regime; calibration diagnostics and reliability bins; cost drag, drawdown, profit factor, and net returns; experiment, robustness, DSR/PBO, and model comparison evidence; and veto/decision frequencies. Every metric carries sample counts and stored trace identifiers. Missing evidence stays `DATA_UNAVAILABLE` or `PARTIAL`; corrupt rows are omitted and reported, and the browser performs no authoritative calculation. Performance and Quant Lab now render these records on desktop and mobile without fake charts or zero-filled claims. Four focused Phase 14 tests pass. The full gate passes 133 backend tests (132 passed and one optional Redis integration skipped), Ruff, strict mypy across 89 source files, 86% coverage, 55 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Phase 15 - hardening

Status: COMPLETE (verified 2026-08-21).

Deliverables:

- Alembic migration validation and recovery procedure.
- Security, secret, authorization, input/rate-limit, and log-redaction audits.
- Health endpoints for MT5/data/models/LLM/database.
- Failure/reconnect/provider-degradation tests.
- Docker PostgreSQL/Redis build/startup verification.
- Full final report with COMPLETE/PARTIAL/NOT IMPLEMENTED statuses.

Exit gate: all final verification commands pass without weakened tests and documentation matches observed behavior.

Evidence: Alembic `20260820_0001` safely adopted the live PostgreSQL schema, reached head idempotently, preserved seeded evidence, created supported price partitions, and rejected destructive downgrade. Backend startup now validates migrations first. The security boundary enforces loopback-only access, mutation request-size and rate bounds, optional constant-time admin authorization, secret redaction, no-store/security headers, and an explicit execution-disabled response. The operational health endpoint truthfully probes database, Redis, MT5/data, governed-model registry, and exact four-model Ollama inventory. All four configured Ollama endpoints also completed real generation requests. PostgreSQL and Redis were observed live on loopback and `/ready` reported both healthy; Compose uses persistent volumes, loopback bindings, required secrets, health checks, official images, and no build context. Direct Docker CLI invocation was blocked by the Codex sandbox, so no CLI build claim is made. The full gate passes 142 backend tests (141 passed and one optional Redis integration skipped), Ruff, strict mypy across 92 source files, 86% coverage, 55 TypeScript tests, 29 legacy Python tests, typecheck, ESLint, Vinext production build, and rendered-terminal verification.

## Critical acceptance test mapping

| Case | Required test owner | Expected outcome |
|---|---|---|
| A: calibrated 78% | Publication gate | NO SIGNAL |
| B: Quant veto at 88% | Quant/publication | NO TRADE |
| C: negative EV at 86% | Expectancy/publication | NO TRADE |
| D: stale MT5 at 90% | Data quality | DATA DEGRADED, NO SIGNAL |
| E: AI bullish, meta rejects | Meta/publication | NO TRADE |
| F: all AI bullish, Quant veto | Quant/publication | NO TRADE |
| G: MT5 reaches TP1 | Server tracking | Persist TP1_HIT and update UI |
| H: in-sample winner fails OOS | Model registry | DO NOT PROMOTE |

## Current implementation checkpoint

Phases 0 through 15 are complete. V4 has replaced the legacy Atlas signal procedure on the canonical product route rather than remaining a sidecar. The chain reaches an authoritative analytical `CanonicalSignal` only after primary, optional non-voting AI, meta, calibration, Quant, expectancy, structural, exposure, data, event, and risk gates pass, then tracks its lifecycle from validated MT5 evidence entirely on the server. Rejections are explicit and ungraded; published signals use exact calibrated boundaries and immutable lineage. The browser renders persisted server decisions, tracking, calibration, performance, experiment, cost, robustness, model-comparison, veto, and operational-health evidence and cannot calculate authority. Server analytical signal authority is enabled. Paper and live broker orders remain disabled.
