# Atlas mandates

Phase 3 mandates are versioned YAML configuration, not application code. Start
with `atlas-macro-tilt.example.yaml`, then validate it through
`POST /mandates/validate` before running an OHLCV-only backtest.

A mandate may describe a future paper/live target, but the global policy keeps
both promotion states and all execution paths disabled.
