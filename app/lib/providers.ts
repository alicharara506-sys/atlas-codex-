// app/lib/providers.ts
// Canonical provider contracts. Every adapter (mt5-provider.ts,
// server-providers.ts) implements these interfaces so the rest of Atlas
// never has to know which vendor a given symbol's data came from.

import type { BrokerSpec, CanonicalSeries, Tick, Timeframe } from "./types.ts";

export interface MarketDataProvider {
  readonly name: string;
  fetchSeries(symbol: string, timeframe: Timeframe, limit: number): Promise<CanonicalSeries>;
}

export interface TickDataProvider {
  readonly name: string;
  fetchTicks(symbol: string, count: number): Promise<Tick[]>;
}

export interface BrokerInstrumentProvider {
  readonly name: string;
  fetchBrokerSpec(symbol: string): Promise<BrokerSpec>;
}

export interface ProviderStatus {
  name: string;
  configured: boolean; // a config value is present — never the value itself
  reachable: boolean | null; // null = not checked this call
}

export class ProviderError extends Error {
  public readonly provider: string;
  public readonly cause?: unknown;

  constructor(provider: string, message: string, cause?: unknown) {
    super(`[${provider}] ${message}`);
    this.name = "ProviderError";
    this.provider = provider;
    this.cause = cause;
  }
}
