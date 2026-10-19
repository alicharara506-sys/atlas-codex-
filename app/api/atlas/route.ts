import { generateDemoSeries } from "../../lib/demo-market.ts";
import { Mt5Provider } from "../../lib/mt5-provider.ts";
import { PythonIntelligenceProvider } from "../../lib/intelligence-provider.ts";
import { buildConditionalPendingSetup } from "../../lib/pending-engine.ts";
import { computeRisk } from "../../lib/risk-engine.ts";
import { blendMLAlignmentScore, runMLCommittee } from "../../lib/ml-committee.ts";
import { confidenceFromScore, determineDirection, generateSignal } from "../../lib/signal-engine.ts";
import type { MultiTimeframeInput } from "../../lib/signal-engine.ts";
import type { CanonicalSeries, Timeframe } from "../../lib/types.ts";

const SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL"] as const;
const TIMEFRAMES: Timeframe[] = ["D1", "4H", "1H", "15M"];

function allowedSymbol(value: string | null): string {
  const symbol = (value ?? "XAUUSD").trim().toUpperCase().replaceAll("/", "");
  if (!(SYMBOLS as readonly string[]).includes(symbol)) throw new Error("Unsupported Atlas symbol.");
  return symbol;
}

function demoStart(symbol: string): number {
  if (symbol === "XAUUSD") return 4_350;
  if (symbol === "XAGUSD") return 38;
  if (symbol === "USOIL" || symbol === "UKOIL") return 78;
  if (symbol.endsWith("JPY")) return 150;
  return 1.1;
}

function demoSeries(symbol: string): Record<Timeframe, CanonicalSeries> {
  return Object.fromEntries(TIMEFRAMES.map((timeframe) => [timeframe, {
    symbol,
    timeframe,
    bars: generateDemoSeries({ symbol, timeframe, count: 260, startPrice: demoStart(symbol) }),
    status: "DEMO" as const,
    source: "deterministic-demo",
  }])) as Record<Timeframe, CanonicalSeries>;
}

export async function GET(request: Request): Promise<Response> {
  try {
    const symbol = allowedSymbol(new URL(request.url).searchParams.get("symbol"));
    const mt5Enabled = process.env.MT5_ENABLED?.trim().toLowerCase() === "true";
    let series: Record<Timeframe, CanonicalSeries>;
    let ticks = undefined;
    let account = null;
    let brokerSpec = null;
    let quote: { bid: number; ask: number; time: number; source: string } | null = null;
    let provider = "DEMO";

    if (mt5Enabled) {
      const mt5 = new Mt5Provider({
        port: Number(process.env.MT5_BRIDGE_PORT || 8765),
        token: process.env.MT5_BRIDGE_TOKEN || undefined,
      });
      const health = await mt5.checkHealth();
      if (!health.mt5Connected) throw new Error("The MT5 terminal is not connected to a broker account.");
      const rows = [] as Array<[Timeframe, CanonicalSeries]>;
      for (const timeframe of TIMEFRAMES) rows.push([timeframe, await mt5.fetchSeries(symbol, timeframe, 260)]);
      series = Object.fromEntries(rows) as Record<Timeframe, CanonicalSeries>;
      ticks = await mt5.fetchTicks(symbol, 2_000).catch(() => undefined);
      const [accountRow, brokerRow, quoteRow] = await Promise.all([mt5.fetchAccount(), mt5.fetchBrokerSpec(symbol), mt5.fetchQuote(symbol)]);
      account = accountRow;
      brokerSpec = brokerRow;
      quote = { bid: quoteRow.bid, ask: quoteRow.ask, time: quoteRow.time, source: "mt5" };
      provider = "MT5";
    } else {
      series = demoSeries(symbol);
    }

    const mtf = Object.fromEntries(TIMEFRAMES.map((timeframe) => [timeframe, series[timeframe].bars])) as unknown as MultiTimeframeInput;
    const signal = generateSignal({ symbol, timeframe: "1H", mtf, dataStatus: provider === "MT5" ? "LIVE" : "DEMO", ticks });
    if (!quote) {
      const close = series["15M"].bars.at(-1)?.close ?? series["1H"].bars.at(-1)!.close;
      quote = { bid: close, ask: close, time: Math.floor(Date.now() / 1000), source: "deterministic-demo" };
    }
    const directionRead = determineDirection(mtf);
    const mlCommittee = runMLCommittee(series["1H"].bars);
    signal.mlCommittee = mlCommittee;
    signal.score = blendMLAlignmentScore(signal.score, Math.sign(directionRead.weightedLean), mlCommittee, 0.15);
    signal.confidence = confidenceFromScore(signal.score, directionRead.conflictingCount);
    signal.evidence.unshift(mlCommittee.available
      ? `Local ML committee: ${(mlCommittee.probability * 100).toFixed(0)}% signed vote at ${(mlCommittee.confidence * 100).toFixed(0)}% agreement; 15% score weight.`
      : "Local ML committee unavailable; deterministic eight-factor score retained.");
    const pendingSetup = buildConditionalPendingSetup(signal, mtf);
    const effectiveAction = pendingSetup?.orderType ?? signal.direction;
    const riskGeometry = pendingSetup ? { entry: pendingSetup.trigger, stop: pendingSetup.stop, target: pendingSetup.targets[1] } : signal.direction === "WAIT" ? null : { entry: (signal.entryZone.low + signal.entryZone.high) / 2, stop: signal.invalidation, target: signal.targets[1] };
    const risk = riskGeometry ? computeRisk({
      accountBalance: account?.balance ?? 100_000,
      riskPercent: signal.researchRiskPercent ?? 0.5,
      entry: riskGeometry.entry,
      stop: riskGeometry.stop,
      target: riskGeometry.target,
      leverage: account?.leverage ?? 100,
      brokerSpec: brokerSpec ?? undefined,
    }) : null;
    const intelligenceEnabled = process.env.ATLAS_INTELLIGENCE_ENABLED?.trim().toLowerCase() === "true";
    let pythonOrderFlow = null;
    let institutionalV2: Record<string, unknown> | null = null;
    let pythonIntelligenceStatus = intelligenceEnabled ? "UNAVAILABLE" : "DISABLED";
    let institutionalV2Status = intelligenceEnabled ? "UNAVAILABLE" : "DISABLED";
    if (intelligenceEnabled && ticks) {
      const intelligence = new PythonIntelligenceProvider({
        port: Number(process.env.ATLAS_INTELLIGENCE_PORT || 8776),
        token: process.env.ATLAS_INTELLIGENCE_TOKEN || undefined,
      });
      try {
        pythonOrderFlow = await intelligence.analyzeOrderFlow(symbol, series["1H"].bars, ticks);
        pythonIntelligenceStatus = "LIVE";
      } catch (cause) {
        pythonIntelligenceStatus = cause instanceof Error ? `UNAVAILABLE: ${cause.message}` : "UNAVAILABLE";
      }
      try {
        institutionalV2 = await intelligence.generateV2Signal({
          instrument: symbol,
          timeframe: "1H",
          bars: series["1H"].bars,
          ticks,
          quote,
          runLlm: false,
          writeAudit: false,
        });
        institutionalV2Status = "LIVE_DETERMINISTIC";
      } catch (cause) {
        institutionalV2Status = cause instanceof Error ? `UNAVAILABLE: ${cause.message}` : "UNAVAILABLE";
      }
    }

    return Response.json({
      provider,
      readOnly: true,
      orderSubmission: false,
      symbols: SYMBOLS,
      signal,
      effectiveAction,
      pendingSetup,
      risk,
      quote,
      localIntelligence: {
        mlCommittee,
        pythonOrderFlow,
        pythonIntelligenceStatus,
        institutionalV2,
        institutionalV2Status,
        llmSentiment: { status: "AWAITING_LOCAL_TEXT", provider: "OLLAMA_LOOPBACK_ONLY", cloudFallback: false },
        llmOrchestrator: {
          status: intelligenceEnabled ? "READY_ON_DEMAND" : "DISABLED",
          provider: "LOCAL_OLLAMA_ONLY",
          advisoryOnly: true,
          deterministicEngineAuthoritative: true,
          cloudFallback: false,
        },
      },
      account: account ? { currency: account.currency, balance: account.balance, equity: account.equity, marginFree: account.marginFree, leverage: account.leverage, tradeMode: account.tradeMode ?? "UNKNOWN", tradeAllowed: account.tradeAllowed ?? null } : null,
      series: Object.fromEntries(TIMEFRAMES.map((timeframe) => [timeframe, { source: series[timeframe].source, status: series[timeframe].status, bars: series[timeframe].bars.length, last: series[timeframe].bars.at(-1) } ])),
    }, { headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Atlas analysis failed.", readOnly: true, orderSubmission: false }, { status: 503, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
  }
}
