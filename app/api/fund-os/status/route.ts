import { backendUrl, errorMessage, jsonHeaders, readJson } from "../../../lib/fund-os-server.ts";

async function probe(path: string): Promise<{ ok: boolean; payload: unknown }> {
  try {
    const response = await fetch(backendUrl(path), {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(4_000),
    });
    return { ok: response.ok, payload: await readJson(response) };
  } catch (cause) {
    return { ok: false, payload: { error: cause instanceof Error ? cause.message : "Backend unavailable." } };
  }
}

export async function GET(): Promise<Response> {
  const [health, ready] = await Promise.all([probe("/health"), probe("/ready")]);
  const healthRow = health.payload && typeof health.payload === "object" ? health.payload as Record<string, unknown> : {};
  const readyRow = ready.payload && typeof ready.payload === "object" ? ready.payload as Record<string, unknown> : {};
  const online = health.ok;
  const issue = online
    ? (ready.ok ? null : errorMessage(ready.payload, "PostgreSQL or Redis is not ready."))
    : errorMessage(health.payload, "Atlas Fund OS backend is offline on port 8800.");
  return Response.json({
    online,
    phase: typeof healthRow.phase === "number" ? healthRow.phase : null,
    agentEngine: healthRow.agent_engine_enabled === true,
    mandateEngine: healthRow.mandate_engine_enabled === true,
    backtestEngine: healthRow.backtest_engine_enabled === true,
    database: readyRow.database === true,
    redis: readyRow.redis === true,
    executionEnabled: false,
    issue,
  }, { headers: jsonHeaders() });
}
