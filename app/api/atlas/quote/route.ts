import { Mt5Provider } from "../../../lib/mt5-provider.ts";

const SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL"] as const;

function allowedSymbol(value: string | null): string {
  const symbol = (value ?? "XAUUSD").trim().toUpperCase().replaceAll("/", "");
  if (!(SYMBOLS as readonly string[]).includes(symbol)) throw new Error("Unsupported Atlas symbol.");
  return symbol;
}

export async function GET(request: Request): Promise<Response> {
  try {
    if (process.env.MT5_ENABLED?.trim().toLowerCase() !== "true") {
      throw new Error("Live quote polling requires the local MT5 bridge.");
    }
    const symbol = allowedSymbol(new URL(request.url).searchParams.get("symbol"));
    const provider = new Mt5Provider({
      port: Number(process.env.MT5_BRIDGE_PORT || 8765),
      token: process.env.MT5_BRIDGE_TOKEN || undefined,
    });
    const quote = await provider.fetchQuote(symbol);
    return Response.json({ symbol, bid: quote.bid, ask: quote.ask, time: quote.time, source: "MT5", readOnly: true, orderSubmission: false }, {
      headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
    });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Live MT5 quote failed.", readOnly: true, orderSubmission: false }, { status: 503 });
  }
}
