import { PythonIntelligenceProvider } from "../../lib/intelligence-provider.ts";

const TASKS = new Set([
  "regime_classification",
  "news_tagging",
  "confluence_synthesis",
  "signal_formatting",
  "risk_review",
  "position_sizing_review",
]);

export async function POST(request: Request): Promise<Response> {
  try {
    if (process.env.ATLAS_INTELLIGENCE_ENABLED?.trim().toLowerCase() !== "true") {
      throw new Error("Atlas Python intelligence bridge is disabled.");
    }
    const body = await request.json() as { task?: unknown; payload?: unknown };
    const task = typeof body.task === "string" ? body.task : "";
    if (!TASKS.has(task)) throw new Error("Unsupported local-LLM orchestration task.");
    if (!body.payload || typeof body.payload !== "object" || Array.isArray(body.payload)) {
      throw new Error("payload must be a JSON object.");
    }
    const provider = new PythonIntelligenceProvider({
      port: Number(process.env.ATLAS_INTELLIGENCE_PORT || 8776),
      token: process.env.ATLAS_INTELLIGENCE_TOKEN || undefined,
    });
    const result = await provider.routeTask(task, body.payload as Record<string, unknown>);
    return Response.json(
      { provider: "LOCAL_OLLAMA_ONLY", cloudFallback: false, advisoryOnly: true, result },
      { headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } }
    );
  } catch (error) {
    return Response.json(
      {
        error: error instanceof Error ? error.message : "Local orchestration failed.",
        provider: "LOCAL_OLLAMA_ONLY",
        cloudFallback: false,
        advisoryOnly: true,
      },
      { status: 400, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } }
    );
  }
}
