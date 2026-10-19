"use client";

import type { SignalOutcome } from "../lib/storage.ts";
import { actionSign, pipSize, pipsFromEntry } from "../lib/live-tracker.ts";

interface LiveSignalCardProps {
  outcome: SignalOutcome | null;
  livePrice: number | null;
  score: number;
  confidence: string;
  onClose: () => void;
  onEdit: () => void;
  onShare: () => void;
  onReaction: (reaction: string) => void;
  onExportChart: () => void;
  exportBusy: boolean;
  exportStatus: string | null;
}

function price(value: number, symbol: string): string {
  const digits = symbol.endsWith("JPY") ? 3 : symbol === "XAUUSD" ? 2 : ["XAGUSD", "USOIL", "UKOIL"].includes(symbol) ? 3 : 5;
  return value.toFixed(digits);
}

function targetHit(outcome: SignalOutcome, target: 1 | 2 | 3): boolean {
  const rank = outcome.hitTarget === "TP3" ? 3 : outcome.hitTarget === "TP2" ? 2 : outcome.hitTarget === "TP1" ? 1 : 0;
  return rank >= target;
}

export function LiveSignalCard({ outcome, livePrice, score, confidence, onClose, onEdit, onShare, onReaction, onExportChart, exportBusy, exportStatus }: LiveSignalCardProps) {
  if (!outcome) {
    return <article className="live-signal-card empty-live-card">
      <div><span className="eyebrow">LIVE LIFECYCLE</span><h2>No actionable signal</h2></div>
      <p>Atlas will create a locally tracked record when a LONG, SHORT, BUY STOP, or SELL STOP setup qualifies.</p>
      <button type="button" onClick={onExportChart} disabled={exportBusy}>{exportBusy ? "RENDERING CHART…" : "EXPORT STRUCTURE CHART"}</button>
      {exportStatus ? <small className="chart-export-status">{exportStatus}</small> : null}
    </article>;
  }
  const current = livePrice ?? outcome.livePrices.at(-1)?.price ?? outcome.entryPrice;
  const levels = [outcome.slPrice, outcome.entryPrice, outcome.tp1Price, outcome.tp2Price, outcome.tp3Price, current];
  const low = Math.min(...levels);
  const high = Math.max(...levels);
  const span = high - low || 1;
  const x = (value: number): number => 5 + ((value - low) / span) * 90;
  const livePips = pipsFromEntry(outcome, current);
  const sign = actionSign(outcome.action);
  const targetRows = [outcome.tp1Price, outcome.tp2Price, outcome.tp3Price] as const;
  const reactions = ["🔥", "❤️", "😎", "💎"];
  const stopPips = Math.abs(outcome.entryPrice - outcome.slPrice) / pipSize(outcome.symbol);
  return <article className={`live-signal-card ${sign === 1 ? "buy" : "sell"}`}>
    <header>
      <div><span className="symbol-badge">{outcome.symbol}</span><strong>{outcome.action.replace("_", " ")}</strong></div>
      <div className="signal-badges"><b>{score.toFixed(1)}</b><span>{confidence}</span><span>{outcome.status.replace("_", " ")}</span></div>
    </header>

    <div className="tracker-rail" aria-label="Live price tracker">
      <svg viewBox="0 0 100 34" role="img" aria-label={`Stop ${price(outcome.slPrice, outcome.symbol)}, entry ${price(outcome.entryPrice, outcome.symbol)}, live ${price(current, outcome.symbol)}`}>
        <defs><linearGradient id={`atlas-rail-${outcome.id}`} x1={sign === 1 ? "0%" : "100%"} x2={sign === 1 ? "100%" : "0%"}><stop offset="0%" stopColor="#e5545e"/><stop offset="42%" stopColor="#eab74c"/><stop offset="100%" stopColor="#4fda91"/></linearGradient></defs>
        <rect x="5" y="15" width="90" height="4" rx="2" fill={`url(#atlas-rail-${outcome.id})`} opacity=".65"/>
        <circle cx={x(outcome.slPrice)} cy="17" r="3.2" fill="#ff646d"/><circle cx={x(outcome.entryPrice)} cy="17" r="3" fill="#f4c85f"/>
        {targetRows.map((target, index) => <circle key={target} cx={x(target)} cy="17" r="3" fill={targetHit(outcome, (index + 1) as 1 | 2 | 3) ? "#d8ffe8" : "#54d993"} stroke={targetHit(outcome, (index + 1) as 1 | 2 | 3) ? "#54d993" : "none"} strokeWidth="2"/>)}
        <circle className="live-orb" cx={x(current)} cy="17" r="4.4" fill="#f6fbff" stroke="#49d3c7" strokeWidth="2"/>
      </svg>
    </div>

    <div className="live-metrics">
      <div><span>ENTRY LEVEL</span><b>{price(outcome.entryPrice, outcome.symbol)}</b></div>
      <div><span>LIVE PRICE</span><b className={livePips >= 0 ? "positive" : "negative"}>{price(current, outcome.symbol)}</b></div>
      <div><span>LIVE PIPS</span><b className={livePips >= 0 ? "positive" : "negative"}>{livePips >= 0 ? "+" : ""}{livePips.toFixed(1)}</b></div>
    </div>

    <div className="target-list">
      {targetRows.map((target, index) => {
        const targetPips = Math.abs(target - outcome.entryPrice) / pipSize(outcome.symbol);
        const hit = targetHit(outcome, (index + 1) as 1 | 2 | 3);
        return <div key={target}><span>TP{index + 1}</span><b>{price(target, outcome.symbol)}</b><em>+{targetPips.toFixed(1)} pips</em><strong className={hit ? "hit" : "pending"}>{hit ? "HIT ✓" : "PENDING"}</strong></div>;
      })}
      <div className="stop-row"><span>SL</span><b>{price(outcome.slPrice, outcome.symbol)}</b><em>-{stopPips.toFixed(1)} pips</em><strong>{outcome.hitTarget === "SL" ? "HIT" : "PROTECTED"}</strong></div>
    </div>

    <footer className="signal-card-footer">
      <div><b className={livePips >= 0 ? "positive pulse" : "negative"}>LIVE {livePips >= 0 ? "+" : ""}{livePips.toFixed(1)} Pips</b><span>SAVED {outcome.timestampResolved ? `${outcome.finalPips >= 0 ? "+" : ""}${outcome.finalPips.toFixed(1)} Pips` : "—"}</span></div>
      <div className="signal-actions"><button type="button" onClick={onExportChart} disabled={exportBusy}>{exportBusy ? "RENDERING…" : "EXPORT CHART"}</button><button type="button" onClick={onClose}>CLOSE</button><button type="button" onClick={onEdit}>EDIT</button><button type="button" onClick={onShare}>SHARE</button></div>
    </footer>
    {exportStatus ? <small className="chart-export-status">{exportStatus}</small> : null}
    <div className="reaction-row">{reactions.map((reaction) => <button className={outcome.userReactions.includes(reaction) ? "selected" : ""} type="button" key={reaction} onClick={() => onReaction(reaction)}>{reaction}</button>)}</div>
    {outcome.userNote ? <p className="signal-note">{outcome.userNote}</p> : null}
  </article>;
}
