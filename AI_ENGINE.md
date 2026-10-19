# Local intelligence engine

Atlas does not delegate quantitative calculations to an LLM. Indicators, features, scores, regimes, entry geometry, risk, lifecycle status, P&L, calibration, and Kelly output are deterministic TypeScript results.

## TypeScript ML committee

`app/lib/ml-committee.ts` combines:

- 50 deterministic decision stumps trained on local engineered features.
- A 20-bar normalized-return nearest-pattern matcher with `k = 5`.
- Triple-barrier labels using 1.5 ATR and a 20-bar horizon.

The committee reports probability, agreement/confidence, component votes, and training sample count. Its signed alignment receives a bounded 15% blend with the base signal score.

## Four-model local sentiment oracle

`app/lib/sentiment-oracle.ts` calls four loopback Ollama `/api/generate` endpoints in parallel. Atlas validates every URL as loopback, accepts only strict numeric output, and requires 4/4 valid responses before it reports consensus.

Sentiment is confirmation-only:

- aligned strong consensus may apply a bounded confidence boost;
- strong opposing consensus may block the research setup;
- incomplete, malformed, or offline responses apply no change;
- there is no cloud fallback.

The `/api/local-sentiment` route receives only text the user explicitly enters into the local panel. It returns per-model availability, consensus, and any bounded confirmation decision.

## Role-aware Python LLM orchestrator

`scripts/llm_orchestrator.py` complements the numeric sentiment committee with task-specific JSON contracts:

- Llama 3.2 1B: regime classification and news tagging.
- Ministral 3B: confluence synthesis from supplied deterministic evidence.
- Qwen 2.5 3B: structured signal presentation that must copy deterministic action and price levels exactly.
- Phi-4 Mini: non-authoritative risk and position-sizing review.

The orchestrator validates loopback URLs and parsed fields, rejects malformed output, has no cloud fallback, and exposes only `advisoryOnly: true` results. It does not participate in broker calls or order execution.

The v2 task catalog expands those roles to micro regime, news triage, delta/OFII checks, AVWAP quality, divergence, footprint and macro narratives, multi-timeframe alignment, signal/scenario JSON, volatility regime, significance, correlation, confluence math, and stress review. Independent tasks run concurrently through the async interface and identical inputs use a five-minute local cache. Every task has a prompt template in `prompts/`; failures degrade to deterministic math per task rather than switching to a cloud service.
