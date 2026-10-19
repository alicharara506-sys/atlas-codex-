# ATLAS V3 Repository Audit

Audit date: 2026-08-18  
Application root: `CLAUDE_ATLAS_TRADING_TERMINAL/atlas-delivery`  
Checkpoint: `checkpoints/atlas-v3-prechange-20260818.zip`

## Executive finding

Atlas is a working local, read-only research terminal, but it is not yet an ATLAS V3 publication system. The active UI/API uses TypeScript V2 logic, a separate Python V2 intelligence pipeline supplies advisory analytics, and a FastAPI Phase 1 scaffold is not connected to the terminal. The strongest existing property is separation from order execution. The largest V3 defect is that the visible API can expose LONG, SHORT, or uncalibrated pending research states without an independent Quant veto, at least 80% empirically calibrated probability, positive net expectancy, or a unified publication gate.

## Existing architecture

- React 19 terminal built through Vinext/Vite. `terminal.tsx` polls `/api/atlas` and `/api/atlas/quote`.
- `app/api/atlas/route.ts` is the active signal path.
- TypeScript implements indicators, structure/regime logic, factor scoring, a deterministic local ML committee, research sizing, lifecycle tracking, backtesting, and isotonic calibration utilities.
- IndexedDB stores outcomes, but that evidence is unavailable to the server publication path.
- `scripts/mt5_bridge.py` exposes read-only account, quote, bar, tick, and contract metadata on loopback. No order submission was found.
- `scripts/intelligence_bridge.py` exposes deterministic Python proxies, V2 composition, charting, and local-LLM routing.
- `scripts/atlas_core.py` composes Python V2 and SQLite audit records.
- `backend/app` is a disconnected FastAPI scaffold with provider adapters, SQLModel persistence, LLM adapters, and health/readiness routes.

## Baseline verification

- TypeScript typecheck: PASS.
- TypeScript engine tests: 41/41 PASS.
- Legacy Python tests: 26/26 PASS.
- FastAPI/backend tests with `.venv`: 32 PASS; one Redis integration test skipped because Redis is not configured.
- Ruff: PASS.
- Production Vinext build: PASS.
- `npm test`: FAILS because the Python runner selects an interpreter missing the backend `redis` dependency.
- ESLint: FAILS because it scans third-party JavaScript under `.venv`.
- No Git repository exists, so the checkpoint above was created before V3 changes.

## Working components to preserve

- Read-only MT5 boundary and explicit `orderSubmission: false`.
- Closed-bar normalization and chronology validation.
- Honest labeling of OTC volume/order-flow/liquidity results as proxies, not Level 2 truth.
- Deterministic financial calculations; LLMs cannot author prices or risk geometry.
- Local Ollama schema validation and fail-closed malformed output.
- Evidence-gated isotonic calibration utility.
- Broker-aware risk sizing that rounds down.
- Signal lifecycle analytics and annotated chart export.
- Backend provider interfaces, usage ledger, persistence contracts, and disabled execution policy.

## Broken or incomplete components

- The active API always expresses its signal on 1H; no V3 selection across 15M/1H/4H/1D exists.
- Server-generated `historicalWinProbability` is always null.
- Uncalibrated LONG/SHORT and pending states can be displayed.
- No independent Quant Validator with absolute veto authority exists in the active path.
- No unified expectancy gate includes spread, slippage, and commissions.
- No unified stale/gap/impossible-price/provider-disagreement data-quality report exists.
- Event, macro, news, liquidity, and portfolio evidence is fragmented and not consistently fail-closed.
- TypeScript V2, Python V2, and FastAPI concepts overlap and can disagree.
- The FastAPI scaffold includes equity/crypto/SEC scope not aligned with forex/commodities.

## Dangerous assumptions

- Confluence or model agreement is not calibrated win probability.
- Demo geometry must never be presented as a production signal.
- OTC forex/CFD volume is not a consolidated exchange tape.
- Missing critical data is not neutral evidence.
- Browser-only outcomes are not automatically valid out-of-sample evidence.
- Attractive reward/risk does not guarantee positive net expectancy.
- Basic chronological splits do not by themselves prevent overlapping-event leakage.

## Missing V3 components

- Canonical typed V3 signal/status schema.
- Independent Quant Validator covering sample, calibration, Brier/ECE, robustness, drift, costs, and veto reasons.
- Calibrated-probability provenance and model governance.
- Fail-closed publication gate and dynamic timeframe selector.
- Unified data-quality and event-risk gates.
- Provider health/cost/rate-limit/circuit-breaker aggregation.
- Server-side shadow-mode audit and outcome resolution.
- Portfolio correlation/concentration policy.
- V3 UI fields for grade, Quant status, expectancy, timeframe, event risk, data quality, and veto reasons.

## Proposed architecture

```text
providers -> canonical market/event/news contracts -> data-quality gate
          -> deterministic features + regime -> per-timeframe candidates
          -> calibration + net expectancy -> independent Quant Validator
          -> optional structured LLM committee for qualified candidates
          -> final publication gate -> best validated timeframe or WAIT
          -> append-only audit/shadow store -> read-only V3 API -> React UI

BrokerAdapter and ExecutionAdapter remain separate and disabled by default.
```

The FastAPI backend should eventually become authoritative while the existing terminal remains the presentation client. During migration, policy logic needs shared test vectors so implementations cannot drift silently.

## Migration plan

1. Fix test/lint infrastructure and add a typed, pure V3 policy foundation.
2. Add data-quality, expectancy, grading, Quant veto, publication-gate, and timeframe tests.
3. Add server-side shadow/audit persistence and calibrated-evidence provenance.
4. Connect the active route to V3; retain V2 output only as diagnostic evidence.
5. Add formal regime, event, macro, liquidity, and portfolio-risk services.
6. Add triple-barrier labeling, purged walk-forward validation, and model governance.
7. Add opt-in model-agnostic cloud/local committee adapters with cost budgets.
8. Move the UI to the authoritative V3 response and retire duplicates only after parity tests.

## Mandatory tests

- 79.99% is rejected; 80.00% can qualify only when every other gate passes.
- 99% plus Quant veto is rejected.
- 95% plus zero/negative net expectancy is rejected.
- Critical event risk and bad/stale data return WAIT.
- Demo or uncalibrated candidates return WAIT.
- Grades match B/A/A+/A+++++ boundaries.
- The shortest timeframe is not automatically preferred.
- Missing LLM responses never become synthetic votes.
- Live execution remains impossible by default.

## Risks and conclusion

Sample scarcity, leakage, selection bias, overfitting, and regime instability can create illusory probabilities. Free feeds also have gaps, delays, inconsistent symbols, and no consolidated FX order book. Three decision paths create policy drift, and Docker/PostgreSQL/Redis are not yet proven on this laptop.

Atlas has a credible deterministic foundation and a strong no-execution boundary. It should be upgraded, not rewritten. V3 must begin by making publication fail closed. Honest calibration will initially produce mostly WAIT; that is correct until sufficient evidence exists.
