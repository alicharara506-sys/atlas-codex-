const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

function backendBaseUrl(): URL {
  const configured = process.env.ATLAS_V4_API_URL?.trim() || "http://127.0.0.1:8800";
  const url = new URL(configured);
  if (url.protocol !== "http:" || !LOOPBACK_HOSTS.has(url.hostname)) {
    throw new Error("ATLAS_V4_API_URL must be a loopback HTTP address.");
  }
  return url;
}

export async function GET(): Promise<Response> {
  try {
    const target = new URL("/api/v4/orchestration/status", backendBaseUrl());
    const response = await fetch(target, { cache: "no-store", signal: AbortSignal.timeout(15_000) });
    const body = await response.text();
    return new Response(body, {
      status: response.status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": response.headers.get("Content-Type") || "application/json",
        "X-Atlas-Authority": "V4-SERVER",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (cause) {
    return Response.json(
      {
        code: "V4_ORCHESTRATION_UNAVAILABLE",
        error: cause instanceof Error ? cause.message : "Atlas V4 orchestration is unavailable.",
        execution_enabled: false,
        order_submission: false,
      },
      { status: 503, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } },
    );
  }
}

export async function POST(request: Request): Promise<Response> {
  try {
    const target = new URL("/api/v4/orchestration/run", backendBaseUrl());
    const instrument = new URL(request.url).searchParams.get("instrument")?.trim();
    if (instrument) target.searchParams.set("instrument", instrument);
    const response = await fetch(target, {
      method: "POST",
      cache: "no-store",
    });
    const body = await response.text();
    return new Response(body, {
      status: response.status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": response.headers.get("Content-Type") || "application/json",
        "X-Atlas-Authority": "V4-SERVER",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (cause) {
    return Response.json(
      {
        code: "V4_ORCHESTRATION_RUN_UNAVAILABLE",
        error: cause instanceof Error ? cause.message : "Atlas V4 could not start an evaluation.",
        execution_enabled: false,
        order_submission: false,
      },
      { status: 503, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } },
    );
  }
}
