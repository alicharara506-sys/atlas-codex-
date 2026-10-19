"use client";

import { useState } from "react";

interface ConsensusResponse {
  provider: string;
  cloudFallback: false;
  result?: { score: number | null; availableModels: number; requiredModels: number; consensus: boolean; spike: boolean; models: { endpoint: string; score: number | null; available: boolean; error?: string }[] };
  error?: string;
}

export function LocalSentimentPanel({ symbol }: { symbol: string }) {
  const [text, setText] = useState("");
  const [result, setResult] = useState<ConsensusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  async function analyze(): Promise<void> {
    setLoading(true);
    try {
      const response = await fetch("/api/local-sentiment", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol, text }) });
      setResult(await response.json() as ConsensusResponse);
    } catch (error) {
      setResult({ provider: "LOCAL_OLLAMA_ONLY", cloudFallback: false, error: error instanceof Error ? error.message : "Local models unavailable." });
    } finally { setLoading(false); }
  }
  return <section className="local-sentiment-panel panel"><header><div><span className="eyebrow">FOUR-LLM LOCAL ORACLE</span><h2>Offline sentiment confirmation</h2></div><small>Ports 11434–11437 · no cloud fallback</small></header><p>Paste locally obtained headlines or notes. Atlas sends them only to the four loopback Ollama endpoints and requires all four numeric responses.</p><textarea value={text} onChange={(event) => setText(event.target.value)} placeholder={`Local headlines or research notes for ${symbol}…`} /><button type="button" onClick={() => void analyze()} disabled={loading || !text.trim()}>{loading ? "QUERYING LOCAL MODELS…" : "RUN LOCAL CONSENSUS"}</button>{result ? <div className="sentiment-result"><b>{result.result?.consensus ? `CONSENSUS ${result.result.score?.toFixed(2)}` : "CONSENSUS UNAVAILABLE"}</b><span>{result.error ?? `${result.result?.availableModels ?? 0}/${result.result?.requiredModels ?? 4} local models responded.`}</span>{result.result?.models.map((model) => <small key={model.endpoint}>{model.endpoint}: {model.available ? model.score : model.error}</small>)}</div> : null}</section>;
}
