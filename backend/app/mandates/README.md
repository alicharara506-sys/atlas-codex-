# Phase 3 mandate engine

Mandates use strict, versioned YAML contracts for universe, desks, risk,
execution intent, and backtest settings. Registration and backtest lifecycle
records are idempotently persisted. Promotion remains fail closed: neither
paper nor live eligibility is issued while the global execution flags are off.
