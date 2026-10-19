import { errorMessage, jsonHeaders, postBackend } from "../../../lib/fund-os-server.ts";

export async function POST(request: Request): Promise<Response> {
  try {
    const input = await request.json() as { yamlText?: unknown };
    if (typeof input.yamlText !== "string" || input.yamlText.trim().length === 0 || input.yamlText.length > 100_000) {
      return Response.json({ error: "Mandate YAML must contain between 1 and 100,000 characters." }, { status: 400, headers: jsonHeaders() });
    }
    const result = await postBackend("/mandates/validate", { yaml_text: input.yamlText });
    if (!result.response.ok) {
      return Response.json({ error: errorMessage(result.payload, "Mandate validation failed."), details: result.payload }, { status: result.response.status, headers: jsonHeaders() });
    }
    return Response.json(result.payload, { headers: jsonHeaders() });
  } catch (cause) {
    return Response.json({ error: cause instanceof Error ? cause.message : "Fund OS validation is unavailable." }, { status: 503, headers: jsonHeaders() });
  }
}
