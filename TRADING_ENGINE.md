# Trading and Signal Engine

## Indicator set

The current engine implements SMA, EMA, RSI, ATR, ROC, standard-deviation volatility, volume comparison, and an ADX-style directional-efficiency approximation. Each indicator is consumed by a defined feature; none exists solely as decoration.

## Structure and regime

Recent and prior swing windows classify higher-high/higher-low, lower-high/lower-low, or mixed structure. Regime combines normalized EMA spread, ROC, volatility, and directional efficiency into:

- strong or weak bull/bear trend;
- range;
- breakout;
- reversal;
- high or low volatility;
- unclear/noise.

## Score

Default weights:

| Factor | Weight |
| --- | ---: |
| Market structure | 20% |
| Trend | 15% |
| Momentum | 15% |
| Multi-timeframe alignment | 15% |
| Volatility | 10% |
| Volume | 10% |
| Support/resistance context | 10% |
| Macro context | 5% |

Weights are device-configurable and normalized by their actual total. Macro remains neutral when news/calendar providers are unavailable.

Direction uses a weighted D1/4H/1H/15M bias. Three conflicting timeframes force `WAIT`. Each signal contains score, qualitative confidence, entry zone, ATR-based invalidation, three targets, evidence, regime, expiry, and bull/base/bear scenarios.

## Critical distinction

- **Score** — current analytical factor alignment from 0–100.
- **Confidence** — qualitative agreement label based on score and timeframe conflicts.
- **Historical win probability** — empirical outcome rate under a defined calibration protocol.

Atlas ships with zero calibrated outcomes, so historical probability is `null` and unavailable in the UI.

## Adding strategies

New strategies should consume closed-bar features through small independent modules, expose explicit entry/exit/invalidation rules, and have isolated tests. Do not embed vendor calls, UI state, or LLM output in a strategy.
