"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ClosingNoteBanner } from "./ClosingNoteBanner.tsx";
import { LiveSignalCard } from "./LiveSignalCard.tsx";
import { LocalSentimentPanel } from "./LocalSentimentPanel.tsx";
import { SignalAnalyticsDashboard } from "./SignalAnalyticsDashboard.tsx";
import { FundOsConsole } from "./FundOsConsole.tsx";
import { SignalLifecycleTracker } from "../lib/live-tracker.ts";
import type { SignalLifecycleEvent } from "../lib/live-tracker.ts";
import type { ConditionalPendingSetup } from "../lib/pending-engine.ts";
import { getAllSignalOutcomes, putSignalOutcome } from "../lib/storage.ts";
import type { SignalOutcome, SignalOutcomeAction } from "../lib/storage.ts";
import type { RiskResult, TradingSignal } from "../lib/types.ts";
import type { PythonOrderFlowRead } from "../lib/intelligence-provider.ts";

interface InstitutionalV2Read {
  signal: { direction: string; conviction: number; confluence_score: number; exposure_units: number; volatility_regime: string };
  regime: { micro: string; macro: string };
  risk: { liquidity_status: string; scenario_test_passed: boolean };
  data_health: Record<string, string | boolean>;
  wyckoff: { phase: string; confidence: number; events: Array<Record<string, unknown>> };
  smc: { order_blocks: Array<Record<string, unknown>>; fvgs: Array<Record<string, unknown>>; liquidity_sweeps: Array<Record<string, unknown>> };
}

interface AtlasPayload {
  provider: string;
  readOnly: true;
  orderSubmission: false;
  symbols: string[];
  signal: TradingSignal;
  effectiveAction: "LONG" | "SHORT" | "WAIT" | "BUY_STOP" | "SELL_STOP";
  pendingSetup: ConditionalPendingSetup | null;
  risk: RiskResult | null;
  quote: { bid: number; ask: number; time: number; source: string };
  account: { currency: string; balance: number; equity: number; marginFree: number; leverage: number; tradeMode: string; tradeAllowed: boolean | null } | null;
  series: Record<string, { source: string; status: string; bars: number; last: { time: number; close: number } }>;
  localIntelligence: {
    mlCommittee: TradingSignal["mlCommittee"];
    pythonOrderFlow: PythonOrderFlowRead | null;
    pythonIntelligenceStatus: string;
    institutionalV2: InstitutionalV2Read | null;
    institutionalV2Status: string;
    llmSentiment: { status: string; provider: string; cloudFallback: false };
    llmOrchestrator: { status: string; provider: string; advisoryOnly: true; deterministicEngineAuthoritative: true; cloudFallback: false };
  };
}

interface QuotePayload { symbol: string; bid: number; ask: number; time: number; source: string; error?: string; }

const DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL"];
const FINAL_STATUSES = new Set(["TP3_HIT", "SL_HIT", "EXPIRED", "CLOSED_MANUAL"]);
type WorkspaceTab = "SIGNALS" | "ANALYSIS" | "INSIGHTS" | "CHAT" | "FUND OS";

function precision(symbol: string): number {
  if (symbol.endsWith("JPY")) return 3;
  if (symbol === "XAUUSD") return 2;
  if (symbol === "XAGUSD" || symbol === "USOIL" || symbol === "UKOIL") return 3;
  return 5;
}

function formatPrice(value: number, symbol: string): string {
  return Number.isFinite(value) ? value.toFixed(precision(symbol)) : "—";
}

function label(value: string): string { return value.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replaceAll("_", " "); }

function lifecycleId(symbol: string, action: string, entry: number): string {
  return `${symbol}:${action}:${entry.toFixed(8)}`;
}

function outcomeFromPayload(data: AtlasPayload): SignalOutcome | null {
  if (data.effectiveAction === "WAIT") return null;
  const action = data.effectiveAction as SignalOutcomeAction;
  const pending = data.pendingSetup;
  const entry = pending ? pending.trigger : (data.signal.entryZone.low + data.signal.entryZone.high) / 2;
  const stop = pending ? pending.stop : data.signal.invalidation;
  const targets = pending ? pending.targets : data.signal.targets;
  const mid = (data.quote.bid + data.quote.ask) / 2;
  const risk = Math.abs(entry - stop);
  return {
    id: lifecycleId(data.signal.symbol, action, entry), symbol: data.signal.symbol, action,
    entryPrice: entry, triggerPrice: pending?.trigger, slPrice: stop,
    tp1Price: targets[0], tp2Price: targets[1], tp3Price: targets[2],
    status: pending ? "PENDING" : "ACTIVE", livePrices: [{ price: mid, time: data.quote.time }],
    highestPips: 0, lowestPips: 0, finalPips: 0,
    plannedRR: risk > 0 ? Math.abs(targets[1] - entry) / risk : 0, achievedRR: 0,
    scoreAtGeneration: data.signal.score, confidenceAtGeneration: data.signal.confidence,
    regimeAtGeneration: data.signal.regimeOverlay ?? data.signal.regime,
    sentimentAtGeneration: 0, mlProbabilityAtGeneration: data.signal.mlCommittee?.probability ?? 0,
    timestampGenerated: data.signal.generatedAt, userReactions: [],
  };
}

export function Terminal() {
  const [symbol, setSymbol] = useState("XAUUSD");
  const [tab, setTab] = useState<WorkspaceTab>("SIGNALS");
  const [data, setData] = useState<AtlasPayload | null>(null);
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [outcomes, setOutcomes] = useState<SignalOutcome[]>([]);
  const [activeOutcomeId, setActiveOutcomeId] = useState<string | null>(null);
  const [lifecycleEvent, setLifecycleEvent] = useState<SignalLifecycleEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [chartExporting, setChartExporting] = useState(false);
  const [chartExportStatus, setChartExportStatus] = useState<string | null>(null);
  const trackerRef = useRef<SignalLifecycleTracker | null>(null);

  useEffect(() => {
    let active = true;
    const tracker = new SignalLifecycleTracker({
      persist: (outcome) => putSignalOutcome(outcome),
      onEvent: (event) => {
        if (!active) return;
        setLifecycleEvent(event);
        setOutcomes(tracker.all());
      },
    });
    trackerRef.current = tracker;
    void getAllSignalOutcomes().then((stored) => {
      if (!active) return;
      tracker.hydrate(stored);
      setOutcomes(tracker.all());
    }).catch(() => undefined);
    return () => { active = false; trackerRef.current = null; };
  }, []);

  const load = useCallback(async (selected: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/atlas?symbol=${encodeURIComponent(selected)}`, { cache: "no-store" });
      const body = await response.json() as AtlasPayload & { error?: string };
      if (!response.ok) throw new Error(body.error || `Atlas returned HTTP ${response.status}.`);
      setData(body);
      setLivePrice((body.quote.bid + body.quote.ask) / 2);
    } catch (cause) {
      setData(null);
      setError(cause instanceof Error ? cause.message : "Atlas analysis failed.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(symbol), 0);
    return () => window.clearTimeout(timer);
  }, [load, symbol]);
  useEffect(() => { const timer = window.setInterval(() => void load(symbol), 60_000); return () => window.clearInterval(timer); }, [load, symbol]);

  useEffect(() => {
    if (!data || !trackerRef.current) return;
    const candidate = outcomeFromPayload(data);
    if (!candidate) {
      const existing = trackerRef.current.all().find((row) => row.symbol === symbol && !FINAL_STATUSES.has(row.status));
      setActiveOutcomeId(existing?.id ?? null);
      return;
    }
    const tracked = trackerRef.current.track(candidate);
    setActiveOutcomeId(tracked.id);
    trackerRef.current.observeTick(symbol, (data.quote.bid + data.quote.ask) / 2, data.quote.time);
    setOutcomes(trackerRef.current.all());
  }, [data, symbol]);

  useEffect(() => {
    let cancelled = false;
    const poll = async (): Promise<void> => {
      try {
        const response = await fetch(`/api/atlas/quote?symbol=${encodeURIComponent(symbol)}`, { cache: "no-store" });
        const quote = await response.json() as QuotePayload;
        if (!response.ok) throw new Error(quote.error || "Live quote unavailable.");
        if (cancelled) return;
        const mid = (quote.bid + quote.ask) / 2;
        setLivePrice(mid);
        trackerRef.current?.observeTick(symbol, mid, quote.time);
        setOutcomes(trackerRef.current?.all() ?? []);
      } catch { /* full analysis surface owns connectivity errors */ }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 5_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [symbol]);

  const signal = data?.signal;
  const pending = data?.pendingSetup;
  const pythonFlow = data?.localIntelligence.pythonOrderFlow;
  const quantV2 = data?.localIntelligence.institutionalV2;
  const factors = signal ? Object.entries(signal.factors) as Array<[string, number | null]> : [];
  const stateClass = data?.effectiveAction === "LONG" || data?.effectiveAction === "BUY_STOP" ? "long" : data?.effectiveAction === "SHORT" || data?.effectiveAction === "SELL_STOP" ? "short" : "wait";
  const activeOutcome = useMemo(() => outcomes.find((row) => row.id === activeOutcomeId) ?? null, [outcomes, activeOutcomeId]);

  function refreshOutcomes(): void { setOutcomes(trackerRef.current?.all() ?? []); }
  function closeSignal(): void { if (activeOutcome && livePrice) { trackerRef.current?.closeManually(activeOutcome.id, livePrice); refreshOutcomes(); } }
  function editSignal(): void {
    if (!activeOutcome) return;
    const note = window.prompt("Local note for this signal:", activeOutcome.userNote ?? "");
    if (note !== null) { trackerRef.current?.update(activeOutcome.id, { userNote: note.slice(0, 500) }); refreshOutcomes(); }
  }
  function shareSignal(): void {
    if (!activeOutcome) return;
    const text = `${activeOutcome.symbol} ${activeOutcome.action.replace("_", " ")} entry ${formatPrice(activeOutcome.entryPrice, activeOutcome.symbol)} SL ${formatPrice(activeOutcome.slPrice, activeOutcome.symbol)} TP ${[activeOutcome.tp1Price, activeOutcome.tp2Price, activeOutcome.tp3Price].map((value) => formatPrice(value, activeOutcome.symbol)).join("/")} · Atlas research only`;
    void navigator.clipboard.writeText(text).catch(() => undefined);
  }
  function react(reaction: string): void {
    if (!activeOutcome) return;
    const next = activeOutcome.userReactions.includes(reaction) ? activeOutcome.userReactions.filter((item) => item !== reaction) : [...activeOutcome.userReactions, reaction];
    trackerRef.current?.update(activeOutcome.id, { userReactions: next }); refreshOutcomes();
  }
  async function exportChart(): Promise<void> {
    setChartExporting(true);
    setChartExportStatus(null);
    try {
      const currentOutcome = data ? outcomeFromPayload(data) : null;
      const currentZoneMatches = Boolean(activeOutcome && currentOutcome?.id === activeOutcome.id && !pending && signal);
      const geometry = activeOutcome ? {
        direction: activeOutcome.action,
        entryMin: currentZoneMatches ? signal!.entryZone.low : activeOutcome.entryPrice,
        entryMax: currentZoneMatches ? signal!.entryZone.high : activeOutcome.entryPrice,
        stopLoss: activeOutcome.slPrice,
        targets: [activeOutcome.tp1Price, activeOutcome.tp2Price, activeOutcome.tp3Price],
      } : undefined;
      const response = await fetch("/api/atlas/chart", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol, timeframe: "1H", geometry }) });
      if (!response.ok) {
        const failure = await response.json().catch(() => ({})) as { error?: string };
        throw new Error(failure.error || `Chart export returned HTTP ${response.status}.`);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? `${symbol}_1H_atlas.png`;
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
      setChartExportStatus(`Static chart exported: ${filename}`);
    } catch (cause) {
      setChartExportStatus(cause instanceof Error ? cause.message : "Static chart export failed.");
    } finally {
      setChartExporting(false);
    }
  }

  return <main className="terminal-shell">
    <header className="topbar"><div className="brand"><span>AQ</span><div><b>ATLAS QUANT</b><small>LOCAL MT5 · ZERO-CLOUD RESEARCH</small></div></div><div className="connection"><i className={data?.provider === "MT5" ? "live" : data ? "demo" : "offline"} /><div><b>{data?.provider === "MT5" ? `MT5 ${data.account?.tradeMode ?? "ACCOUNT"} CONNECTED` : data ? "DEMO MODE" : loading ? "CONNECTING" : "OFFLINE"}</b><small>Read-only · order submission disabled</small></div></div></header>
    <section className="command-bar"><div><span>INSTRUMENT</span><select value={symbol} onChange={(event) => setSymbol(event.target.value)}>{(data?.symbols ?? DEFAULT_SYMBOLS).map((item) => <option key={item}>{item}</option>)}</select></div><button type="button" onClick={() => void load(symbol)} disabled={loading}>{loading ? "ANALYZING…" : "REFRESH ANALYSIS"}</button><div className="safety"><b>ANALYSIS ONLY</b><small>No route can place, modify, or cancel an order.</small></div></section>
    <nav className="workspace-tabs" aria-label="Atlas workspace">{(["SIGNALS", "ANALYSIS", "INSIGHTS", "CHAT", "FUND OS"] as WorkspaceTab[]).map((item) => <button className={tab === item ? "active" : ""} key={item} type="button" onClick={() => setTab(item)}>{item}</button>)}</nav>
    {error ? <section className="error-card"><b>MT5 DATA UNAVAILABLE</b><span>{error}</span></section> : null}
    <ClosingNoteBanner event={lifecycleEvent} />

    {tab === "SIGNALS" ? <>
      <section className="decision-grid"><article className={`decision ${stateClass}`}><span className="eyebrow">{pending ? "CONDITIONAL PENDING SIGNAL" : "DETERMINISTIC + LOCAL ML"}</span><strong>{loading ? "ASSESSING" : data?.effectiveAction ? label(data.effectiveAction) : "UNAVAILABLE"}</strong><p>{pending ? pending.reason : signal ? `${label(signal.regimeOverlay ?? signal.regime)} · ${label(signal.structure)} · ${signal.confidence} qualitative confidence` : "Waiting for closed-bar evidence."}</p><div className="score"><span>BLENDED ALIGNMENT</span><b>{signal?.score.toFixed(1) ?? "—"}<small>/100</small></b></div><div className="probability"><span>HISTORICAL WIN PROBABILITY</span><b>{signal?.historicalWinProbability ? `${(signal.historicalWinProbability.probability * 100).toFixed(1)}%` : "UNCALIBRATED"}</b></div></article>
      <article className="geometry"><span className="eyebrow">ENTRY / RISK GEOMETRY</span><div><span>LIVE MID PRICE</span><b>{livePrice ? formatPrice(livePrice, symbol) : "—"}</b></div><div><span>{pending ? "PENDING TRIGGER" : "ENTRY ZONE"}</span><b>{pending ? formatPrice(pending.trigger, symbol) : signal ? `${formatPrice(signal.entryZone.low, symbol)} – ${formatPrice(signal.entryZone.high, symbol)}` : "—"}</b></div><div><span>STOP / INVALIDATION</span><b>{pending ? `${formatPrice(pending.stop, symbol)} / ${formatPrice(pending.invalidation, symbol)}` : signal ? formatPrice(signal.invalidation, symbol) : "—"}</b></div><div><span>TARGETS</span><b>{(pending?.targets ?? signal?.targets)?.map((item) => formatPrice(item, symbol)).join(" / ") ?? "—"}</b></div></article>
      <article className="account"><span className="eyebrow">BROKER ACCOUNT · READ ONLY</span><div><span>BALANCE</span><b>{data?.account ? `${data.account.balance.toFixed(2)} ${data.account.currency}` : "—"}</b></div><div><span>EQUITY</span><b>{data?.account ? `${data.account.equity.toFixed(2)} ${data.account.currency}` : "—"}</b></div><div><span>RESEARCH RISK</span><b>{signal?.researchRiskPercent?.toFixed(2) ?? "0.50"}%</b></div><div><span>LEVERAGE</span><b>{data?.account ? `1:${data.account.leverage}` : "—"}</b></div></article>
      <article className="account"><span className="eyebrow">INSTITUTIONAL QUANT V2</span><div><span>CONFLUENCE</span><b>{quantV2 ? `${quantV2.signal.confluence_score.toFixed(1)} / 10` : "—"}</b></div><div><span>V2 STATE</span><b>{quantV2 ? label(quantV2.signal.direction) : "UNAVAILABLE"}</b></div><div><span>MICRO / MACRO</span><b>{quantV2 ? `${label(quantV2.regime.micro)} / ${label(quantV2.regime.macro)}` : "—"}</b></div><div><span>WYCKOFF</span><b>{quantV2 ? `${label(quantV2.wyckoff.phase)} · ${(quantV2.wyckoff.confidence * 100).toFixed(0)}%` : "—"}</b></div><div><span>SMC STRUCTURE</span><b>{quantV2 ? `${quantV2.smc.order_blocks.length} OB · ${quantV2.smc.fvgs.length} FVG · ${quantV2.smc.liquidity_sweeps.length} SWEEP` : "—"}</b></div><div><span>VOL EXPOSURE</span><b>{quantV2 ? `${quantV2.signal.exposure_units.toFixed(2)} units` : "—"}</b></div><small>{data?.localIntelligence.institutionalV2Status ?? "DISABLED"} · {quantV2 ? label(quantV2.risk.liquidity_status) : "no data"}</small></article></section>
      <LiveSignalCard outcome={activeOutcome} livePrice={livePrice} score={signal?.score ?? 0} confidence={signal?.confidence ?? "LOW"} onClose={closeSignal} onEdit={editSignal} onShare={shareSignal} onReaction={react} onExportChart={() => void exportChart()} exportBusy={chartExporting} exportStatus={chartExportStatus} />
    </> : null}

    {tab === "ANALYSIS" ? <><section className="timeframes panel"><header><div><span className="eyebrow">CLOSED-BAR MARKET STATE</span><h2>Multi-timeframe broker evidence</h2></div><small>{data?.provider ?? "—"} · forming bars excluded</small></header><div className="tf-grid">{["D1", "4H", "1H", "15M"].map((timeframe) => { const row = data?.series[timeframe]; return <article key={timeframe}><span>{timeframe}</span><b>{row ? formatPrice(row.last.close, symbol) : "—"}</b><small>{row ? `${row.bars} bars · ${row.status}` : "UNAVAILABLE"}</small></article>; })}</div></section><section className="analysis-grid"><article className="panel factors"><header><div><span className="eyebrow">EIGHT-FACTOR + 15% ML</span><h2>Explainable score composition</h2></div></header>{factors.map(([key, value]) => <div key={key}><span>{label(key).toUpperCase()}</span><i><em style={{ width: `${value ?? 0}%` }} /></i><b>{value === null ? "N/A" : value.toFixed(0)}</b></div>)}</article><article className="panel evidence"><header><div><span className="eyebrow">SIGNAL EVIDENCE</span><h2>What the engine observed</h2></div></header><ol>{[...(pending?.evidence ?? []), ...(signal?.evidence ?? [])].map((item, index) => <li key={`${index}-${item}`}><span>{String(index + 1).padStart(2, "0")}</span><p>{item}</p></li>)}</ol></article><article className="panel order-flow"><header><div><span className="eyebrow">ORDER FLOW</span><h2>AVWAP · profile · CVD</h2></div><small>{data?.localIntelligence.pythonIntelligenceStatus ?? "DISABLED"}</small></header>{pythonFlow ? <><strong>{label(pythonFlow.confluence.bias)} · {pythonFlow.confluence.score.toFixed(1)}</strong><div><span>ANCHORED VWAP</span><b>{pythonFlow.anchoredVwap.value === null ? "—" : formatPrice(pythonFlow.anchoredVwap.value, symbol)}</b></div><div><span>PROFILE POC</span><b>{pythonFlow.volumeProfile.poc === null ? "—" : formatPrice(pythonFlow.volumeProfile.poc, symbol)}</b></div><div><span>CVD / DELTA</span><b>{pythonFlow.delta.cumulativeDelta.toFixed(0)} / {pythonFlow.delta.delta.toFixed(0)}</b></div><p>{pythonFlow.coverage.ticks} broker ticks · {label(pythonFlow.delta.volumeBasis)}.</p>{pythonFlow.confluence.evidence.map((item) => <small key={item}>{item}</small>)}</> : signal?.orderFlow ? <><strong>{label(signal.orderFlow.pressure)}</strong><p>{signal.orderFlow.ticksAnalyzed} broker ticks analyzed. OTC approximation, not centralized tape.</p>{signal.orderFlow.evidence.map((item) => <small key={item}>{item}</small>)}</> : <p>Broker ticks unavailable; Atlas did not fabricate a neutral reading.</p>}</article><article className="panel risk"><header><div><span className="eyebrow">RISK MODEL</span><h2>{signal?.researchRiskPercent?.toFixed(2) ?? "0.50"}% research sizing</h2></div></header>{data?.risk ? <><div><span>MAX LOSS</span><b>{data.risk.maxLossAmount.toFixed(2)}</b></div><div><span>BROKER VOLUME</span><b>{data.risk.positionUnits}</b></div><div><span>REWARD / RISK</span><b>{data.risk.rewardToRiskRatio?.toFixed(2) ?? "—"}R</b></div><div><span>ML VOTE</span><b>{signal?.mlCommittee?.available ? `${((signal.mlCommittee.probability ?? 0) * 100).toFixed(0)}%` : "N/A"}</b></div>{data.risk.warnings.map((warning) => <small key={warning.code}>{warning.message}</small>)}</> : <p>Risk sizing withheld because no setup qualifies.</p>}</article></section></> : null}
    {tab === "INSIGHTS" ? <SignalAnalyticsDashboard outcomes={outcomes} /> : null}
    {tab === "CHAT" ? <LocalSentimentPanel symbol={symbol} /> : null}
    {tab === "FUND OS" ? <FundOsConsole symbol={symbol} /> : null}
    <footer><b>ATLAS QUANT</b><span>MT5-only market data · local TypeScript analytics · local Ollama optional.</span><span>Read-only · no automated execution.</span></footer>
  </main>;
}
