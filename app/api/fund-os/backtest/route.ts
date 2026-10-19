import { FUND_OS_SYMBOLS } from "../../../lib/fund-os.ts";
import { errorMessage, jsonHeaders, postBackend } from "../../../lib/fund-os-server.ts";
import { Mt5Provider } from "../../../lib/mt5-provider.ts";

interface ValidatedMandate {
  document?: {
    mandate?: {
      universe?: { symbols?: unknown };
      backtest?: { warmup_bars?: unknown; eval_bars?: unknown };
    };
  };
}

function mandateInputs(payload: unknown): { symbols: string[]; requiredBars: number } {
  const validation = payload as ValidatedMandate;
  const rawSymbols = validation.document?.mandate?.universe?.symbols;
  const warmup = validation.document?.mandate?.backtest?.warmup_bars;
  const evaluation = validation.document?.mandate?.backtest?.eval_bars;
  if (!Array.isArray(rawSymbols) || typeof warmup !== "number" || typeof evaluation !== "number") {
    throw new Error("Validated mandate did not contain a usable universe and backtest window.");
  }
  const symbols = rawSymbols.map((value) => String(value).trim().toUpperCase().replaceAll("/", ""));
  if (symbols.some((symbol) => !(FUND_OS_SYMBOLS as readonly string[]).includes(symbol))) {
    throw new Error("This UI backtest supports only the Atlas FX and commodity universe shown in the terminal.");
  }
  const requiredBars = warmup + evaluation;
  if (!Number.isInteger(requiredBars) || requiredBars < 30 || requiredBars > 5_000) {
    throw new Error("The requested backtest must use between 30 and 5,000 closed bars per symbol.");
  }
  return { symbols, requiredBars };
}

export async function POST(request: Request): Promise<Response> {
  try {
    const input = await request.json() as { yamlText?: unknown };
    if (typeof input.yamlText !== "string" || input.yamlText.trim().length === 0 || input.yamlText.length > 100_000) {
      return Response.json({ error: "Mandate YAML must contain between 1 and 100,000 characters." }, { status: 400, headers: jsonHeaders() });
    }

    const validation = await postBackend("/mandates/validate", { yaml_text: input.yamlText });
    if (!validation.response.ok) {
      return Response.json({ error: errorMessage(validation.payload, "Mandate validation failed."), details: validation.payload }, { status: validation.response.status, headers: jsonHeaders() });
    }
    const { symbols, requiredBars } = mandateInputs(validation.payload);
    if (process.env.MT5_ENABLED?.trim().toLowerCase() !== "true") {
      throw new Error("Real backtests require the local read-only MT5 bridge; demo data is never substituted here.");
    }

    const mt5 = new Mt5Provider({
      port: Number(process.env.MT5_BRIDGE_PORT || 8765),
      token: process.env.MT5_BRIDGE_TOKEN || undefined,
    });
    const health = await mt5.checkHealth();
    if (!health.mt5Connected) throw new Error("MetaTrader 5 is not connected to the demo broker account.");
    const marketSeries = await Promise.all(symbols.map(async (symbol) => {
      const series = await mt5.fetchSeries(symbol, "1H", requiredBars);
      if (series.bars.length < requiredBars) {
        throw new Error(`${symbol} supplied ${series.bars.length} closed bars; ${requiredBars} are required.`);
      }
      return {
        symbol,
        bars: series.bars.slice(-requiredBars).map((bar) => ({
          timestamp: new Date(bar.time * 1_000).toISOString(),
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
          volume: bar.volume,
        })),
      };
    }));

    const result = await postBackend("/backtests/run", { yaml_text: input.yamlText, series: marketSeries });
    if (!result.response.ok) {
      return Response.json({ error: errorMessage(result.payload, "Deterministic backtest failed."), details: result.payload }, { status: result.response.status, headers: jsonHeaders() });
    }
    return Response.json({
      ...(result.payload as Record<string, unknown>),
      market_data: { source: "MT5", timeframe: "1H", closed_bars_only: true, bars_per_symbol: requiredBars },
      execution_enabled: false,
    }, { headers: jsonHeaders() });
  } catch (cause) {
    return Response.json({ error: cause instanceof Error ? cause.message : "Fund OS backtest is unavailable.", execution_enabled: false }, { status: 503, headers: jsonHeaders() });
  }
}
