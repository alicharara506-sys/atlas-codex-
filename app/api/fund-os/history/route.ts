import { backendUrl, errorMessage, jsonHeaders, readJson } from "../../../lib/fund-os-server.ts";

async function getBackend(path: string): Promise<unknown> {
  const response = await fetch(backendUrl(path), {
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(10_000),
  });
  const payload = await readJson(response);
  if (!response.ok) throw new Error(errorMessage(payload, `Atlas backend returned HTTP ${response.status}.`));
  return payload;
}

export async function GET(): Promise<Response> {
  try {
    const [backtests, research] = await Promise.all([
      getBackend("/backtests?limit=50"),
      getBackend("/research/runs?limit=20"),
    ]);
    return Response.json({ backtests, research, execution_enabled: false }, { headers: jsonHeaders() });
  } catch (cause) {
    return Response.json({ error: cause instanceof Error ? cause.message : "Fund OS history is unavailable.", execution_enabled: false }, { status: 503, headers: jsonHeaders() });
  }
}
