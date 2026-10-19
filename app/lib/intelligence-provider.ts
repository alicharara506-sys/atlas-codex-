import type { OHLCBar, Tick } from "./types.ts";

export interface PythonOrderFlowRead {
  symbol: string;
  method: "TICK_RULE_APPROXIMATION";
  centralizedTape: false;
  lastPrice: number;
  anchoredVwap: { value: number | null; anchorTime: number | null; volumeBasis: string };
  volumeProfile: {
    method: string;
    volumeBasis: string;
    poc: number | null;
    valueAreaLow: number | null;
    valueAreaHigh: number | null;
    highVolumeNodes: number[];
    lowVolumeNodes: number[];
  };
  delta: {
    volumeBasis: string;
    buyVolume: number;
    sellVolume: number;
    delta: number;
    cumulativeDelta: number;
    imbalanceRatio: number;
    pressure: string;
  };
  confluence: {
    score: number;
    bias: "BULLISH" | "BEARISH" | "NEUTRAL";
    components: Record<string, number>;
    evidence: string[];
  };
  coverage: { closedBars: number; ticks: number };
  dataQuality: {
    orderFlowVerified: false;
    volumeBasis: string;
    warnings: string[];
  };
}

export interface LocalLLMRouteResult {
  task: string;
  model: string;
  endpoint: string;
  role: string;
  advisoryOnly: true;
  cloudFallback: false;
  latencyMs: number;
  output: Record<string, unknown>;
}

export interface IntelligenceHealth {
  status: string;
  provider: string;
  readOnly: true;
  orderSubmission: false;
  orderflowMethod: string;
  llmRoutes: Array<{ task: string; model: string; endpoint: string; role: string }>;
  cloudFallback: false;
}

export interface StaticChartExport {
  filename: string;
  mimeType: "image/png";
  bytes: number;
  renderer: "MATPLOTLIB_AGG" | "ATLAS_BUILTIN_PNG";
  width: number;
  height: number;
  static: true;
  readOnly: true;
  orderSubmission: false;
  pngBase64: string;
}

interface PythonIntelligenceConfig {
  baseUrl?: string;
  port?: number;
  token?: string;
  fetchImpl?: typeof fetch;
}

function loopbackBaseUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
    throw new Error("Atlas intelligence bridge must use loopback-only HTTP.");
  }
  if (url.username || url.password || url.search || url.hash || (url.pathname !== "/" && url.pathname !== "")) {
    throw new Error("Atlas intelligence bridge URL contains unsupported components.");
  }
  return value.replace(/\/$/, "");
}

export class PythonIntelligenceProvider {
  private readonly baseUrl: string;
  private readonly token?: string;
  private readonly fetchImpl: typeof fetch;

  constructor(config: PythonIntelligenceConfig = {}) {
    this.baseUrl = loopbackBaseUrl(config.baseUrl ?? `http://127.0.0.1:${config.port ?? 8776}`);
    this.token = config.token;
    this.fetchImpl = config.fetchImpl ?? fetch;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.fetchImpl(this.baseUrl + path, {
      ...init,
      headers: {
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    const body = await response.json() as T & { error?: string };
    if (!response.ok) throw new Error(body.error || `Intelligence bridge returned HTTP ${response.status}.`);
    return body;
  }

  async checkHealth(): Promise<IntelligenceHealth> {
    return this.request<IntelligenceHealth>("/health");
  }

  async analyzeOrderFlow(symbol: string, bars: OHLCBar[], ticks: Tick[]): Promise<PythonOrderFlowRead> {
    const response = await this.request<{ result: PythonOrderFlowRead }>("/orderflow", {
      method: "POST",
      body: JSON.stringify({ symbol, bars, ticks }),
    });
    if (response.result.method !== "TICK_RULE_APPROXIMATION" || response.result.centralizedTape !== false) {
      throw new Error("Intelligence bridge returned an untruthful order-flow method label.");
    }
    return response.result;
  }

  async routeTask(task: string, payload: Record<string, unknown>): Promise<LocalLLMRouteResult> {
    const response = await this.request<{ result: LocalLLMRouteResult }>("/llm/route", {
      method: "POST",
      body: JSON.stringify({ task, payload }),
    });
    if (!response.result.advisoryOnly || response.result.cloudFallback !== false) {
      throw new Error("Local orchestrator did not preserve the advisory-only boundary.");
    }
    return response.result;
  }

  async generateV2Signal(input: {
    instrument: string;
    timeframe: string;
    bars: OHLCBar[];
    ticks?: Tick[];
    quote?: unknown;
    macroInputs?: Record<string, unknown>;
    headlines?: Array<Record<string, unknown>>;
    calendarEvents?: Array<Record<string, unknown>>;
    activeSignals?: Array<Record<string, unknown>>;
    runLlm?: boolean;
    writeAudit?: boolean;
  }): Promise<Record<string, unknown>> {
    const response = await this.request<{ result: Record<string, unknown> }>("/v2/signal", {
      method: "POST",
      body: JSON.stringify(input),
    });
    return response.result;
  }

  async renderChart(input: {
    symbol: string;
    timeframe: string;
    bars: OHLCBar[];
    signal: Record<string, unknown>;
    wyckoff?: Record<string, unknown>;
    smc?: Record<string, unknown>;
  }): Promise<StaticChartExport> {
    const response = await this.request<{ result: StaticChartExport }>("/render-chart", {
      method: "POST",
      body: JSON.stringify(input),
    });
    if (response.result.mimeType !== "image/png" || !response.result.static || !response.result.readOnly || response.result.orderSubmission !== false) {
      throw new Error("Intelligence bridge returned an invalid static-chart contract.");
    }
    return response.result;
  }
}
