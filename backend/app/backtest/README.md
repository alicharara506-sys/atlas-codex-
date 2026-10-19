# Phase 3 deterministic backtester

The OHLCV-only engine uses completed-bar moving-average signals, fills only at
the next bar open, produces deterministic metrics and audit summaries, and can
export summary JSON, trades/equity JSONL, and a static HTML report. It never
submits an order and leaves the existing Atlas TypeScript backtester unchanged.
