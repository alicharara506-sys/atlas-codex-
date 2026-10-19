import { FUND_OS_SYMBOLS } from "../../../lib/fund-os.ts";
import { errorMessage, jsonHeaders, postBackend } from "../../../lib/fund-os-server.ts";

const SCORE_FIELDS = ["fundamentals", "technical", "sentiment", "macro", "narrative", "flow", "insider"] as const;

function bounded(value: unknown, minimum: number, maximum: number, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum && value <= maximum ? value : fallback;
}

function optionalScore(value: unknown): number | null {
  return value === null || value === undefined ? null : bounded(value, -1, 1, 0);
}

export async function POST(request: Request): Promise<Response> {
  try {
    const input = await request.json() as {
      symbol?: unknown;
      scores?: Record<string, unknown>;
      volatilityPercentile?: unknown;
      correlationToBook?: unknown;
      maxPositionWeight?: unknown;
      maxGrossLeverage?: unknown;
      maxDailyDrawdown?: unknown;
      maxWeeklyDrawdown?: unknown;
      maxCorrelation?: unknown;
    };
    const symbol = String(input.symbol ?? "").trim().toUpperCase().replaceAll("/", "");
    if (!(FUND_OS_SYMBOLS as readonly string[]).includes(symbol)) {
      return Response.json({ error: "Unsupported Atlas research symbol." }, { status: 400, headers: jsonHeaders() });
    }
    const assetClass = ["XAUUSD", "XAGUSD", "USOIL", "UKOIL"].includes(symbol) ? "commodity" : "forex";
    const scores = Object.fromEntries(SCORE_FIELDS.map((field) => [`${field}_score`, optionalScore(input.scores?.[field])])) as Record<string, number | null>;
    const maxPositionWeight = bounded(input.maxPositionWeight, 0.001, 1, 0.10);
    const requestPayload = {
      observations: [{
        ticker: symbol,
        asset_class: assetClass,
        ...scores,
        volatility_percentile: input.volatilityPercentile === null ? null : bounded(input.volatilityPercentile, 0, 1, 1),
        correlation_to_book: bounded(input.correlationToBook, -1, 1, 0),
        current_weight: 0,
        requested_weight: Math.min(0.05, maxPositionWeight),
      }],
      risk_context: { daily_drawdown: 0, weekly_drawdown: 0, gross_leverage: 0 },
      limits: {
        allowed_universe: [symbol],
        allowed_asset_classes: ["forex", "commodity"],
        max_position_weight: maxPositionWeight,
        max_gross_leverage: bounded(input.maxGrossLeverage, 0.001, 100, 1),
        max_daily_drawdown: bounded(input.maxDailyDrawdown, 0.001, 1, 0.03),
        max_weekly_drawdown: bounded(input.maxWeeklyDrawdown, 0.001, 1, 0.06),
        max_correlation: bounded(input.maxCorrelation, 0, 1, 0.80),
        max_volatility_percentile: 0.90,
        arbitration_threshold: 0.20,
        desk_weights: Object.fromEntries(SCORE_FIELDS.map((field) => [field, 1])),
      },
      mode: "autonomous",
    };
    const result = await postBackend("/research/evaluate", requestPayload);
    if (!result.response.ok) {
      return Response.json({ error: errorMessage(result.payload, "Agent research workflow failed."), details: result.payload, execution_enabled: false }, { status: result.response.status, headers: jsonHeaders() });
    }
    return Response.json(result.payload, { headers: jsonHeaders() });
  } catch (cause) {
    return Response.json({ error: cause instanceof Error ? cause.message : "Agent research workflow is unavailable.", execution_enabled: false }, { status: 503, headers: jsonHeaders() });
  }
}
