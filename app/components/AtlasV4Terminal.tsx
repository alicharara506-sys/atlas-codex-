"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  decisionTone,
  displayLabel,
  filterMarkets,
  formatProbability,
} from "../lib/v4-terminal.ts";
import type {
  MarketFilter,
  V4AIReviewSummary,
  V4CalendarEvent,
  V4MarketSummary,
  V4ModelSummary,
  V4OperationalHealth,
  V4OrchestrationStatus,
  V4Section,
  V4SignalSummary,
  V4TerminalWorkspace,
} from "../lib/v4-terminal.ts";

const FALLBACK_SYMBOLS = [
  "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
  "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "XAUUSD", "XAGUSD", "WTI", "BRENT",
] as const;

const NAV_ITEMS: Array<{ id: V4Section; mark: string; label: string }> = [
  { id: "COMMAND_CENTER", mark: "CC", label: "Command Center" },
  { id: "MARKETS", mark: "MK", label: "Markets" },
  { id: "SIGNALS", mark: "SG", label: "Signals" },
  { id: "ATLAS_AI", mark: "AI", label: "Atlas AI" },
  { id: "CALENDAR", mark: "EC", label: "Calendar" },
  { id: "PERFORMANCE", mark: "PF", label: "Performance" },
  { id: "QUANT_LAB", mark: "QL", label: "Quant Lab" },
  { id: "MODELS", mark: "ML", label: "Models" },
  { id: "MT5", mark: "M5", label: "MT5" },
  { id: "SETTINGS", mark: "ST", label: "Settings" },
];

type SignalFilter = "ACTIVE" | "PENDING" | "WON" | "STOPPED" | "EXPIRED" | "INVALIDATED" | "ALL";

function precision(symbol: string): number {
  if (symbol.endsWith("JPY")) return 3;
  if (symbol === "XAUUSD") return 2;
  if (["XAGUSD", "WTI", "BRENT"].includes(symbol)) return 3;
  return 5;
}

function price(value: number | null | undefined, symbol: string): string {
  return value === null || value === undefined ? "—" : value.toFixed(precision(symbol));
}

function signed(value: number | null | undefined, suffix = ""): string {
  if (value === null || value === undefined) return "-";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}${suffix}`;
}

function metric(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined ? "-" : value.toFixed(digits);
}

function traceId(value: string): string {
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function dateTime(value: string | null | undefined): string {
  if (!value) return "DATA UNAVAILABLE";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "DATA UNAVAILABLE" : parsed.toLocaleString();
}

function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

function isLiveState(state: string | null | undefined): boolean {
  return state === "ACTIVE" || state === "TP1_HIT" || state === "TP2_HIT";
}

function StatusPill({ value, tone }: { value: string; tone?: "positive" | "negative" | "caution" | "neutral" }) {
  return <span className={`v4-status ${tone ?? "neutral"}`}>{displayLabel(value)}</span>;
}

function SectionHeading({ eyebrow, title, note }: { eyebrow: string; title: string; note?: string }) {
  return <header className="v4-section-heading"><div><span>{eyebrow}</span><h2>{title}</h2></div>{note ? <small>{note}</small> : null}</header>;
}

function EmptyEvidence({ title, children }: { title: string; children: ReactNode }) {
  return <section className="v4-empty"><span>NO CLAIM</span><h3>{title}</h3><p>{children}</p></section>;
}

function MetricCard({ label, value, detail, tone = "neutral" }: { label: string; value: string | number; detail: string; tone?: string }) {
  return <article className={`v4-metric ${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function SignalDecision({ market, summary }: { market: V4MarketSummary; summary: V4SignalSummary | null }) {
  const signal = summary?.signal ?? null;
  const decision = market.decision ?? "INSUFFICIENT_DATA";
  const tone = decisionTone(decision);
  if (!signal || !summary) {
    return <section className={`v4-decision-card ${tone}`}>
      <div className="v4-decision-title"><div><span>ATLAS DECISION</span><h1>{displayLabel(decision)}</h1></div><StatusPill value={market.data_quality ?? "DATA UNAVAILABLE"} tone={market.availability === "AVAILABLE" ? "positive" : "negative"} /></div>
      <div className="v4-threshold"><div><span>BEST CALIBRATED PROBABILITY</span><strong>{formatProbability(market.calibrated_probability)}</strong></div><div><span>PUBLICATION THRESHOLD</span><strong>60.0%</strong></div></div>
      <div className="v4-reasons"><b>WHY ATLAS DID NOT PUBLISH</b>{(market.decision_reasons.length ? market.decision_reasons : market.data_issues).map((reason) => <p key={reason}><i />{displayLabel(reason)}</p>)}</div>
      <footer><span>Ungraded until every V4 gate passes</span><span>Execution disabled</span></footer>
    </section>;
  }
  return <section className={`v4-signal-hero ${signal.direction.toLowerCase()}`}>
    <div className="v4-signal-primary">
      <div className="v4-symbol-line"><span>{signal.instrument}</span><b>{signal.timeframe}</b><StatusPill value={summary.tracking?.state ?? "PUBLISHED"} tone="positive" /></div>
      <h1>{signal.direction}</h1>
      <div className="v4-probability"><strong>{formatProbability(signal.calibrated_probability)}</strong><span>CALIBRATED WIN PROBABILITY</span></div>
      <div className="v4-grade"><span>GRADE</span><b>{signal.grade}</b></div>
      <p>{signal.drivers[0]}</p>
    </div>
    <div className="v4-geometry-grid">
      <div><span>LIVE PRICE</span><b>{price(summary.tracking?.live_price ?? market.price, signal.instrument)}</b><small>{summary.tracking ? "Server tracked" : "Latest persisted MT5"}</small></div>
      <div><span>ENTRY ZONE</span><b>{price(signal.entry.low, signal.instrument)} – {price(signal.entry.high, signal.instrument)}</b><small>{signal.entry.type}</small></div>
      <div className="stop"><span>STOP / INVALIDATION</span><b>{price(signal.stop_loss, signal.instrument)}</b><small>Structural</small></div>
      {signal.targets.map((target) => <div key={target.name}><span>{target.name}</span><b>{price(target.price, signal.instrument)}</b><small>{target.reward_risk.toFixed(2)}R minimum</small></div>)}
      <div><span>EXPECTED R</span><b>{signal.expected_r.toFixed(2)}R</b><small>EV {signed(signal.expected_value_net)}</small></div>
      <div><span>APPROVED RISK</span><b>{formatProbability(signal.approved_risk_fraction)}</b><small>Account-independent cap</small></div>
      <div><span>QUANT / RISK</span><b>{signal.quant_validation.status} / {signal.risk_validation.status}</b><small>No AI override</small></div>
    </div>
  </section>;
}

function TimeframeMatrix({ market }: { market: V4MarketSummary }) {
  const measures: Array<[string, (cell: V4MarketSummary["timeframes"][number]) => string]> = [
    ["PRICE", (cell) => price(cell.last_close, market.instrument)],
    ["TREND", (cell) => displayLabel(cell.trend)],
    ["STRUCTURE", (cell) => displayLabel(cell.structure)],
    ["REGIME", (cell) => displayLabel(cell.regime)],
    ["RAW ML", (cell) => formatProbability(cell.raw_probability)],
    ["META", (cell) => formatProbability(cell.meta_probability)],
    ["CALIBRATED", (cell) => formatProbability(cell.calibrated_probability)],
    ["EVENT RISK", (cell) => displayLabel(cell.event_risk)],
    ["QUANT", (cell) => displayLabel(cell.quant_status)],
  ];
  return <section className="v4-panel v4-mtf">
    <SectionHeading eyebrow="CORRELATED EVIDENCE" title="Multi-timeframe matrix" note={`Selected: ${market.best_timeframe ?? "NONE"}`} />
    <div className="v4-mtf-grid v4-mtf-head"><span>MEASURE</span>{market.timeframes.map((cell) => <b className={cell.selected ? "selected" : ""} key={cell.timeframe}>{cell.timeframe}</b>)}</div>
    {measures.map(([name, formatter]) => <div className="v4-mtf-grid" key={name}><span>{name}</span>{market.timeframes.map((cell) => <b className={cell.selected ? "selected" : ""} key={cell.timeframe}>{formatter(cell)}</b>)}</div>)}
  </section>;
}

function SignalTrack({ summary }: { summary: V4SignalSummary }) {
  const { signal, tracking } = summary;
  const current = tracking?.live_price ?? (signal.entry.low + signal.entry.high) / 2;
  const levels = [...signal.targets].reverse().map((target) => ({ label: target.name, value: target.price, reached: (tracking?.highest_target_hit ?? 0) >= Number(target.name.at(-1)) }));
  return <section className="v4-panel v4-live-track">
    <SectionHeading eyebrow="SERVER AUTHORITY" title="Live signal track" note={tracking ? `Event sequence ${tracking.event_sequence}` : "Awaiting first validated tracking snapshot"} />
    <div className="v4-track-layout"><div className="v4-track-rail">
      {levels.map((level) => <div className={level.reached ? "reached" : ""} key={level.label}><span>{level.label}</span><i /><b>{price(level.value, signal.instrument)}</b></div>)}
      <div className="live"><span>LIVE</span><i /><b>{price(current, signal.instrument)}</b></div>
      <div><span>ENTRY</span><i /><b>{price(tracking?.entry_fill ?? (signal.entry.low + signal.entry.high) / 2, signal.instrument)}</b></div>
      <div className="stop"><span>SL</span><i /><b>{price(signal.stop_loss, signal.instrument)}</b></div>
    </div><div className="v4-live-stats">
      <div><span>LIVE PIPS</span><b className={(tracking?.live_pips ?? 0) >= 0 ? "positive-text" : "negative-text"}>{signed(tracking?.live_pips, " p")}</b></div>
      <div><span>LIVE R</span><b className={(tracking?.live_r ?? 0) >= 0 ? "positive-text" : "negative-text"}>{signed(tracking?.live_r, "R")}</b></div>
      <div><span>TIME ACTIVE</span><b>{duration(tracking?.elapsed_seconds)}</b></div>
      <div><span>MFE / MAE</span><b>{price(tracking?.mfe_price, signal.instrument)} / {price(tracking?.mae_price, signal.instrument)}</b></div>
      <div><span>TO TARGET / STOP</span><b>{price(tracking?.distance_to_next_target, signal.instrument)} / {price(tracking?.distance_to_stop, signal.instrument)}</b></div>
      <div><span>HISTORY</span><b>{summary.event_count} events · {summary.revision_count} revisions</b></div>
    </div></div>
  </section>;
}

function CommandCenter({ workspace, market, signal }: { workspace: V4TerminalWorkspace; market: V4MarketSummary; signal: V4SignalSummary | null }) {
  const opportunities = workspace.markets.filter((row) => row.decision === "PUBLISHED").slice(0, 4);
  return <div className="v4-view">
    <section className="v4-metrics-grid">
      <MetricCard label="MT5 STATUS" value={market.mt5_status ?? "UNAVAILABLE"} detail={market.account_mode ?? "No account evidence"} tone={market.mt5_status === "CONNECTED" ? "positive" : "negative"} />
      <MetricCard label="ACTIVE SIGNALS" value={workspace.counts.active_signals} detail={`${workspace.counts.pending_signals} pending`} tone="positive" />
      <MetricCard label="WAIT MARKETS" value={workspace.counts.wait_markets} detail="Professional rejections" tone="caution" />
      <MetricCard label="QUANT VETOES" value={workspace.counts.quant_vetoes} detail="Absolute authority" tone="negative" />
      <MetricCard label="RISK VETOES" value={workspace.counts.risk_vetoes} detail="Cannot be overridden" tone="negative" />
      <MetricCard label="DATA HEALTH" value={market.data_quality_score === null ? "—" : `${market.data_quality_score.toFixed(0)}`} detail={market.data_quality ?? "Unavailable"} tone={market.availability === "AVAILABLE" ? "positive" : "negative"} />
    </section>
    <SignalDecision market={market} summary={signal} />
    {signal ? <SignalTrack summary={signal} /> : null}
    <TimeframeMatrix market={market} />
    <section className="v4-two-column"><article className="v4-panel"><SectionHeading eyebrow="RANKED BY PERSISTED AUTHORITY" title="Best opportunities" />{opportunities.length ? opportunities.map((row) => <div className="v4-opportunity" key={row.instrument}><b>{row.instrument}</b><span>{row.signal_direction} · {row.best_timeframe}</span><strong>{formatProbability(row.calibrated_probability)} · {row.grade}</strong></div>) : <EmptyEvidence title="No canonical opportunities">Atlas has not persisted a signal that passed every gate.</EmptyEvidence>}</article><article className="v4-panel"><SectionHeading eyebrow="SYSTEM PRESSURE" title="Blocks and degraded evidence" />{workspace.decisions.filter((row) => row.decision !== "PUBLISHED").slice(0, 6).map((row) => <div className="v4-opportunity" key={row.publication_attempt_id}><b>{row.instrument}</b><span>{displayLabel(row.decision)}</span><strong>{formatProbability(row.atlas_probability)}</strong></div>)}{workspace.decisions.length === 0 ? <EmptyEvidence title="No decision history">No publication attempt has been persisted yet.</EmptyEvidence> : null}</article></section>
  </div>;
}

function MarketsView({ workspace, filter, onFilter, onSelect }: { workspace: V4TerminalWorkspace; filter: MarketFilter; onFilter: (value: MarketFilter) => void; onSelect: (symbol: string) => void }) {
  const rows = filterMarkets(workspace.markets, filter);
  const filters: MarketFilter[] = ["ALL", "FOREX", "COMMODITY", "SIGNALS", "A_PLUS", "P90", "EVENT_SAFE", "LONG", "SHORT", "WAIT"];
  return <section className="v4-panel v4-market-scanner"><SectionHeading eyebrow="CANONICAL V4 READ MODEL" title="Market scanner" note="No demo fallback" /><div className="v4-filters">{filters.map((item) => <button className={filter === item ? "active" : ""} key={item} onClick={() => onFilter(item)} type="button">{displayLabel(item)}</button>)}</div><div className="v4-table-scroll"><div className="v4-market-row v4-table-head"><span>SYMBOL</span><span>PRICE</span><span>SPREAD</span><span>15M</span><span>1H</span><span>4H</span><span>1D</span><span>BEST TF</span><span>CAL P</span><span>GRADE</span><span>EV</span><span>REGIME</span><span>EVENT</span><span>QUANT</span><span>STATUS</span></div>{rows.map((row) => <button className="v4-market-row" key={row.instrument} onClick={() => onSelect(row.instrument)} type="button"><b>{row.instrument}<small>{row.asset_class}</small></b><span>{price(row.price, row.instrument)}</span><span>{price(row.spread, row.instrument)}</span>{row.timeframes.map((cell) => <span className={cell.selected ? "selected" : ""} key={cell.timeframe}>{displayLabel(cell.trend)}</span>)}<span>{row.best_timeframe ?? "—"}</span><span>{formatProbability(row.calibrated_probability)}</span><span>{row.grade ?? "—"}</span><span>{signed(row.expected_value_net)}</span><span>{displayLabel(row.regime)}</span><span>{displayLabel(row.event_risk)}</span><span>{displayLabel(row.quant_status)}</span><StatusPill value={row.decision ?? row.availability} tone={decisionTone(row.decision)} /></button>)}</div></section>;
}

function signalMatches(summary: V4SignalSummary, filter: SignalFilter): boolean {
  if (filter === "ALL") return true;
  const state = summary.tracking?.state ?? "PENDING_ENTRY";
  if (filter === "ACTIVE") return isLiveState(state);
  if (filter === "PENDING") return state === "PENDING_ENTRY";
  if (filter === "WON") return state === "TP3_HIT";
  if (filter === "STOPPED") return state === "STOPPED";
  if (filter === "EXPIRED") return state === "EXPIRED";
  return state.includes("INVALIDATED") || state.includes("CANCELLED");
}

function SignalEnginePanel({
  market,
  orchestration,
  generating,
  generationMessage,
  generationError,
  onGenerate,
}: {
  market: V4MarketSummary;
  orchestration: V4OrchestrationStatus | null;
  generating: boolean;
  generationMessage: string | null;
  generationError: string | null;
  onGenerate: () => void;
}) {
  const latestResult = orchestration?.latest_results.find((row) => row.instrument === market.instrument) ?? null;
  const blockerCodes = latestResult?.blocker_codes.length
    ? latestResult.blocker_codes
    : market.decision_reasons.length
      ? market.decision_reasons
      : market.data_issues;
  const engineState = generating
    ? "EVALUATION RUNNING"
    : latestResult?.signal_id
      ? "SIGNAL PUBLISHED"
      : latestResult
        ? "EVALUATION COMPLETE"
        : "READY";
  const stateTone = generationError ? "negative" : generating ? "caution" : latestResult?.signal_id ? "positive" : "neutral";
  const progress = orchestration
    ? `${Math.min(orchestration.instruments_completed, orchestration.instruments_total)}/${orchestration.instruments_total}`
    : "0/0";
  const runningTitle = orchestration?.current_instrument
    ? `Atlas is evaluating ${orchestration.current_instrument}`
    : orchestration?.current_stage?.startsWith("PREPARING_META")
      ? "Atlas is preparing quantitative models"
      : "Atlas is evaluating approved markets";
  const runningMessage = `Completed ${progress} markets. ${displayLabel(orchestration?.current_stage)}.`;

  return <section className="v4-panel v4-signal-engine">
    <SectionHeading eyebrow="ON-DEMAND ORCHESTRATION" title="Signal engine" note="Real MT5 evidence; no fabricated trades" />
    <div className="v4-engine-layout">
      <div className={`v4-engine-state ${stateTone}`}>
        <span>ENGINE STATE</span>
        <StatusPill value={engineState} tone={stateTone} />
        <h3>{generating ? runningTitle : latestResult?.signal_id ? `${market.instrument} published a canonical signal` : `${market.instrument} has no publishable signal yet`}</h3>
        <p>{generationError ?? (generating ? runningMessage : generationMessage ?? (blockerCodes.length ? blockerCodes.map(displayLabel).join(" / ") : "Press Generate Signal to run the complete Atlas evidence pipeline."))}</p>
      </div>
      <div className="v4-engine-grid">
        <div><span>SELECTED MARKET</span><b>{market.instrument}</b><small>{displayLabel(market.decision)}</small></div>
        <div><span>DATA</span><b>{displayLabel(market.data_quality)}</b><small>{market.mt5_status ?? "MT5 unavailable"}</small></div>
        <div><span>QUANT</span><b>{displayLabel(market.quant_status)}</b><small>{formatProbability(market.calibrated_probability)} calibrated</small></div>
        <div><span>LAST EVALUATION</span><b>{orchestration?.last_state ? displayLabel(orchestration.last_state) : "NOT RUN"}</b><small>{orchestration?.last_cycle_completed_at ? dateTime(orchestration.last_cycle_completed_at) : "No completed cycle"}</small></div>
      </div>
    </div>
    <div className="v4-engine-actions">
      <button className="v4-generate-button" disabled={generating || orchestration?.enabled === false} onClick={onGenerate} type="button">{generating ? `EVALUATING ${progress}` : "GENERATE SIGNAL"}</button>
      <p>Evaluates this selected market with a five-minute target. A 60%+ calibrated setup can publish LONG/SHORT with entry, SL, and TP1/TP2/TP3 only when every data, Quant, and risk gate also passes.</p>
    </div>
  </section>;
}

function SignalsView({ signals, filter, onFilter }: { signals: V4SignalSummary[]; filter: SignalFilter; onFilter: (value: SignalFilter) => void }) {
  const filters: SignalFilter[] = ["ACTIVE", "PENDING", "WON", "STOPPED", "EXPIRED", "INVALIDATED", "ALL"];
  const rows = signals.filter((row) => signalMatches(row, filter));
  return <section className="v4-panel v4-signal-history"><SectionHeading eyebrow="IMMUTABLE LEDGER" title="Signal history" note="Losses are never deleted" /><div className="v4-filters">{filters.map((item) => <button className={filter === item ? "active" : ""} key={item} onClick={() => onFilter(item)} type="button">{item}</button>)}</div>{rows.length ? <div className="v4-table-scroll"><div className="v4-history-row v4-table-head"><span>CREATED</span><span>SYMBOL</span><span>SIDE / TF</span><span>PROBABILITY</span><span>GRADE</span><span>ENTRY</span><span>SL</span><span>TP1 / TP2 / TP3</span><span>STATE</span><span>R / PIPS</span></div>{rows.map(({ signal, tracking }) => <div className="v4-history-row" key={signal.signal_id}><span>{dateTime(signal.created_at)}</span><b>{signal.instrument}</b><span>{signal.direction} · {signal.timeframe}</span><span>{formatProbability(signal.calibrated_probability)}</span><span>{signal.grade}</span><span>{price(signal.entry.low, signal.instrument)}-{price(signal.entry.high, signal.instrument)}</span><span>{price(signal.stop_loss, signal.instrument)}</span><span>{signal.targets.map((target) => price(target.price, signal.instrument)).join(" / ")}</span><StatusPill value={tracking?.state ?? "PUBLISHED"} tone={tracking?.state === "STOPPED" ? "negative" : "positive"} /><span>{signed(tracking?.live_r, "R")} / {signed(tracking?.live_pips, "p")}</span></div>)}</div> : <EmptyEvidence title={`No ${filter.toLowerCase()} signals`}>No canonical LONG/SHORT has passed every V4 gate. The Signal Engine above shows the current evaluation and exact blockers.</EmptyEvidence>}</section>;
}

function AIView({ reviews, selected }: { reviews: V4AIReviewSummary[]; selected: string }) {
  const rows = reviews.filter((review) => review.instrument === selected);
  return <section className="v4-panel"><SectionHeading eyebrow="STRUCTURED · NON-VOTING" title="Atlas AI evidence review" note="AI cannot set probability or override Quant" />{rows.length ? rows.map((review) => <article className="v4-ai-review" key={review.bundle_id}><header><div><b>{review.instrument}</b><span>{displayLabel(review.status)}</span></div><small>{review.total_latency_ms.toFixed(0)} ms · {review.input_tokens + review.output_tokens} local tokens · $0.00</small></header><div className="v4-review-roles">{Object.entries(review.specialist_statuses).map(([role, status]) => <div key={role}><span>{displayLabel(role)}</span><StatusPill value={status} tone={status === "SUPPORTS" ? "positive" : status === "CONTRADICTS" ? "negative" : "caution"} /></div>)}</div><div className="v4-two-column"><div><b>CONTRADICTIONS</b>{review.contradictions.length ? review.contradictions.map((item) => <p key={item}>{item}</p>) : <p>None recorded.</p>}</div><div><b>INVALIDATIONS</b>{review.invalidations.length ? review.invalidations.map((item) => <p key={item}>{item}</p>) : <p>None recorded.</p>}</div></div></article>) : <EmptyEvidence title="No grounded AI review">Atlas AI will not answer from generic knowledge here. A persisted candidate and structured V4 review bundle are required.</EmptyEvidence>}</section>;
}

function CalendarView({ events }: { events: V4CalendarEvent[] }) {
  return <section className="v4-panel"><SectionHeading eyebrow="DEFENSIVE EVENT LAYER" title="Economic calendar" note="DATE_ONLY events never invent a release minute" />{events.length ? <div className="v4-table-scroll"><div className="v4-calendar-row v4-table-head"><span>TIME</span><span>EVENT</span><span>CCY</span><span>INSTRUMENT</span><span>IMPACT</span><span>COUNTDOWN</span><span>AFFECTED SIGNALS</span></div>{events.map((event) => <div className="v4-calendar-row" key={`${event.instrument}-${event.event_id}`}><span>{dateTime(event.scheduled_at)}<small>{event.time_precision}</small></span><b>{event.name}</b><span>{event.currency}</span><span>{event.instrument}</span><StatusPill value={event.risk} tone={event.candidate_veto ? "negative" : "caution"} /><span>{event.minutes_to_event === null ? "DATE ONLY" : `${Math.round(event.minutes_to_event)} min`}</span><span>{event.affected_signal_ids.length}</span></div>)}</div> : <EmptyEvidence title="Calendar data unavailable">No current persisted event assessments exist. Atlas does not manufacture an economic calendar.</EmptyEvidence>}</section>;
}

function ModelsView({ models }: { models: V4ModelSummary[] }) {
  return <section className="v4-panel"><SectionHeading eyebrow="GOVERNED MODEL REGISTRY" title="Primary models" note="No automatic promotion" />{models.length ? <div className="v4-model-grid">{models.map((model) => <article key={model.model_id}><header><span>{displayLabel(model.family)}</span><StatusPill value={model.status} tone={model.status === "SHADOW" ? "caution" : "neutral"} /></header><h3>{model.model_version}</h3><div><span>TEST SAMPLES</span><b>{model.test_sample_count}</b></div><div><span>BRIER</span><b>{model.test_brier_score.toFixed(4)}</b></div><div><span>MEAN NET RETURN</span><b>{signed(model.test_mean_net_return)}</b></div><div><span>SCOPE</span><b>{model.supported_timeframes.join(" / ")}</b></div><small>Decision influence: disabled</small></article>)}</div> : <EmptyEvidence title="No governed model artifacts">Model metrics are shown only after a persisted V4 training run.</EmptyEvidence>}</section>;
}

function MT5View({ market }: { market: V4MarketSummary }) {
  const rows = [
    ["Connection", market.mt5_status], ["Broker", market.broker], ["Server", market.server], ["Account mode", market.account_mode],
    ["Last tick", dateTime(market.last_tick_at)], ["Tick age", market.tick_age_seconds === null ? null : `${market.tick_age_seconds?.toFixed(2)} s`],
    ["Snapshot", dateTime(market.captured_at)], ["Ticks captured", market.tick_count?.toString()], ["Session", market.session],
    ["Volume provenance", market.volume_provenance], ["Data quality", market.data_quality], ["Quality score", market.data_quality_score?.toFixed(1)],
  ];
  return <div className="v4-two-column"><section className="v4-panel"><SectionHeading eyebrow="READ-ONLY BROKER BRIDGE" title={`${market.instrument} MT5 state`} note="Order methods do not exist" /><div className="v4-definition-list">{rows.map(([name, value]) => <div key={name}><span>{name}</span><b>{displayLabel(value)}</b></div>)}</div></section><section className="v4-panel"><SectionHeading eyebrow="FAIL-CLOSED QUALITY" title="Data issues" />{market.data_issues.length ? market.data_issues.map((issue) => <div className="v4-issue" key={issue}><i />{displayLabel(issue)}</div>) : <div className="v4-clear-state"><b>HEALTHY</b><span>No persisted blocking issue.</span></div>}</section></div>;
}

function PerformanceView({ workspace }: { workspace: V4TerminalWorkspace }) {
  const dashboard = workspace.performance;
  const aggregate = dashboard.performance_slices.find((row) => row.dimension === "ALL");
  const calibration = dashboard.calibrations[0];
  return <div className="v4-view v4-performance">
    <section className="v4-metrics-grid">
      <MetricCard label="EVIDENCE STATUS" value={displayLabel(dashboard.status)} detail={`${dashboard.performance_slices.length} traceable slices`} tone={dashboard.status === "AVAILABLE" ? "positive" : "caution"} />
      <MetricCard label="RESOLVED SIGNALS" value={aggregate?.resolved_count ?? 0} detail={`${aggregate?.published_count ?? 0} canonical publications`} />
      <MetricCard label="WIN RATE" value={formatProbability(aggregate?.win_rate)} detail={`${aggregate?.win_count ?? 0} wins / ${aggregate?.loss_count ?? 0} losses`} tone="positive" />
      <MetricCard label="MEAN REALIZED R" value={signed(aggregate?.mean_realized_r, "R")} detail="Server-resolved outcomes only" />
      <MetricCard label="CUMULATIVE R" value={signed(aggregate?.cumulative_realized_r, "R")} detail="No browser calculation" />
      <MetricCard label="PUBLICATION ATTEMPTS" value={dashboard.vetoes.attempt_count} detail="Immutable decision evidence" />
    </section>

    <section className="v4-panel">
      <SectionHeading eyebrow="PROBABILITY RELIABILITY" title="Calibration dashboard" note="Stored out-of-sample evidence only" />
      {calibration ? <article className="v4-calibration-card">
        <header><div><StatusPill value={calibration.status} tone="positive" /><b>{displayLabel(calibration.method)}</b></div><small>{dateTime(calibration.created_at)}</small></header>
        <div className="v4-calibration-metrics"><div><span>SAMPLES / EVALUATION</span><b>{calibration.sample_count} / {calibration.evaluation_sample_count}</b></div><div><span>BRIER BEFORE / AFTER</span><b>{metric(calibration.brier_before)} / {metric(calibration.brier_after)}</b></div><div><span>ECE AFTER</span><b>{metric(calibration.ece_after)}</b></div><div><span>UNCERTAINTY</span><b>{displayLabel(calibration.uncertainty)}</b></div></div>
        <div className="v4-reliability"><div className="v4-reliability-head"><span>PROBABILITY BIN</span><span>MEAN P</span><span>OBSERVED</span><span>SAMPLES</span></div>{calibration.reliability_bins.map((bin) => <div className="v4-reliability-row" key={`${bin.lower_bound}-${bin.upper_bound}`}><span>{formatProbability(bin.lower_bound)}-{formatProbability(bin.upper_bound)}</span><div><i style={{ width: `${(bin.mean_probability ?? 0) * 100}%` }} /></div><b>{formatProbability(bin.observed_rate)}</b><strong>{bin.sample_count}</strong></div>)}</div>
        <footer><code title={calibration.calibration_id}>CAL {traceId(calibration.calibration_id)}</code><code title={calibration.source_validation_run_id}>RUN {traceId(calibration.source_validation_run_id)}</code><code title={calibration.meta_model_id}>META {traceId(calibration.meta_model_id)}</code></footer>
      </article> : <EmptyEvidence title="Calibration evidence unavailable">No persisted calibration artifact exists, so Atlas reports no reliability curve or probability-quality claim.</EmptyEvidence>}
    </section>

    <section className="v4-panel">
      <SectionHeading eyebrow="OUTCOME ATTRIBUTION" title="Performance by instrument, timeframe, and regime" note="Every row includes evidence counts" />
      {dashboard.performance_slices.length ? <div className="v4-table-scroll"><div className="v4-performance-row v4-table-head"><span>DIMENSION</span><span>VALUE</span><span>PUBLISHED</span><span>RESOLVED</span><span>W / L</span><span>WIN RATE</span><span>MEAN R</span><span>CUM R</span><span>TRACE</span></div>{dashboard.performance_slices.map((row) => <div className="v4-performance-row" key={`${row.dimension}-${row.key}`}><span>{row.dimension}</span><b>{displayLabel(row.key)}</b><span>{row.published_count}</span><span>{row.resolved_count}</span><span>{row.win_count} / {row.loss_count}</span><span>{formatProbability(row.win_rate)}</span><span>{signed(row.mean_realized_r, "R")}</span><span>{signed(row.cumulative_realized_r, "R")}</span><code title={row.signal_ids.join(", ")}>{row.signal_ids.length} signals / {row.tracking_ids.length} tracks</code></div>)}</div> : <EmptyEvidence title="No resolved signal evidence">Performance slices appear only after canonical V4 publications exist. Win rate and realized R are never inferred.</EmptyEvidence>}
    </section>

    <section className="v4-panel">
      <SectionHeading eyebrow="COST-AWARE VALIDATION" title="Backtests and execution-cost drag" note="Research runs cannot publish or trade" />
      {dashboard.backtests.length ? <div className="v4-table-scroll"><div className="v4-backtest-row v4-table-head"><span>RUN</span><span>SYMBOL</span><span>SAMPLES</span><span>WIN RATE</span><span>MEAN NET</span><span>CUM NET</span><span>MAX DD</span><span>PF</span><span>COST DRAG</span><span>SPREAD / SLIP / COMM</span></div>{dashboard.backtests.map((row) => <div className="v4-backtest-row" key={row.backtest_run_id}><code title={row.backtest_run_id}>{traceId(row.backtest_run_id)}</code><b>{row.instrument}</b><span>{row.sample_count}</span><span>{formatProbability(row.win_rate)}</span><span>{metric(row.mean_net_return)}</span><span>{metric(row.cumulative_net_return)}</span><span>{metric(row.maximum_drawdown)}</span><span>{metric(row.profit_factor, 2)}</span><span>{metric(row.cost_drag)}</span><span>{row.spread_bps.toFixed(2)} / {row.slippage_bps.toFixed(2)} / {row.commission_bps_round_turn.toFixed(2)} bps</span></div>)}</div> : <EmptyEvidence title="No persisted backtest run">Atlas will not display expectancy, drawdown, profit factor, or cost drag until a deterministic V4 backtest is stored.</EmptyEvidence>}
    </section>

    <section className="v4-panel">
      <SectionHeading eyebrow="GATE PRESSURE" title="Publication and veto analytics" note={`${dashboard.vetoes.attempt_count} traceable attempts`} />
      {dashboard.vetoes.attempt_count ? <div className="v4-veto-grid"><article><b>DECISIONS</b>{Object.entries(dashboard.vetoes.decision_counts).map(([name, count]) => <p key={name}><span>{displayLabel(name)}</span><strong>{count}</strong></p>)}</article><article><b>TOP REASONS</b>{Object.entries(dashboard.vetoes.reason_counts).slice(0, 10).map(([name, count]) => <p key={name}><span>{displayLabel(name)}</span><strong>{count}</strong></p>)}</article><article className="v4-trace-list"><b>ATTEMPT IDS</b>{dashboard.vetoes.publication_attempt_ids.slice(0, 12).map((id) => <code title={id} key={id}>{traceId(id)}</code>)}</article></div> : <EmptyEvidence title="No publication attempts">Veto rates require persisted gate decisions. Empty history is shown as unavailable, not zero performance.</EmptyEvidence>}
    </section>
  </div>;
}

function QuantLabView({ workspace }: { workspace: V4TerminalWorkspace }) {
  const dashboard = workspace.performance;
  return <div className="v4-view v4-quant-lab">
    <section className="v4-panel"><SectionHeading eyebrow="RESEARCH ISOLATION" title="Experiment registry" note="Automatic promotion disabled" />{dashboard.experiments.length ? <div className="v4-experiment-grid">{dashboard.experiments.map((row) => <article key={row.experiment_id}><header><StatusPill value={row.status} tone="caution" /><span>{dateTime(row.registered_at)}</span></header><h3>{row.name}</h3><p>{row.total_variants} registered variants</p><div><span>FEATURE SETS</span><b>{row.feature_sets.join(" / ")}</b></div><div><span>MODEL FAMILIES</span><b>{row.model_families.join(" / ")}</b></div><code title={row.experiment_id}>{traceId(row.experiment_id)}</code><small>Automatic promotion: disabled</small></article>)}</div> : <EmptyEvidence title="No registered experiments">A/B and walk-forward claims remain unavailable until a V4 experiment record is persisted.</EmptyEvidence>}</section>

    <section className="v4-panel"><SectionHeading eyebrow="ROBUSTNESS AND MULTIPLE TESTING" title="Monte Carlo, DSR, and PBO" note="Unavailable values stay unavailable" />{dashboard.robustness_runs.length ? <div className="v4-robustness-grid">{dashboard.robustness_runs.map((row) => <article key={row.robustness_run_id}><header><StatusPill value={row.status} tone={row.status === "AVAILABLE" ? "positive" : "caution"} /><code title={row.robustness_run_id}>{traceId(row.robustness_run_id)}</code></header><div><span>RETURN SAMPLES</span><b>{row.sample_count}</b></div><div><span>BOOTSTRAPS</span><b>{row.bootstrap_samples}</b></div><div><span>VARIANTS</span><b>{row.variants_tested}</b></div><div><span>MEAN / STRESSED</span><b>{metric(row.mean_return)} / {metric(row.stressed_mean_return)}</b></div><div><span>DEFLATED SHARPE</span><b>{metric(row.deflated_sharpe_ratio)}</b></div><div><span>PBO</span><b>{formatProbability(row.probability_backtest_overfitting)}</b></div><p>{row.reason}</p></article>)}</div> : <EmptyEvidence title="Robustness evidence unavailable">Atlas makes no Monte Carlo, deflated Sharpe, or probability-of-overfitting claim without a persisted robustness run.</EmptyEvidence>}</section>

    <section className="v4-panel"><SectionHeading eyebrow="SHADOW COMPARISON" title="Model evidence" note="Comparison does not grant signal authority" />{dashboard.model_comparisons.length ? <div className="v4-table-scroll"><div className="v4-model-compare-row v4-table-head"><span>MODEL</span><span>FAMILY</span><span>STATUS</span><span>SAMPLES</span><span>BRIER</span><span>LOG LOSS</span><span>BAL ACC</span><span>MEAN NET</span><span>BASELINE</span><span>TRACE</span></div>{dashboard.model_comparisons.map((row) => <div className="v4-model-compare-row" key={row.model_id}><b>{row.model_version}</b><span>{displayLabel(row.family)}</span><StatusPill value={row.status} tone="caution" /><span>{row.sample_count}</span><span>{metric(row.brier_score)}</span><span>{metric(row.log_loss)}</span><span>{formatProbability(row.balanced_accuracy)}</span><span>{metric(row.mean_net_return)}</span><span>{metric(row.baseline_mean_net_return)}</span><code title={row.run_ids.join(", ")}>{traceId(row.model_id)}</code></div>)}</div> : <EmptyEvidence title="No governed model comparison">Shadow results appear only after stored V4 model artifacts with chronological test metrics exist.</EmptyEvidence>}</section>
  </div>;
}

function OrchestrationPanel({ status }: { status: V4OrchestrationStatus | null }) {
  const blockers = status?.latest_results.flatMap((row) =>
    row.blocker_codes.map((code) => `${row.instrument}: ${code}`),
  ) ?? [];
  return <section className="v4-panel">
    <SectionHeading
      eyebrow="BACKGROUND ORCHESTRATION"
      title="Continuous evidence worker"
      note={status ? `${status.running ? "RUNNING" : "STOPPED"} · ${status.interval_seconds}s cadence` : "STATUS UNAVAILABLE"}
    />
    {status ? <>
      <div className="v4-settings-grid">
        <article><span>WORKER</span><StatusPill value={status.cycle_in_progress ? "CYCLE ACTIVE" : status.running ? "RUNNING" : "STOPPED"} tone={status.running ? "positive" : "negative"} /><p>{status.instruments.length} approved markets are supervised by the backend lifecycle.</p></article>
        <article><span>LAST CYCLE</span><StatusPill value={status.last_state ?? "NOT RUN"} tone={status.last_state === "COMPLETED" ? "positive" : status.last_state === "FAILED" ? "negative" : "caution"} /><p>{status.last_cycle_completed_at ? dateTime(status.last_cycle_completed_at) : "No completed cycle yet."}</p></article>
        <article><span>CYCLES</span><b>{status.cycles_completed} COMPLETE</b><p>{status.consecutive_cycle_failures} consecutive service failure(s).</p></article>
        <article><span>NEXT CYCLE</span><b>{status.next_cycle_at ? dateTime(status.next_cycle_at) : status.cycle_in_progress ? "IN PROGRESS" : "UNSCHEDULED"}</b><p>Execution and order submission remain disabled.</p></article>
      </div>
      {blockers.length ? <div className="v4-source-issues"><b>LATEST EVIDENCE BLOCKERS</b>{blockers.slice(0, 20).map((issue) => <span key={issue}>{issue}</span>)}</div> : null}
    </> : <EmptyEvidence title="Orchestration status unavailable">The browser could not read the server-owned background worker contract.</EmptyEvidence>}
  </section>;
}

function SettingsView({ workspace, health }: { workspace: V4TerminalWorkspace; health: V4OperationalHealth | null }) {
  const components = health ? [health.database, health.redis, health.mt5, health.data, health.models, health.llm] : [];
  return <div className="v4-view"><section className="v4-panel"><SectionHeading eyebrow="AUTHORITY AND SAFETY" title="Atlas V4 settings" note={workspace.version} /><div className="v4-settings-grid"><article><span>SIGNAL AUTHORITY</span><b>V4 SERVER ENABLED</b><p>The calibrated publication gate can issue analytical LONG/SHORT signals; the browser cannot construct one.</p></article><article><span>TRACKING AUTHORITY</span><b>V4 SERVER ONLY</b><p>Entry, TP, stop, expiry, and MFE/MAE are resolved from validated MT5 evidence.</p></article><article><span>BROKER EXECUTION</span><b className="negative-text">DISABLED</b><p>No V4 route can place, modify, close, or cancel a MetaTrader order.</p></article><article><span>UNAVAILABLE DATA</span><b>FAIL CLOSED</b><p>Missing prices, probability, macro, event, or performance data remain explicitly unavailable.</p></article></div>{workspace.source_issues.length ? <div className="v4-source-issues"><b>PRESENTATION SOURCE ISSUES</b>{workspace.source_issues.map((issue) => <span key={issue}>{issue}</span>)}</div> : null}</section><section className="v4-panel"><SectionHeading eyebrow="LIVE OPERATIONAL PROBES" title="Phase 15 component health" note={health ? `${health.status} · ${dateTime(health.checked_at)}` : "HEALTH ENDPOINT UNAVAILABLE"} />{components.length ? <div className="v4-settings-grid">{components.map((component) => <article key={component.name}><span>{component.name}</span><StatusPill value={component.state} tone={component.state === "HEALTHY" ? "positive" : component.state === "DISABLED" ? "neutral" : "negative"} /><p>{component.message}</p><small>{component.evidence_count} evidence item(s) · {component.latency_ms.toFixed(1)} ms</small>{component.issues.length ? <code>{component.issues.join(" / ")}</code> : null}</article>)}</div> : <EmptyEvidence title="Operational health unavailable">Atlas could not read the dedicated Phase 15 health contract. No component is assumed healthy.</EmptyEvidence>}</section></div>;
}

function IntelligenceRail({ market, signal, review }: { market: V4MarketSummary; signal: V4SignalSummary | null; review: V4AIReviewSummary | null }) {
  return <aside className="v4-intelligence"><div><span>SELECTED MARKET</span><h3>{market.instrument}</h3><b>{price(market.price, market.instrument)}</b><small>{market.session ?? "Session unavailable"} · {market.best_timeframe ?? "No selected TF"}</small></div><div><span>DECISION STACK</span>{[
    ["Data", market.data_quality], ["Meta / Cal", formatProbability(market.calibrated_probability)], ["Quant", market.quant_status], ["Risk", signal?.signal.risk_validation.status], ["Publication", market.decision], ["Tracking", market.tracking_state],
  ].map(([name, value]) => <p key={name}><span>{name}</span><b>{displayLabel(value)}</b></p>)}</div><div><span>WHY THIS DIRECTION</span>{signal ? signal.signal.drivers.slice(0, 4).map((item) => <p className="evidence" key={item}>{item}</p>) : <p className="evidence">No published direction exists.</p>}</div><div><span>CONTRADICTIONS</span>{signal?.signal.contradictions.length ? signal.signal.contradictions.slice(0, 4).map((item) => <p className="risk" key={item}>{item}</p>) : review?.contradictions.length ? review.contradictions.slice(0, 4).map((item) => <p className="risk" key={item}>{item}</p>) : <p className="evidence">None persisted.</p>}</div><div><span>INVALIDATION</span>{signal ? signal.signal.invalidations.slice(0, 3).map((item) => <p className="risk" key={item}>{item}</p>) : <p className="evidence">No canonical geometry exists.</p>}</div><footer>Structured evidence only · hidden chain-of-thought is never exposed.</footer></aside>;
}

export function AtlasV4Terminal() {
  const [section, setSection] = useState<V4Section>("COMMAND_CENTER");
  const [symbol, setSymbol] = useState("XAUUSD");
  const [workspace, setWorkspace] = useState<V4TerminalWorkspace | null>(null);
  const [operationalHealth, setOperationalHealth] = useState<V4OperationalHealth | null>(null);
  const [orchestration, setOrchestration] = useState<V4OrchestrationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [manualGenerating, setManualGenerating] = useState(false);
  const [generationMessage, setGenerationMessage] = useState<string | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [marketFilter, setMarketFilter] = useState<MarketFilter>("ALL");
  const [signalFilter, setSignalFilter] = useState<SignalFilter>("ALL");
  const loadingRef = useRef(false);

  const refreshOrchestration = useCallback(async (): Promise<V4OrchestrationStatus | null> => {
    try {
      const response = await fetch("/api/v4/orchestration", { cache: "no-store" });
      const body = await response.json() as V4OrchestrationStatus;
      const valid = response.ok && body.execution_enabled === false ? body : null;
      setOrchestration(valid);
      return valid;
    } catch {
      setOrchestration(null);
      return null;
    }
  }, []);

  const load = useCallback(async (instrument: string) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/v4/terminal?instrument=${encodeURIComponent(instrument)}`, { cache: "no-store" });
      const body = await response.json() as V4TerminalWorkspace & { error?: string; detail?: string | { message?: string } };
      if (!response.ok) {
        const detail = typeof body.detail === "string" ? body.detail : body.detail?.message;
        throw new Error(body.error || detail || `Atlas V4 returned HTTP ${response.status}.`);
      }
      if (body.phase !== 15 || body.execution_enabled !== false || body.order_submission !== false) {
        throw new Error("Atlas V4 authority contract was not satisfied.");
      }
      setWorkspace(body);
      try {
        const healthResponse = await fetch(`/api/v4/health?instrument=${encodeURIComponent(instrument)}`, { cache: "no-store" });
        const healthBody = await healthResponse.json() as V4OperationalHealth;
        setOperationalHealth(healthBody.phase === 15 && healthBody.execution_enabled === false ? healthBody : null);
      } catch {
        setOperationalHealth(null);
      }
      await refreshOrchestration();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Atlas V4 is unavailable.");
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, [refreshOrchestration]);

  const generateSignal = useCallback(async (): Promise<void> => {
    setSection("SIGNALS");
    setGenerationError(null);
    if (orchestration?.cycle_in_progress) {
      setGenerationMessage("An Atlas evaluation is already running. This screen will update when it completes.");
      return;
    }
    setManualGenerating(true);
    setGenerationMessage(`Starting the complete ${symbol} evaluation. Target completion is under five minutes on local models.`);
    try {
      const response = await fetch(`/api/v4/orchestration?instrument=${encodeURIComponent(symbol)}`, { method: "POST", cache: "no-store" });
      const body = await response.json() as {
        detail?: string;
        error?: string;
        signals_published?: number;
        blocked_instruments?: number;
        execution_enabled?: boolean;
      };
      if (response.status === 409) {
        setGenerationMessage("An Atlas evaluation is already running. Live status is shown below.");
        await refreshOrchestration();
        return;
      }
      if (!response.ok || body.execution_enabled !== false) {
        throw new Error(body.error || body.detail || `Atlas evaluation returned HTTP ${response.status}.`);
      }
      const published = body.signals_published ?? 0;
      setGenerationMessage(published > 0
        ? `Evaluation complete. ${published} canonical signal${published === 1 ? "" : "s"} passed every gate.`
        : `Evaluation complete for ${symbol}. The setup did not pass every required gate, so no unsupported LONG/SHORT was created.`);
      await load(symbol);
    } catch (cause) {
      setGenerationError(cause instanceof Error ? cause.message : "Atlas could not start the signal evaluation.");
      await refreshOrchestration();
    } finally {
      setManualGenerating(false);
    }
  }, [load, orchestration?.cycle_in_progress, refreshOrchestration, symbol]);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void load(symbol), 0);
    return () => window.clearTimeout(initialLoad);
  }, [load, symbol]);
  useEffect(() => {
    const timer = window.setInterval(() => void load(symbol), 60_000);
    return () => window.clearInterval(timer);
  }, [load, symbol]);
  useEffect(() => {
    if (!orchestration?.cycle_in_progress) return;
    const timer = window.setInterval(async () => {
      const status = await refreshOrchestration();
      if (status && !status.cycle_in_progress) {
        setGenerationMessage(status.last_state === "COMPLETED"
          ? "Evaluation completed. Atlas state has been refreshed."
          : "Evaluation completed with evidence blockers. Review the selected market below.");
        void load(symbol);
      }
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [load, orchestration?.cycle_in_progress, refreshOrchestration, symbol]);

  const market = useMemo(() => workspace?.markets.find((row) => row.instrument === symbol) ?? null, [workspace, symbol]);
  const signal = useMemo(() => workspace?.signals.find((row) => row.signal.instrument === symbol) ?? null, [workspace, symbol]);
  const review = useMemo(() => workspace?.ai_reviews.find((row) => row.instrument === symbol) ?? null, [workspace, symbol]);
  const symbols = workspace?.markets.map((row) => row.instrument) ?? [...FALLBACK_SYMBOLS];
  const generating = manualGenerating || orchestration?.cycle_in_progress === true;
  const generationProgress = orchestration
    ? `${Math.min(orchestration.instruments_completed, orchestration.instruments_total)}/${orchestration.instruments_total}`
    : "0/0";

  function selectMarket(instrument: string): void {
    setSymbol(instrument);
    setSection("COMMAND_CENTER");
  }

  const currentMarket: V4MarketSummary = market ?? {
    instrument: symbol, asset_class: ["XAUUSD", "XAGUSD", "WTI", "BRENT"].includes(symbol) ? "COMMODITY" : "FOREX",
    bid: null, ask: null, price: null, spread: null, spread_bps: null, captured_at: null, session: null,
    broker: null, server: null, account_mode: null, last_tick_at: null, tick_age_seconds: null, tick_count: null,
    volume_provenance: null, mt5_status: null, data_quality: null, data_quality_score: null,
    data_issues: ["V4_BACKEND_UNAVAILABLE"], liquidity: null, mtf_alignment: null, best_timeframe: null,
    calibrated_probability: null, grade: null, expected_value_net: null, regime: null, event_risk: null,
    quant_status: null, decision: null, decision_reasons: [], signal_id: null, signal_direction: null,
    tracking_state: null, availability: "DATA_UNAVAILABLE", timeframes: (["15M", "1H", "4H", "1D"] as const).map((timeframe) => ({ timeframe, last_close: null, last_bar_at: null, trend: null, structure: null, regime: null, volatility: null, raw_probability: null, meta_probability: null, calibrated_probability: null, event_risk: null, quant_status: null, selected: false, availability: "DATA_UNAVAILABLE" })),
  };

  return <main className="v4-app-shell">
    <aside className="v4-sidebar"><div className="v4-brand"><span>AQ</span><div><b>ATLAS</b><small>QUANT V4</small></div></div><nav aria-label="Atlas V4 navigation">{NAV_ITEMS.map((item) => <button className={section === item.id ? "active" : ""} key={item.id} onClick={() => setSection(item.id)} type="button"><span>{item.mark}</span><b>{item.label}</b></button>)}</nav><div className="v4-safety-lock"><i /><div><b>RESEARCH ONLY</b><small>Orders disabled</small></div></div></aside>
    <section className="v4-main">
      <header className="v4-topbar"><div><span className="v4-mobile-brand">AQ</span><div><small>ATLAS V4 / {displayLabel(section)}</small><b>{symbol} intelligence workspace</b></div></div><div className="v4-market-controls"><label><span>INSTRUMENT</span><select aria-label="Instrument" value={symbol} onChange={(event) => setSymbol(event.target.value)}>{symbols.map((item) => <option key={item}>{item}</option>)}</select></label><div className="v4-connection"><i className={currentMarket.mt5_status === "CONNECTED" ? "live" : ""} /><span><b>{currentMarket.mt5_status ?? "V4 OFFLINE"}</b><small>{currentMarket.account_mode ?? "NO LIVE ACCOUNT EVIDENCE"}</small></span></div><button className="v4-generate-button" disabled={generating || orchestration?.enabled === false} onClick={() => void generateSignal()} type="button">{generating ? `EVALUATING ${generationProgress}` : "GENERATE SIGNAL"}</button><button disabled={loading} onClick={() => void load(symbol)} type="button">{loading ? "SYNCING" : "RELOAD STATE"}</button></div></header>
      {error ? <section className="v4-system-alert"><b>V4 BACKEND UNAVAILABLE</b><span>{error}</span><small>No demo or legacy signal has been substituted.</small></section> : null}
      <div className="v4-workspace"><div className="v4-content">
        {workspace && section === "COMMAND_CENTER" ? <CommandCenter workspace={workspace} market={currentMarket} signal={signal} /> : null}
        {workspace && section === "MARKETS" ? <MarketsView workspace={workspace} filter={marketFilter} onFilter={setMarketFilter} onSelect={selectMarket} /> : null}
        {workspace && section === "SIGNALS" ? <><SignalEnginePanel market={currentMarket} orchestration={orchestration} generating={generating} generationMessage={generationMessage} generationError={generationError} onGenerate={() => void generateSignal()} /><SignalsView signals={workspace.signals} filter={signalFilter} onFilter={setSignalFilter} /></> : null}
        {workspace && section === "ATLAS_AI" ? <AIView reviews={workspace.ai_reviews} selected={symbol} /> : null}
        {workspace && section === "CALENDAR" ? <CalendarView events={workspace.calendar} /> : null}
        {workspace && section === "PERFORMANCE" ? <PerformanceView workspace={workspace} /> : null}
        {workspace && section === "QUANT_LAB" ? <QuantLabView workspace={workspace} /> : null}
        {workspace && section === "MODELS" ? <ModelsView models={workspace.models} /> : null}
        {workspace && section === "MT5" ? <MT5View market={currentMarket} /> : null}
        {workspace && section === "SETTINGS" ? <><OrchestrationPanel status={orchestration} /><SettingsView workspace={workspace} health={operationalHealth} /></> : null}
        {!workspace ? <section className="v4-panel v4-loading"><span>{loading ? "CONNECTING TO CANONICAL V4" : "CANONICAL V4 UNAVAILABLE"}</span><h1>{loading ? "Loading persisted Atlas evidence" : "No legacy fallback"}</h1><p>The terminal will show market prices and signals only after the local V4 backend returns its typed, read-only authority contract.</p></section> : null}
      </div>{workspace ? <IntelligenceRail market={currentMarket} signal={signal} review={review} /> : null}</div>
      <footer className="v4-footer"><span>ATLAS V4 · {workspace?.version ?? "OFFLINE"}</span><span>MT5 evidence → ML → AI review → Meta → Calibration → Quant → Risk → Publication → Server tracking</span><b>ORDER SUBMISSION DISABLED</b></footer>
    </section>
    <nav className="v4-mobile-nav" aria-label="Atlas mobile navigation">{NAV_ITEMS.map((item) => <button className={section === item.id ? "active" : ""} key={item.id} onClick={() => setSection(item.id)} type="button"><span>{item.mark}</span><b>{item.label}</b></button>)}</nav>
  </main>;
}
