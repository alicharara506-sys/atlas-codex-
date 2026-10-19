import type {
  SignalOutcome,
  SignalOutcomeAction,
  SignalOutcomeStatus,
} from "./storage.ts";

export type LifecycleEventType =
  | "SIGNAL_TRIGGERED"
  | "TP1_HIT"
  | "TP2_HIT"
  | "TP3_HIT"
  | "SL_HIT"
  | "EXPIRED"
  | "CLOSED_MANUAL";

export interface SignalLifecycleEvent {
  type: LifecycleEventType;
  price: number;
  time: number;
  outcome: SignalOutcome;
}

export interface TrackerOptions {
  persist?: (outcome: SignalOutcome) => Promise<void> | void;
  onEvent?: (event: SignalLifecycleEvent) => void;
  sampleIntervalSeconds?: number;
  expirySeconds?: number;
}

const TERMINAL_STATUSES: SignalOutcomeStatus[] = [
  "TP3_HIT",
  "SL_HIT",
  "EXPIRED",
  "CLOSED_MANUAL",
];

export function pipSize(symbol: string): number {
  if (symbol.endsWith("JPY")) return 0.01;
  if (symbol === "XAUUSD") return 0.1;
  if (symbol === "XAGUSD" || symbol === "USOIL" || symbol === "UKOIL") return 0.01;
  return 0.0001;
}

export function actionSign(action: SignalOutcomeAction): 1 | -1 {
  return action === "LONG" || action === "BUY_STOP" ? 1 : -1;
}

export function pipsFromEntry(outcome: SignalOutcome, price: number): number {
  return ((price - outcome.entryPrice) * actionSign(outcome.action)) / pipSize(outcome.symbol);
}

export function isResolvedStatus(status: SignalOutcomeStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export class SignalLifecycleTracker {
  private readonly outcomes = new Map<string, SignalOutcome>();
  private readonly persist?: TrackerOptions["persist"];
  private readonly onEvent?: TrackerOptions["onEvent"];
  private readonly sampleIntervalSeconds: number;
  private readonly expirySeconds: number;

  constructor(options: TrackerOptions = {}) {
    this.persist = options.persist;
    this.onEvent = options.onEvent;
    this.sampleIntervalSeconds = options.sampleIntervalSeconds ?? 5;
    this.expirySeconds = options.expirySeconds ?? 4 * 60 * 60;
  }

  hydrate(outcomes: SignalOutcome[]): void {
    for (const outcome of outcomes) this.outcomes.set(outcome.id, structuredClone(outcome));
  }

  track(outcome: SignalOutcome): SignalOutcome {
    const existing = this.outcomes.get(outcome.id);
    if (existing) return existing;
    const copy = structuredClone(outcome);
    this.outcomes.set(copy.id, copy);
    void this.persist?.(copy);
    return copy;
  }

  get(id: string): SignalOutcome | undefined {
    const outcome = this.outcomes.get(id);
    return outcome ? structuredClone(outcome) : undefined;
  }

  all(): SignalOutcome[] {
    return [...this.outcomes.values()]
      .map((outcome) => structuredClone(outcome))
      .sort((a, b) => b.timestampGenerated - a.timestampGenerated);
  }

  update(id: string, patch: Partial<SignalOutcome>): SignalOutcome | null {
    const outcome = this.outcomes.get(id);
    if (!outcome) return null;
    const updated = { ...outcome, ...patch, id: outcome.id };
    this.save(updated);
    return structuredClone(updated);
  }

  private emit(type: LifecycleEventType, outcome: SignalOutcome, price: number, time: number): void {
    const snapshot = structuredClone(outcome);
    this.onEvent?.({ type, price, time, outcome: snapshot });
  }

  private save(outcome: SignalOutcome): void {
    this.outcomes.set(outcome.id, outcome);
    void this.persist?.(structuredClone(outcome));
  }

  observeTick(symbol: string, price: number, time = Math.floor(Date.now() / 1000)): SignalLifecycleEvent[] {
    if (!Number.isFinite(price) || price <= 0) return [];
    const events: SignalLifecycleEvent[] = [];
    for (const outcome of this.outcomes.values()) {
      if (outcome.symbol !== symbol || isResolvedStatus(outcome.status)) continue;

      const expiresAt = outcome.timestampGenerated + this.expirySeconds;
      if (outcome.status === "PENDING" && time >= expiresAt) {
        outcome.status = "EXPIRED";
        outcome.timestampResolved = time;
        outcome.finalPips = 0;
        outcome.achievedRR = 0;
        const event = { type: "EXPIRED" as const, price, time, outcome: structuredClone(outcome) };
        events.push(event);
        this.emit(event.type, outcome, price, time);
        this.save(outcome);
        continue;
      }

      const lastSample = outcome.livePrices.at(-1)?.time ?? 0;
      if (time - lastSample >= this.sampleIntervalSeconds) {
        outcome.livePrices.push({ price, time });
        if (outcome.livePrices.length > 2_880) outcome.livePrices.shift();
      }

      if (outcome.status === "PENDING") {
        const trigger = outcome.triggerPrice ?? outcome.entryPrice;
        const triggered = outcome.action === "BUY_STOP" ? price >= trigger : price <= trigger;
        if (!triggered) {
          this.save(outcome);
          continue;
        }
        outcome.status = "ACTIVE";
        outcome.timestampTriggered = time;
        const event = { type: "SIGNAL_TRIGGERED" as const, price, time, outcome: structuredClone(outcome) };
        events.push(event);
        this.emit(event.type, outcome, price, time);
      }

      const pips = pipsFromEntry(outcome, price);
      outcome.highestPips = Math.max(outcome.highestPips, pips);
      outcome.lowestPips = Math.min(outcome.lowestPips, pips);
      const sign = actionSign(outcome.action);
      const hit = (level: number): boolean => sign === 1 ? price >= level : price <= level;
      const stopHit = sign === 1 ? price <= outcome.slPrice : price >= outcome.slPrice;

      let eventType: LifecycleEventType | null = null;
      if (stopHit) {
        outcome.status = "SL_HIT";
        outcome.hitTarget = "SL";
        eventType = "SL_HIT";
      } else if (hit(outcome.tp3Price)) {
        outcome.status = "TP3_HIT";
        outcome.hitTarget = "TP3";
        eventType = "TP3_HIT";
      } else if (hit(outcome.tp2Price) && outcome.status !== "TP2_HIT") {
        outcome.status = "TP2_HIT";
        outcome.hitTarget = "TP2";
        eventType = "TP2_HIT";
      } else if (hit(outcome.tp1Price) && outcome.status !== "TP1_HIT" && outcome.status !== "TP2_HIT") {
        outcome.status = "TP1_HIT";
        outcome.hitTarget = "TP1";
        eventType = "TP1_HIT";
      }

      if (eventType) {
        outcome.finalPips = pips;
        const plannedRiskPips = Math.abs(outcome.entryPrice - outcome.slPrice) / pipSize(outcome.symbol);
        outcome.achievedRR = plannedRiskPips > 0 ? pips / plannedRiskPips : 0;
        if (eventType === "TP3_HIT" || eventType === "SL_HIT") outcome.timestampResolved = time;
        const event = { type: eventType, price, time, outcome: structuredClone(outcome) };
        events.push(event);
        this.emit(eventType, outcome, price, time);
      }
      this.save(outcome);
    }
    return events;
  }

  closeManually(id: string, price: number, time = Math.floor(Date.now() / 1000)): SignalOutcome | null {
    const outcome = this.outcomes.get(id);
    if (!outcome || isResolvedStatus(outcome.status)) return null;
    outcome.status = "CLOSED_MANUAL";
    outcome.timestampResolved = time;
    outcome.finalPips = pipsFromEntry(outcome, price);
    const plannedRiskPips = Math.abs(outcome.entryPrice - outcome.slPrice) / pipSize(outcome.symbol);
    outcome.achievedRR = plannedRiskPips > 0 ? outcome.finalPips / plannedRiskPips : 0;
    this.emit("CLOSED_MANUAL", outcome, price, time);
    this.save(outcome);
    return structuredClone(outcome);
  }
}
