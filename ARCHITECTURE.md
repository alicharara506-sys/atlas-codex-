# Atlas Quant architecture

## Product boundary

Atlas is a single-user, local-first research application. The browser provides the UI and IndexedDB persistence. A loopback Python bridge reads MT5. Pure TypeScript modules calculate all quantitative outputs. Optional Ollama requests stay on loopback. No component can submit an order.

```text
MT5 terminal
    -> loopback read-only Python bridge
    -> /api/atlas and /api/atlas/quote
    -> indicators, regime, features, signal, ML, risk
    -> loopback Python intelligence bridge
       -> AVWAP, profile, CVD/delta, footprint/liquidity approximations
    -> React terminal
    -> IndexedDB lifecycle outcomes and analytics

Local research text
    -> /api/local-sentiment
    -> four loopback Ollama endpoints
    -> unanimous numeric confirmation or unavailable

Bounded advisory task
    -> /api/local-orchestrator
    -> Python role router
    -> Llama (regime/news), Ministral (synthesis), Qwen (JSON), Phi-4 (risk review)
    -> strict JSON validation; deterministic signal/risk remain authoritative

Institutional v2 request
    -> /api/atlas/v2 or main terminal validation card
    -> scripts/atlas_core.py
    -> deterministic orderflow + macro/news state + confluence + structural risk
    -> optional role-routed Ollama reviews in parallel
    -> complete JSON attribution + SQLite audit hash
```

## Layers

1. `scripts/mt5_bridge.py` exposes read-only health, account, quote, rates, ticks, and symbol-information endpoints on loopback.
2. `app/lib/mt5-provider.ts` validates and normalizes bridge data.
3. `app/lib/signal-engine.ts` calculates indicators, eight factors, regimes, strategy routing, feature engineering, evidence, entry, stop, and targets.
4. `app/lib/ml-committee.ts` provides deterministic decision-stump and nearest-pattern votes. It contributes 15% of alignment; it cannot bypass safety or calibration rules.
5. `app/lib/risk-engine.ts` performs broker-aware, round-down-only research sizing.
6. `app/lib/live-tracker.ts` and `app/lib/outcome-engine.ts` track signal lifecycle and calculate friction-adjusted analytics.
7. `app/lib/sentiment-oracle.ts` calls only validated `127.0.0.1` endpoints and requires 4/4 responses.
8. `app/lib/storage.ts` owns IndexedDB schema version 3, including the `signalOutcomes` store.
9. `app/components/terminal.tsx` orchestrates 60-second analysis refreshes and 5-second quote sampling without calculating indicators in React.
10. `scripts/orderflow_engine.py` adds deterministic broker-feed AVWAP, volume-profile, CVD/delta, footprint, absorption, and liquidity-proxy features.
11. `scripts/llm_orchestrator.py` performs role-aware, schema-checked, local-only advisory inference. It cannot mutate the deterministic signal or submit an order.
12. `scripts/intelligence_bridge.py` exposes those two Python modules on authenticated loopback HTTP and has no broker/order dependency.
13. `scripts/atlas_core.py` composes the v2 signal schema without allowing LLMs to author prices or risk geometry.
14. `scripts/macro_engine.py` and `scripts/news_sentiment.py` normalize optional zero-cost evidence and explicitly report unavailable inputs.
15. `scripts/confluence_scorer.py` and `scripts/risk_engine.py` own bounded scoring, suppression, exposure, structural stops/targets, correlation, and stress logic.
16. `scripts/audit_log.py` writes local SQLite attribution records with a canonical payload hash.

## Safety and truthfulness

- API responses always expose `readOnly: true` and `orderSubmission: false`.
- MT5 login details stay in the terminal; bridge authorization stays server-side.
- Forming candles are excluded from analysis.
- Missing macro, tick, calibration, or Ollama evidence remains missing and is labeled.
- Qualitative confidence, blended alignment score, and historical win probability remain separate concepts.
- Sentiment is optional confirmation; unavailable local models never become synthetic votes.

## V3 fail-closed policy foundation

`app/lib/v3/` is the first controlled V3 migration layer. It contains no I/O and no execution capability. `data-quality.ts` validates canonical bars and live provenance; `expectancy.ts` calculates net expected value after explicit costs; `quant-validator.ts` owns independent statistical vetoes; `publication-gate.ts` enforces the 80% threshold and all critical gates; and `timeframe-selector.ts` ranks only candidates that already passed publication policy.

The existing V2 API is not silently relabeled V3. Until authoritative server-side calibration evidence and model approval are wired into the route, V3 policy remains a tested foundation rather than a claim that signals are institutionally validated.

## V4 background orchestration

`backend/app/v4/orchestration.py` is owned by the FastAPI lifespan. When
`V4_ORCHESTRATION_ENABLED=true`, it starts immediately with the backend and then
runs on a fixed delay. Every cycle processes each approved instrument in
isolation:

```text
MT5 capture -> deterministic features -> explicit context
            -> leakage-safe H1 train/validation/test split
            -> experimental primary models + OOS block-bootstrap evidence
            -> governed SHADOW candidate when one is manually approved
            -> canonical publication gate when complete current lineage exists
```

Cycle reports and singleton status are durable database records. One failed
symbol cannot terminate the service or stop the remaining universe. Model,
calibration, Quant, risk, and publication blockers are emitted as typed codes;
they are not converted into synthetic confidence or demo signals. Automatic
promotion to SHADOW remains prohibited. The service has analytical publication
authority only through the existing canonical gate and exposes no order route.
