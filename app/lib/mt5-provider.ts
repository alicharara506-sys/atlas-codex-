// app/lib/mt5-provider.ts
// Normalizes responses from the loopback scripts/mt5_bridge.py into Atlas's
// canonical types. Server-side only (reads MT5_BRIDGE_TOKEN from the
// environment and calls http://127.0.0.1, which is meaningless/unreachable
// from a browser bundle anyway).
//
// Deliberately split into two halves:
//   1. Pure `normalize*` functions — no I/O, fully unit-testable.
//   2. `Mt5Provider` — the thin fetch wrapper that calls the bridge and
//      hands its JSON to the pure functions above.
// This mirrors the rest of Atlas: the parts that can be wrong in an
// interesting way (bar/tick shaping, closed-bar guarantees, unit
// conversions) are pure and tested; the network plumbing is thin on purpose.

import type {
  BrokerInstrumentProvider,
  MarketDataProvider,
  TickDataProvider,
} from "./providers.ts";
import { ProviderError } from "./providers.ts";
import type {
  BrokerSpec,
  CanonicalSeries,
  DataStatus,
  OHLCBar,
  Tick,
  Timeframe,
} from "./types.ts";

const PROVIDER_NAME = "mt5";

// ---------------------------------------------------------------------------
// Bridge response shapes (must match scripts/mt5_bridge.py exactly)
// ---------------------------------------------------------------------------

export interface BridgeBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface BridgeRatesResponse {
  symbol: string;
  brokerSymbol: string;
  timeframe: string;
  bars: BridgeBar[];
}

export interface BridgeTick {
  time: number;
  bid: number;
  ask: number;
  last: number | null;
  volume: number;
}

export interface BridgeTicksResponse {
  symbol: string;
  brokerSymbol: string;
  ticks: BridgeTick[];
}

export interface BridgeQuoteResponse {
  symbol: string;
  brokerSymbol: string;
  bid: number;
  ask: number;
  time: number;
}

export interface BridgeSymbolInfoResponse {
  symbol: string;
  brokerSymbol: string;
  digits: number;
  tickSize: number;
  tickValue: number;
  contractSize: number;
  minVolume: number;
  maxVolume: number;
  lotStep: number;
  profitCurrency: string;
  marginCurrency: string;
}

export interface BridgeAccountResponse {
  login: number;
  server: string;
  currency: string;
  balance: number;
  equity: number;
  margin: number;
  marginFree: number;
  leverage: number;
  tradeMode?: "REAL" | "DEMO" | "CONTEST";
  tradeAllowed?: boolean;
}

export interface BridgeHealthResponse {
  status: string;
  mt5Connected: boolean;
  terminal: { build: number; company: string; connected: boolean } | null;
}

export interface BridgeErrorResponse {
  error: string;
}

// ---------------------------------------------------------------------------
// Pure normalization (unit-tested, no network)
// ---------------------------------------------------------------------------

export function normalizeRatesResponse(
  response: BridgeRatesResponse,
  timeframe: Timeframe,
  status: DataStatus = "LIVE"
): CanonicalSeries {
  const bars: OHLCBar[] = response.bars
    .map((b) => ({
      time: b.time,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
      volume: b.volume,
    }))
    .sort((a, b) => a.time - b.time);

  // Defensive check, not a trust assumption: the bridge already sorts and
  // excludes the forming bar, but a normalizer that doesn't verify its own
  // invariants is how "closed bars only" quietly breaks after a refactor.
  for (let i = 1; i < bars.length; i++) {
    if (bars[i].time <= bars[i - 1].time) {
      throw new ProviderError(
        PROVIDER_NAME,
        `non-chronological or duplicate bar timestamps at index ${i}`
      );
    }
  }

  return {
    symbol: response.symbol,
    timeframe,
    bars,
    status,
    source: PROVIDER_NAME,
  };
}

export function normalizeTicksResponse(response: BridgeTicksResponse): Tick[] {
  const ticks: Tick[] = response.ticks
    .map((t) => ({
      time: t.time,
      bid: t.bid,
      ask: t.ask,
      last: t.last ?? undefined,
      volume: t.volume,
    }))
    .sort((a, b) => a.time - b.time);
  return ticks;
}

export function normalizeSymbolInfoResponse(
  response: BridgeSymbolInfoResponse,
  leverage: number
): BrokerSpec {
  if (response.tickSize <= 0) {
    throw new ProviderError(
      PROVIDER_NAME,
      `symbol_info for '${response.symbol}' has non-positive tickSize`
    );
  }
  return {
    tickSize: response.tickSize,
    tickValue: response.tickValue,
    contractSize: response.contractSize,
    minVolume: response.minVolume,
    maxVolume: response.maxVolume,
    lotStep: response.lotStep,
    leverage,
  };
}

// ---------------------------------------------------------------------------
// Thin fetch wrapper
// ---------------------------------------------------------------------------

export interface Mt5ProviderConfig {
  baseUrl?: string; // default http://127.0.0.1:<port>
  port?: number; // used only if baseUrl is omitted; default 8765
  token?: string; // MT5_BRIDGE_TOKEN
  fetchImpl?: typeof fetch; // injectable for tests
}

export class Mt5Provider
  implements MarketDataProvider, TickDataProvider, BrokerInstrumentProvider
{
  readonly name = PROVIDER_NAME;
  private readonly baseUrl: string;
  private readonly token: string | undefined;
  private readonly fetchImpl: typeof fetch;

  constructor(config: Mt5ProviderConfig = {}) {
    this.baseUrl = config.baseUrl ?? `http://127.0.0.1:${config.port ?? 8765}`;
    this.token = config.token;
    this.fetchImpl = config.fetchImpl ?? fetch;
  }

  private async getJson<T>(path: string, params: Record<string, string>): Promise<T> {
    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);

    let response: Response;
    try {
      response = await this.fetchImpl(url.toString(), {
        headers: this.token ? { Authorization: `Bearer ${this.token}` } : {},
      });
    } catch (err) {
      throw new ProviderError(
        PROVIDER_NAME,
        "bridge unreachable — is scripts/mt5_bridge.py running and MT5 logged in?",
        err
      );
    }

    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const body = (await response.json()) as BridgeErrorResponse;
        if (body.error) message = body.error;
      } catch {
        // response body wasn't JSON; keep the generic status message
      }
      throw new ProviderError(PROVIDER_NAME, message);
    }

    return (await response.json()) as T;
  }

  async checkHealth(): Promise<BridgeHealthResponse> {
    return this.getJson<BridgeHealthResponse>("/health", {});
  }

  async fetchAccount(): Promise<BridgeAccountResponse> {
    return this.getJson<BridgeAccountResponse>("/account", {});
  }

  async fetchSeries(
    symbol: string,
    timeframe: Timeframe,
    limit: number
  ): Promise<CanonicalSeries> {
    const raw = await this.getJson<BridgeRatesResponse>("/rates", {
      symbol,
      timeframe,
      count: String(limit),
    });
    return normalizeRatesResponse(raw, timeframe, "LIVE");
  }

  async fetchTicks(symbol: string, count: number): Promise<Tick[]> {
    const raw = await this.getJson<BridgeTicksResponse>("/ticks", {
      symbol,
      count: String(count),
    });
    return normalizeTicksResponse(raw);
  }

  async fetchQuote(symbol: string): Promise<BridgeQuoteResponse> {
    return this.getJson<BridgeQuoteResponse>("/quote", { symbol });
  }

  async fetchBrokerSpec(symbol: string): Promise<BrokerSpec> {
    const [infoRaw, accountRaw] = await Promise.all([
      this.getJson<BridgeSymbolInfoResponse>("/symbol_info", { symbol }),
      this.fetchAccount(),
    ]);
    return normalizeSymbolInfoResponse(infoRaw, accountRaw.leverage);
  }
}
