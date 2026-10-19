import { LocalLLMConsensusEngine } from "../../lib/sentiment-oracle.ts";

const SYMBOLS = new Set(["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL"]);

export async function POST(request: Request): Promise<Response> {
  try {
    const body = await request.json() as { symbol?: unknown; text?: unknown };
    const symbol = String(body.symbol ?? "").toUpperCase().replaceAll("/", "");
    if (!SYMBOLS.has(symbol)) throw new Error("Unsupported Atlas symbol.");
    const text = Array.isArray(body.text) ? body.text.map(String) : typeof body.text === "string" ? body.text.split("\n") : [];
    const result = await new LocalLLMConsensusEngine().analyze(symbol, text);
    return Response.json({ provider: "LOCAL_OLLAMA_ONLY", cloudFallback: false, result }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Local sentiment analysis failed.", provider: "LOCAL_OLLAMA_ONLY", cloudFallback: false }, { status: 400 });
  }
}
