"use client";

import type { SignalLifecycleEvent } from "../lib/live-tracker.ts";

export function ClosingNoteBanner({ event }: { event: SignalLifecycleEvent | null }) {
  if (!event) return null;
  const pips = event.outcome.finalPips;
  const positive = event.type.startsWith("TP") || (event.type === "CLOSED_MANUAL" && pips >= 0);
  const icon = event.type.startsWith("TP") ? "🎯" : event.type === "SL_HIT" ? "🛑" : event.type === "EXPIRED" ? "⏱️" : "■";
  const message = event.type === "EXPIRED"
    ? "EXPIRED — trigger never reached"
    : `${event.type.replace("_", " ")} at ${event.price} (${pips >= 0 ? "+" : ""}${pips.toFixed(1)} pips)`;
  return <aside className={`closing-note ${event.type === "EXPIRED" ? "expired" : positive ? "won" : "lost"}`}><span>{icon}</span><b>{message}</b><small>Stored locally in Atlas IndexedDB.</small></aside>;
}
