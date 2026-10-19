export const LOCAL_OLLAMA_ENDPOINTS = [
  "http://127.0.0.1:11434",
  "http://127.0.0.1:11435",
  "http://127.0.0.1:11436",
  "http://127.0.0.1:11437",
] as const;

export interface LocalModelSentiment {
  endpoint: string;
  score: number | null;
  available: boolean;
  error?: string;
}

export interface SentimentConsensus {
  score: number | null;
  availableModels: number;
  requiredModels: number;
  consensus: boolean;
  spike: boolean;
  zScore: number | null;
  models: LocalModelSentiment[];
}

function loopbackOnly(endpoint: string): string {
  const url = new URL(endpoint);
  if (!['127.0.0.1', 'localhost', '::1'].includes(url.hostname)) {
    throw new Error("Local sentiment endpoints must be loopback-only.");
  }
  return url.origin;
}

function parseScore(value: unknown): number | null {
  const text = typeof value === "number" ? String(value) : typeof value === "string" ? value : "";
  const match = text.trim().match(/^[+-]?(?:1(?:\.0+)?|0(?:\.\d+)?)$/);
  if (!match) return null;
  const score = Number(match[0]);
  return Number.isFinite(score) ? Math.max(-1, Math.min(1, score)) : null;
}

export class SentimentSpikeDetector {
  private readonly history: { score: number; time: number }[] = [];

  add(score: number, time = Date.now()): { spike: boolean; zScore: number | null } {
    const cutoff = time - 24 * 60 * 60 * 1_000;
    while (this.history.length && this.history[0].time < cutoff) this.history.shift();
    const prior = this.history.map((row) => row.score);
    this.history.push({ score, time });
    if (prior.length < 5) return { spike: false, zScore: null };
    const mean = prior.reduce((sum, value) => sum + value, 0) / prior.length;
    const variance = prior.reduce((sum, value) => sum + (value - mean) ** 2, 0) / prior.length;
    const std = Math.sqrt(variance);
    const zScore = std > 0 ? (score - mean) / std : 0;
    return { spike: Math.abs(zScore) > 2, zScore };
  }
}

export class LocalLLMConsensusEngine {
  private readonly endpoints: string[];
  private readonly models: string[];
  private readonly fetcher: typeof fetch;
  private readonly detector = new SentimentSpikeDetector();

  constructor(options: { endpoints?: string[]; models?: string[]; fetcher?: typeof fetch } = {}) {
    this.endpoints = (options.endpoints ?? [...LOCAL_OLLAMA_ENDPOINTS]).map(loopbackOnly);
    this.models = options.models ?? ["ministral-3:3b", "llama3.2:1b-instruct-q3_K_L", "qwen2.5:3b", "phi4-mini"];
    this.fetcher = options.fetcher ?? fetch;
  }

  async analyze(symbol: string, localText: string[]): Promise<SentimentConsensus> {
    const text = localText.map((item) => item.trim()).filter(Boolean).slice(0, 50);
    if (!text.length) {
      return { score: null, availableModels: 0, requiredModels: 4, consensus: false, spike: false, zScore: null, models: this.endpoints.map((endpoint) => ({ endpoint, score: null, available: false, error: "No local text supplied." })) };
    }
    const prompt = `Rate market sentiment for ${symbol} from -1 (bearish) to +1 (bullish). Respond ONLY with a number. Local text:\n${text.join("\n")}`;
    const results = await Promise.all(this.endpoints.map(async (endpoint, index): Promise<LocalModelSentiment> => {
      const controller = new AbortController();
      // Cold CPU loads of the 3B/3.8B models can take tens of seconds.
      const timeout = setTimeout(() => controller.abort(), 300_000);
      try {
        const response = await this.fetcher(`${endpoint}/api/generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: this.models[index] ?? this.models[0], prompt, stream: false, keep_alive: -1, options: { temperature: 0, num_ctx: 2_048, num_predict: 8, stop: ["\n"] } }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json() as { response?: unknown };
        const score = parseScore(payload.response);
        if (score === null) throw new Error("Model did not return a numeric score.");
        return { endpoint, score, available: true };
      } catch (error) {
        return { endpoint, score: null, available: false, error: error instanceof Error ? error.message : "Local model failed." };
      } finally {
        clearTimeout(timeout);
      }
    }));
    const scores = results.flatMap((result) => result.score === null ? [] : [result.score]);
    const score = scores.length === 4 ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null;
    const spike = score === null ? { spike: false, zScore: null } : this.detector.add(score);
    return { score, availableModels: scores.length, requiredModels: 4, consensus: scores.length === 4, spike: spike.spike, zScore: spike.zScore, models: results };
  }
}

export function applySentimentConfirmation<T extends "LONG" | "SHORT" | "WAIT" | "BUY_STOP" | "SELL_STOP">(
  action: T,
  score: number,
  sentiment: SentimentConsensus
): { action: T | "WAIT"; score: number; blocked: boolean; reason: string } {
  if (!sentiment.consensus || sentiment.score === null) return { action, score, blocked: false, reason: "Local sentiment unavailable; no score change." };
  const longSide = action === "LONG" || action === "BUY_STOP";
  const shortSide = action === "SHORT" || action === "SELL_STOP";
  if (longSide && sentiment.score < -0.2) return { action: "WAIT", score, blocked: true, reason: "Opposing bearish local sentiment blocked the long setup." };
  if (shortSide && sentiment.score > 0.2) return { action: "WAIT", score, blocked: true, reason: "Opposing bullish local sentiment blocked the short setup." };
  const alignedSpike = sentiment.spike && ((longSide && sentiment.score > 0.7) || (shortSide && sentiment.score < -0.7));
  return { action, score: Math.min(100, score + (alignedSpike ? 5 : 0)), blocked: false, reason: alignedSpike ? "Aligned local sentiment spike added five score points." : "Local sentiment confirmation passed." };
}
