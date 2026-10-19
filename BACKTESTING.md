# Backtesting and Calibration

## Chronological backtests

The backtester runs Trend Following, Pullback, and Mean Reversion rules over chronological OHLC. Signals use closed bars, entries occur at the next bar open, ambiguous stop/target bars assume the stop first, and partitions are fixed at chronological 60/20/20 train/validation/test. With MT5 connected or Twelve Data configured, the Backtest view requests provider history; otherwise every result is explicitly demo-only.

The engine does not yet model commissions, spreads, slippage, changing contract definitions, market-session gaps, or dataset revisions. Research-grade claims require those costs and a versioned dataset.

## Historical win-probability calibration

Atlas never converts the analytical score or qualitative confidence label into a win probability. Probabilities appear only after importing resolved evidence in Settings with these CSV columns:

```csv
signal_id,score,outcome,resolved_at,source,symbol,timeframe
sig-001,72,WIN,2026-01-10T14:00:00Z,OUT_OF_SAMPLE,EURUSD,1H
```

Required fields are `signal_id`, `score`, `outcome`, `resolved_at`, and `source`. Outcomes must be `WIN` or `LOSS`; source must be `OUT_OF_SAMPLE` or `LIVE_RESOLVED`. Duplicate IDs, future dates, invalid scores, training rows, and other labels are rejected.

MT5 history follows the same rule. Atlas imports a resolved MT5 position only when one of its deal comments includes `ATLAS:signal_id:score`. Untagged trades are not assigned a reconstructed score and therefore cannot be used to calibrate the signal engine.

Availability requires at least 200 valid outcomes overall, 40 wins, 40 losses, and 30 outcomes in the applicable isotonic score segment. Atlas uses monotonic pooled score bins with Beta smoothing and reports a 95% Wilson interval. Imported outcomes live in device-local IndexedDB and can be removed from Settings.
