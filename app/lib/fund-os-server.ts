const BACKEND_HOST = "127.0.0.1";
const DEFAULT_BACKEND_PORT = 8800;

function backendPort(): number {
  const candidate = Number(process.env.ATLAS_BACKEND_PORT ?? DEFAULT_BACKEND_PORT);
  return Number.isInteger(candidate) && candidate > 0 && candidate <= 65_535
    ? candidate
    : DEFAULT_BACKEND_PORT;
}

export function backendUrl(path: string): string {
  if (!path.startsWith("/") || path.includes("..")) throw new Error("Invalid Atlas backend path.");
  return `http://${BACKEND_HOST}:${backendPort()}${path}`;
}

export async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { detail: text.slice(0, 1_000) };
  }
}

export function errorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object") {
    const row = payload as { detail?: unknown; error?: unknown };
    if (typeof row.detail === "string") return row.detail;
    if (typeof row.error === "string") return row.error;
    if (Array.isArray(row.detail)) {
      return row.detail.map((item) => {
        if (item && typeof item === "object" && "msg" in item) return String(item.msg);
        return String(item);
      }).join("; ");
    }
  }
  return fallback;
}

export async function postBackend(path: string, payload: unknown): Promise<{ response: Response; payload: unknown }> {
  const response = await fetch(backendUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
    signal: AbortSignal.timeout(120_000),
  });
  return { response, payload: await readJson(response) };
}

export function jsonHeaders(): HeadersInit {
  return { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
}
