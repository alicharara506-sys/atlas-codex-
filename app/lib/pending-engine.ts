import { atr } from "./indicators.ts";
import { determineDirection } from "./signal-engine.ts";
import type { MultiTimeframeInput } from "./signal-engine.ts";
import type { TradingSignal } from "./types.ts";

export const PENDING_MIN_SCORE = 45;
export const PENDING_MIN_ABS_LEAN = 0.04;
export const PENDING_MAX_CONFLICTS = 1;

export interface ConditionalPendingSetup {
  orderType: "BUY_STOP" | "SELL_STOP";
  status: "CONDITIONAL_UNCALIBRATED";
  trigger: number;
  stop: number;
  targets: [number, number, number];
  invalidation: number;
  weightedLean: number;
  conflicts: number;
  score: number;
  expiresAt: number;
  reason: string;
  evidence: string[];
}

function oppositeOrderFlow(signal: TradingSignal, orderType: ConditionalPendingSetup["orderType"]): boolean {
  const pressure = signal.orderFlow?.pressure;
  return orderType === "BUY_STOP"
    ? pressure === "SELL_PRESSURE" || pressure === "STRONG_SELL_PRESSURE"
    : pressure === "BUY_PRESSURE" || pressure === "STRONG_BUY_PRESSURE";
}

export function buildConditionalPendingSetup(signal: TradingSignal, mtf: MultiTimeframeInput): ConditionalPendingSetup | null {
  if (signal.direction !== "WAIT" || signal.dataStatus !== "LIVE" || signal.score < PENDING_MIN_SCORE) return null;
  const direction = determineDirection(mtf);
  if (Math.abs(direction.weightedLean) < PENDING_MIN_ABS_LEAN || direction.conflictingCount > PENDING_MAX_CONFLICTS) return null;
  const orderType = direction.weightedLean > 0 ? "BUY_STOP" : "SELL_STOP";
  const alignedStructure = orderType === "BUY_STOP" ? signal.structure === "HH_HL" : signal.structure === "LH_LL";
  if (!alignedStructure || oppositeOrderFlow(signal, orderType)) return null;

  const bars = mtf["1H"];
  const currentAtr = atr(bars, 14).at(-1);
  if (!currentAtr || !Number.isFinite(currentAtr) || currentAtr <= 0) return null;
  const recent = bars.slice(-20);
  const recentHigh = Math.max(...recent.map((bar) => bar.high));
  const recentLow = Math.min(...recent.map((bar) => bar.low));
  const buffer = currentAtr * 0.10;
  const trigger = orderType === "BUY_STOP" ? recentHigh + buffer : recentLow - buffer;
  const riskDistance = currentAtr * (signal.stopAtrMultiple ?? 1.5);
  const stop = orderType === "BUY_STOP" ? trigger - riskDistance : trigger + riskDistance;
  const sign = orderType === "BUY_STOP" ? 1 : -1;
  const targets: [number, number, number] = [1, 2, 3].map((multiple) => trigger + sign * riskDistance * multiple) as [number, number, number];
  const invalidation = orderType === "BUY_STOP" ? recentLow : recentHigh;
  return {
    orderType,
    status: "CONDITIONAL_UNCALIBRATED",
    trigger,
    stop,
    targets,
    invalidation,
    weightedLean: direction.weightedLean,
    conflicts: direction.conflictingCount,
    score: signal.score,
    expiresAt: signal.generatedAt + 4 * 60 * 60,
    reason: `${orderType.replace("_", " ")} requires a break of the recent 1H structure; it is not a market entry.`,
    evidence: [
      `Weighted D1/4H/1H/15M lean: ${direction.weightedLean.toFixed(3)}.`,
      `Timeframe conflicts: ${direction.conflictingCount}/4 (maximum ${PENDING_MAX_CONFLICTS}).`,
      `1H structure ${signal.structure} aligns with ${orderType.replace("_", " ")}.`,
      `Trigger is beyond the 20-bar structure plus 0.10 ATR; stop distance is ${(signal.stopAtrMultiple ?? 1.5).toFixed(2)} ATR.`,
      "Historical probability remains unavailable until independently resolved outcomes are calibrated.",
    ],
  };
}
