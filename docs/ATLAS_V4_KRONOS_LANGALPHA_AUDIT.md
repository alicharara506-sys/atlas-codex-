# ATLAS V4 Kronos and LangAlpha audit

Audit date: 2026-08-25  
Atlas root: `CLAUDE_ATLAS_TRADING_TERMINAL/atlas-delivery`  
Reference projects:

- Kronos: <https://github.com/shiyu-coder/Kronos>
- LangAlpha: <https://github.com/ginlix-ai/LangAlpha>

## Executive decision

Atlas already has an authoritative V4 path for MT5 snapshots, deterministic features, numeric context, governed primary models, non-voting AI reviews, a meta-label model, calibration, Quant validation, expectancy, structural geometry, risk overlay, canonical publication, immutable tracking, and performance presentation.

The repository therefore must not be replaced with either reference project. The safe migration is additive:

1. Add Kronos as a zero-authority shadow specialist.
2. Accumulate and score Kronos outcomes before any weighting discussion.
3. Add persistent instrument workspaces as projections over Atlas's immutable evidence store.
4. Add progressive tool discovery and provider routing around the existing non-voting review layer.
5. Add explicit evidence-family redundancy and incremental-value comparisons.
6. Add a cost ledger and operator dashboards only after the underlying records exist.

## Reference-source findings

### Kronos

The official repository is MIT-licensed. `Kronos-base` is published as a 102.3M-parameter model with a 512-bar context window. The public `KronosPredictor` accepts OHLC plus optional volume/amount and performs input-window normalization before autoregressive inference.

Important integration facts:

- The public predictor's `sample_count` dimension is averaged before the forecast DataFrame is returned. Atlas cannot treat that averaged path as a probabilistic ensemble.
- The Atlas adapter therefore preserves individual scenarios by running seeded one-sample forecasts in a background-only process. This is slower but statistically honest.
- The official fine-tuning example is explicitly not a production quantitative strategy.
- The published model's pretraining temporal cutoff is not clearly established in the repository documentation. An open upstream issue requests this information: <https://github.com/shiyu-coder/Kronos/issues/265>.
- Until the pretraining cutoff is independently verified for an evaluation period, Atlas must not label Kronos evaluation as a clean unseen OOS test.
- Zero-shot shadow evaluation must precede any fine-tuning.

### LangAlpha

The official repository is Apache-2.0-licensed. The useful architectural patterns for Atlas are:

- persistent workspaces and long-lived research state;
- progressive tool discovery;
- provider-neutral model routing;
- programmatic computation before LLM interpretation;
- durable provenance for external evidence and tool calls.

Atlas must not copy LangAlpha's product scope, equity research behavior, chat-centered authority, or hosted-service assumptions. Atlas's Quant Validator remains deterministic and superior to every LLM or agent.

## License and reuse decision

No Kronos or LangAlpha source code was copied into Atlas in the initial integration slice. Atlas contains its own typed adapter around a separately installed, pinned official Kronos checkout. If the external runtime is distributed with Atlas later, include the corresponding MIT notice and record the exact source revision. Any future adapted LangAlpha code must preserve the Apache-2.0 notice and document changes.

## Existing Atlas capabilities to preserve

- Read-only MT5 market-data boundary and explicit `order_submission=false`.
- Exact 15M, 1H, 4H, and 1D timeframes.
- Immutable snapshot and feature lineage.
- Data-quality and freshness gates.
- Governed primary and meta models.
- Calibrated probability artifacts.
- Deterministic Quant veto, expectancy, and risk gates.
- Non-voting local LLM reviews.
- Canonical BUY/SELL/WAIT publication with the 80% minimum.
- Server-side signal tracking and immutable revisions.
- PostgreSQL-backed evidence store and operational health surface.

## Gap classification against the new master prompt

| Capability | Status after this slice | Decision |
|---|---|---|
| Kronos typed output vector | Implemented | Full distribution, tails, MAE/MFE, optional barrier ordering |
| Kronos scenario generation | Implemented adapter boundary | Official runtime remains optional and separately installed |
| Kronos shadow persistence | Implemented | Per-timeframe forecasts and four-timeframe bundles |
| Kronos zero production weight | Enforced | Contract and configuration reject non-zero shadow weight |
| Kronos leakage audit | Implemented initial gate | Provider preprocessing approval must be pinned to a source revision |
| Kronos continuous cycle | Implemented | Diagnostic stage cannot affect canonical blockers or signals |
| Kronos API/status | Implemented | Status, manual forecast, and latest persisted forecast |
| Kronos outcome validation KPIs | Pending | Next phase; requires realized outcome alignment |
| Incremental value vs Atlas baseline | Pending | Cannot be computed until shadow history exists |
| Dynamic Kronos weight | Deliberately disabled | Requires verified cutoff, OOS improvement, calibration, and promotion |
| Persistent instrument workspaces | Pending | Build as store-backed projections, not mutable truth files |
| Bayesian thesis updates | Pending | Research evidence only; no probability authority |
| Progressive tool registry | Pending | Preserve typed deterministic tools and load schemas on demand |
| Provider-neutral task router | Partial | Existing LLM client supports providers; task policy/cost routing remains |
| Evidence-family redundancy matrix | Partial | Meta dependency engine exists; explicit Kronos comparisons remain |
| Cost ledger/dashboard | Pending | Existing call telemetry is not yet a complete ledger |
| Kronos dashboard | Pending | Do not build UI until sufficient backend evidence exists |

## Implemented files

- `backend/app/v4/kronos_contracts.py`
- `backend/app/v4/kronos.py`
- `backend/app/v4/kronos_provider.py`
- `backend/tests/test_v4_kronos.py`
- Kronos settings in `backend/app/core/config.py` and `.env.example`
- Kronos endpoints in `backend/app/api/v4.py`
- Non-authoritative background stage in `backend/app/v4/orchestration.py`

## Non-negotiable promotion requirements

Kronos effective weight remains zero until all of the following are true:

1. The official source and model artifacts are pinned and checksummed.
2. Provider preprocessing passes the Atlas leakage audit.
3. The model's pretraining cutoff is verified for the claimed evaluation window, or the evaluation is explicitly labeled contaminated/unknown.
4. Enough shadow forecasts exist per instrument, timeframe, horizon, and regime.
5. Direction, Brier, log loss, ECE, quantile coverage, tail coverage, TP/SL ordering, stability, and post-cost metrics pass.
6. Atlas with Kronos improves an untouched baseline without Kronos.
7. Improvement is robust after redundancy control and transaction costs.
8. A governed promotion artifact is approved by the deterministic Quant policy.

Until then:

```text
mode = SHADOW
effective_weight = 0
signal_authority = false
execution_enabled = false
```

## Next implementation sequence

1. Add realized-outcome alignment for Kronos forecasts without look-ahead.
2. Implement per-market/timeframe/regime/horizon validation records and reliability summaries.
3. Implement Atlas-without-Kronos versus Atlas-with-Kronos incremental-value experiments.
4. Implement a persistent instrument-workspace projection with atomic revisions and provenance.
5. Implement progressive tool metadata and deterministic tool execution contracts.
6. Extend the existing provider client with explicit task policies, fallback lineage, budgets, and cost events.
7. Add redundancy-matrix outputs for Kronos versus momentum, trend, volatility, and the technical composite.
8. Build dashboard panels only from persisted evidence; never display a raw Kronos probability as the Atlas probability.

## Known risks

- Two hundred independent Kronos paths can be computationally expensive on CPU because the public predictor averages its internal sample dimension. Run it in a supervised background worker and enforce runtime budgets.
- Model downloads may require network access and substantial disk space. Atlas must remain functional when the runtime is missing.
- A local repository named `model` can collide with another Python package. The adapter detects this and fails explicitly.
- Kronos was trained across many exchanges, while Atlas targets FX and commodities. Domain transfer must be measured rather than assumed.
- Broker tick volume is not global exchange volume. Existing provenance labels must remain attached to every Kronos input.
- Unknown pretraining dates can invalidate strong OOS claims even when the Atlas walk-forward code is otherwise leak-free.
