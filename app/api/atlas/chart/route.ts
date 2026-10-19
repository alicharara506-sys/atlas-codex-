import { Mt5Provider } from "../../../lib/mt5-provider.ts";
import { PythonIntelligenceProvider } from "../../../lib/intelligence-provider.ts";
import type { Timeframe } from "../../../lib/types.ts";

const SYMBOLS = new Set(["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL"]);
const TIMEFRAMES = new Set<Timeframe>(["15M", "1H", "4H", "D1"]);
const DIRECTIONS = new Set(["LONG", "SHORT", "BUY_STOP", "SELL_STOP"]);

interface ChartGeometry {
  direction: string;
  entryMin: number;
  entryMax: number;
  stopLoss: number;
  targets: [number, number, number];
}

function finitePrice(value: unknown, label: string): number {
  const price = Number(value);
  if (!Number.isFinite(price) || price <= 0) throw new Error(`Invalid ${label} in Atlas chart geometry.`);
  return price;
}

function parseGeometry(value: unknown): ChartGeometry | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid Atlas chart geometry.");
  const row = value as Record<string, unknown>;
  const direction = String(row.direction ?? "").toUpperCase();
  if (!DIRECTIONS.has(direction)) throw new Error("Unsupported Atlas chart direction.");
  const entryMin = finitePrice(row.entryMin, "entry minimum");
  const entryMax = finitePrice(row.entryMax, "entry maximum");
  const stopLoss = finitePrice(row.stopLoss, "stop loss");
  if (entryMin > entryMax) throw new Error("Atlas chart entry minimum exceeds its maximum.");
  if (!Array.isArray(row.targets) || row.targets.length !== 3) throw new Error("Atlas chart requires exactly three targets.");
  const targets = row.targets.map((target, index) => finitePrice(target, `target ${index + 1}`)) as [number, number, number];
  const longSide = direction === "LONG" || direction === "BUY_STOP";
  if (longSide && (stopLoss >= entryMin || targets.some((target) => target <= entryMax))) throw new Error("Long chart geometry has invalid stop/target ordering.");
  if (!longSide && (stopLoss <= entryMax || targets.some((target) => target >= entryMin))) throw new Error("Short chart geometry has invalid stop/target ordering.");
  return { direction, entryMin, entryMax, stopLoss, targets };
}

export async function POST(request: Request): Promise<Response> {
  try {
    if (process.env.MT5_ENABLED?.trim().toLowerCase() !== "true") throw new Error("Static chart export requires the read-only MT5 provider.");
    if (process.env.ATLAS_INTELLIGENCE_ENABLED?.trim().toLowerCase() !== "true") throw new Error("Atlas Python intelligence is disabled.");
    const body = await request.json() as { symbol?: unknown; timeframe?: unknown; geometry?: unknown };
    const symbol = String(body.symbol ?? "XAUUSD").toUpperCase().replaceAll("/", "");
    const timeframe = String(body.timeframe ?? "1H").toUpperCase() as Timeframe;
    if (!SYMBOLS.has(symbol)) throw new Error("Unsupported Atlas symbol.");
    if (!TIMEFRAMES.has(timeframe)) throw new Error("Unsupported Atlas timeframe.");
    const geometry = parseGeometry(body.geometry);

    const mt5 = new Mt5Provider({ port: Number(process.env.MT5_BRIDGE_PORT || 8765), token: process.env.MT5_BRIDGE_TOKEN || undefined });
    const health = await mt5.checkHealth();
    if (!health.mt5Connected) throw new Error("The MT5 terminal is not connected.");
    const [series, ticks, quote] = await Promise.all([mt5.fetchSeries(symbol, timeframe, 260), mt5.fetchTicks(symbol, 2_000), mt5.fetchQuote(symbol)]);
    const intelligence = new PythonIntelligenceProvider({ port: Number(process.env.ATLAS_INTELLIGENCE_PORT || 8776), token: process.env.ATLAS_INTELLIGENCE_TOKEN || undefined });
    const signal = await intelligence.generateV2Signal({ instrument: symbol, timeframe, bars: series.bars, ticks, quote, runLlm: false, writeAudit: false });
    const chartSignal = geometry ? {
      ...signal,
      metadata: { ...((signal.metadata as Record<string, unknown> | undefined) ?? {}), instrument: symbol, timeframe },
      signal: {
        ...((signal.signal as Record<string, unknown> | undefined) ?? {}),
        direction: geometry.direction,
        entry_zone: { min: geometry.entryMin, max: geometry.entryMax, type: geometry.direction.endsWith("STOP") ? "STOP" : "MARKET_ZONE" },
        stop_loss: geometry.stopLoss,
        take_profit: { tp1: geometry.targets[0], tp2: geometry.targets[1], tp3: geometry.targets[2] },
      },
    } : signal;
    const chart = await intelligence.renderChart({
      symbol,
      timeframe,
      bars: series.bars,
      signal: chartSignal,
      wyckoff: signal.wyckoff as Record<string, unknown> | undefined,
      smc: signal.smc as Record<string, unknown> | undefined,
    });
    const png = Buffer.from(chart.pngBase64, "base64");
    if (png.length !== chart.bytes || png.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a") throw new Error("Atlas chart PNG failed integrity validation.");
    const filename = chart.filename.replace(/[^A-Za-z0-9._-]/g, "_");
    return new Response(png, {
      headers: {
        "Content-Type": "image/png",
        "Content-Length": String(png.length),
        "Content-Disposition": `attachment; filename="${filename}"`,
        "Cache-Control": "no-store",
        "X-Atlas-Renderer": chart.renderer,
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Static chart export failed.", readOnly: true, orderSubmission: false }, { status: 503, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
  }
}
