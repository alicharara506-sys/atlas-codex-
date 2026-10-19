import { Mt5Provider } from "../../../lib/mt5-provider.ts";
import { PythonIntelligenceProvider } from "../../../lib/intelligence-provider.ts";
import type { Timeframe } from "../../../lib/types.ts";

const SYMBOLS = new Set(["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL"]);
const TIMEFRAMES = new Set<Timeframe>(["15M", "1H", "4H", "D1"]);

export async function GET(request: Request): Promise<Response> {
  try {
    if (process.env.MT5_ENABLED?.trim().toLowerCase() !== "true") throw new Error("ATLAS v2 live route requires the read-only MT5 provider.");
    if (process.env.ATLAS_INTELLIGENCE_ENABLED?.trim().toLowerCase() !== "true") throw new Error("ATLAS Python intelligence bridge is disabled.");
    const url = new URL(request.url);
    const symbol = (url.searchParams.get("symbol") ?? "XAUUSD").toUpperCase().replaceAll("/", "");
    const timeframe = (url.searchParams.get("timeframe") ?? "1H").toUpperCase() as Timeframe;
    if (!SYMBOLS.has(symbol)) throw new Error("Unsupported Atlas symbol.");
    if (!TIMEFRAMES.has(timeframe)) throw new Error("Unsupported Atlas timeframe.");
    const mt5 = new Mt5Provider({ port: Number(process.env.MT5_BRIDGE_PORT || 8765), token: process.env.MT5_BRIDGE_TOKEN || undefined });
    const health = await mt5.checkHealth();
    if (!health.mt5Connected) throw new Error("The MT5 terminal is not connected.");
    const [series, ticks, quote] = await Promise.all([mt5.fetchSeries(symbol, timeframe, 260), mt5.fetchTicks(symbol, 2_000), mt5.fetchQuote(symbol)]);
    const intelligence = new PythonIntelligenceProvider({ port: Number(process.env.ATLAS_INTELLIGENCE_PORT || 8776), token: process.env.ATLAS_INTELLIGENCE_TOKEN || undefined });
    const result = await intelligence.generateV2Signal({ instrument: symbol, timeframe, bars: series.bars, ticks, quote, runLlm: url.searchParams.get("llm") === "true", writeAudit: true });
    return Response.json(result, { headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "ATLAS v2 analysis failed.", readOnly: true, orderSubmission: false }, { status: 503, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
  }
}
