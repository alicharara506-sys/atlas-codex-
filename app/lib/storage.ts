// app/lib/storage.ts
// The only module allowed to touch IndexedDB. Everything else in Atlas
// gets/sets data through this file so the persistence layer can be swapped
// (e.g. for SQLite in a future native build) without touching engines or UI.

import type { ResolvedOutcome } from "./types.ts";

export const DB_NAME = "atlas-trading-terminal";
export const DB_VERSION = 3;

export const STORE_TRADES = "trades";
export const STORE_SETTINGS = "settings";
export const STORE_WATCHLISTS = "watchlists";
export const STORE_OUTCOMES = "outcomes";
export const STORE_SIGNAL_OUTCOMES = "signalOutcomes";

export type SignalOutcomeAction = "LONG" | "SHORT" | "BUY_STOP" | "SELL_STOP";
export type SignalOutcomeStatus =
  | "PENDING"
  | "ACTIVE"
  | "TP1_HIT"
  | "TP2_HIT"
  | "TP3_HIT"
  | "SL_HIT"
  | "EXPIRED"
  | "CLOSED_MANUAL";

export interface SignalOutcome {
  id: string;
  symbol: string;
  action: SignalOutcomeAction;
  entryPrice: number;
  triggerPrice?: number;
  slPrice: number;
  tp1Price: number;
  tp2Price: number;
  tp3Price: number;
  status: SignalOutcomeStatus;
  livePrices: { price: number; time: number }[];
  highestPips: number;
  lowestPips: number;
  finalPips: number;
  plannedRR: number;
  achievedRR: number;
  scoreAtGeneration: number;
  confidenceAtGeneration: string;
  regimeAtGeneration: string;
  sentimentAtGeneration: number;
  mlProbabilityAtGeneration: number;
  timestampGenerated: number;
  timestampTriggered?: number;
  timestampResolved?: number;
  hitTarget?: "TP1" | "TP2" | "TP3" | "SL";
  userNote?: string;
  userReactions: string[];
}

export interface JournalTrade {
  id: string;
  symbol: string;
  direction: "LONG" | "SHORT";
  entry: number;
  stop: number;
  target: number | null;
  exit: number | null;
  openedAt: string; // ISO
  closedAt: string | null; // ISO
  riskPercent: number;
  outcome: "OPEN" | "WIN" | "LOSS" | "BREAKEVEN";
  signalId?: string;
  notes?: string;
}

export interface Watchlist {
  id: string;
  name: string;
  symbols: string[];
}

export interface AppSettings {
  key: string; // e.g. "theme", "riskDefaults"
  value: unknown;
}

function hasIndexedDB(): boolean {
  return typeof indexedDB !== "undefined";
}

let dbPromise: Promise<IDBDatabase> | null = null;

export function openDatabase(): Promise<IDBDatabase> {
  if (!hasIndexedDB()) {
    return Promise.reject(
      new Error("IndexedDB is unavailable in this environment")
    );
  }
  if (dbPromise) return dbPromise;

  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = (event) => {
      const db = request.result;
      const oldVersion = event.oldVersion;

      if (oldVersion < 1) {
        if (!db.objectStoreNames.contains(STORE_TRADES)) {
          db.createObjectStore(STORE_TRADES, { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains(STORE_SETTINGS)) {
          db.createObjectStore(STORE_SETTINGS, { keyPath: "key" });
        }
        if (!db.objectStoreNames.contains(STORE_WATCHLISTS)) {
          db.createObjectStore(STORE_WATCHLISTS, { keyPath: "id" });
        }
      }
      if (oldVersion < 2) {
        if (!db.objectStoreNames.contains(STORE_OUTCOMES)) {
          const store = db.createObjectStore(STORE_OUTCOMES, {
            keyPath: "signalId",
          });
          store.createIndex("resolvedAt", "resolvedAt", { unique: false });
          store.createIndex("source", "source", { unique: false });
        }
      }
      if (oldVersion < 3) {
        if (!db.objectStoreNames.contains(STORE_SIGNAL_OUTCOMES)) {
          const store = db.createObjectStore(STORE_SIGNAL_OUTCOMES, {
            keyPath: "id",
          });
          store.createIndex("symbol", "symbol", { unique: false });
          store.createIndex("status", "status", { unique: false });
          store.createIndex("timestampGenerated", "timestampGenerated", {
            unique: false,
          });
        }
      }
    };

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

  return dbPromise;
}

function withStore<T>(
  storeName: string,
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => IDBRequest<T>
): Promise<T> {
  return openDatabase().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(storeName, mode);
        const store = tx.objectStore(storeName);
        const request = fn(store);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      })
  );
}

function withStoreAll(
  storeName: string,
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => void
): Promise<void> {
  return openDatabase().then(
    (db) =>
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(storeName, mode);
        fn(tx.objectStore(storeName));
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      })
  );
}

// --- Trades -----------------------------------------------------------

export function putTrade(trade: JournalTrade): Promise<void> {
  return withStore(STORE_TRADES, "readwrite", (s) => s.put(trade)).then(
    () => undefined
  );
}

export function getAllTrades(): Promise<JournalTrade[]> {
  return withStore(STORE_TRADES, "readonly", (s) => s.getAll());
}

export function deleteTrade(id: string): Promise<void> {
  return withStore(STORE_TRADES, "readwrite", (s) => s.delete(id)).then(
    () => undefined
  );
}

// --- Settings -----------------------------------------------------------

export function putSetting(key: string, value: unknown): Promise<void> {
  return withStore(STORE_SETTINGS, "readwrite", (s) =>
    s.put({ key, value })
  ).then(() => undefined);
}

export async function getSetting<T = unknown>(
  key: string
): Promise<T | undefined> {
  const record = await withStore<AppSettings | undefined>(
    STORE_SETTINGS,
    "readonly",
    (s) => s.get(key)
  );
  return record?.value as T | undefined;
}

// --- Watchlists -----------------------------------------------------------

export function putWatchlist(list: Watchlist): Promise<void> {
  return withStore(STORE_WATCHLISTS, "readwrite", (s) => s.put(list)).then(
    () => undefined
  );
}

export function getAllWatchlists(): Promise<Watchlist[]> {
  return withStore(STORE_WATCHLISTS, "readonly", (s) => s.getAll());
}

// --- Calibration outcomes -----------------------------------------------

export function putOutcomes(outcomes: ResolvedOutcome[]): Promise<void> {
  return withStoreAll(STORE_OUTCOMES, "readwrite", (store) => {
    for (const o of outcomes) store.put(o);
  });
}

export function getAllOutcomes(): Promise<ResolvedOutcome[]> {
  return withStore(STORE_OUTCOMES, "readonly", (s) => s.getAll());
}

export function deleteOutcome(signalId: string): Promise<void> {
  return withStore(STORE_OUTCOMES, "readwrite", (s) =>
    s.delete(signalId)
  ).then(() => undefined);
}

export function clearAllOutcomes(): Promise<void> {
  return withStoreAll(STORE_OUTCOMES, "readwrite", (store) => store.clear());
}

// --- Live signal lifecycle outcomes --------------------------------------

export function putSignalOutcome(outcome: SignalOutcome): Promise<void> {
  return withStore(STORE_SIGNAL_OUTCOMES, "readwrite", (store) =>
    store.put(outcome)
  ).then(() => undefined);
}

export function putSignalOutcomes(outcomes: SignalOutcome[]): Promise<void> {
  return withStoreAll(STORE_SIGNAL_OUTCOMES, "readwrite", (store) => {
    for (const outcome of outcomes) store.put(outcome);
  });
}

export function getSignalOutcome(id: string): Promise<SignalOutcome | undefined> {
  return withStore<SignalOutcome | undefined>(
    STORE_SIGNAL_OUTCOMES,
    "readonly",
    (store) => store.get(id)
  );
}

export function getAllSignalOutcomes(): Promise<SignalOutcome[]> {
  return withStore(STORE_SIGNAL_OUTCOMES, "readonly", (store) => store.getAll());
}

export function deleteSignalOutcome(id: string): Promise<void> {
  return withStore(STORE_SIGNAL_OUTCOMES, "readwrite", (store) =>
    store.delete(id)
  ).then(() => undefined);
}

export function clearAllSignalOutcomes(): Promise<void> {
  return withStoreAll(STORE_SIGNAL_OUTCOMES, "readwrite", (store) => store.clear());
}
