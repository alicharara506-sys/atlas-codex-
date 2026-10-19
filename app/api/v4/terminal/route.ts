const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

function backendBaseUrl(): URL {
  const configured = process.env.ATLAS_V4_API_URL?.trim() || "http://127.0.0.1:8800";
  const url = new URL(configured);
  if (url.protocol !== "http:" || !LOOPBACK_HOSTS.has(url.hostname)) {
    throw new Error("ATLAS_V4_API_URL must be a loopback HTTP address.");
  }
  return url;
}

export async function GET(request: Request): Promise<Response> {
  const instrument = new URL(request.url).searchParams.get("instrument")?.trim().toUpperCase() || "XAUUSD";
  if (!/^[A-Z]{6,8}$/.test(instrument)) {
    return Response.json(
      { code: "INVALID_INSTRUMENT", error: "Atlas instrument is invalid." },
      { status: 422, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const target = new URL("/api/v4/terminal", backendBaseUrl());
    target.searchParams.set("instrument", instrument);
    // Building the typed terminal contract validates persisted evidence across
    // the full V4 pipeline. Local PostgreSQL can take longer while the CPU-bound
    // model worker is active, so keep the proxy alive without weakening any
    // backend evidence or publication gate.
    const response = await fetch(target, { cache: "no-store", signal: AbortSignal.timeout(60_000) });
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
        code: "V4_BACKEND_UNAVAILABLE",
        error: cause instanceof Error ? cause.message : "Atlas V4 backend is unavailable.",
        execution_enabled: false,
        order_submission: false,
      },
      { status: 503, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } },
    );
  }
}
