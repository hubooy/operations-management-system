import { requestDjangoAi } from "@/lib/django/ai-service";
import { PublicApiError } from "@/lib/http/api-error";
import { readAiBoundedText } from "@/app/api/ai/route-helpers";
async function callback(request: Request, context: { params: Promise<{ channelId: string }> }) {
  try {
    const { channelId } = await context.params;
    if (!/^[A-Za-z0-9_-]{1,160}$/.test(channelId)) return new Response("not found", { status: 404 });
    const params = new URL(request.url).searchParams;
    for (const key of params.keys()) if (params.getAll(key).length !== 1) return new Response("invalid callback", { status: 403 });
    const result = await requestDjangoAi<{ text: string }>({ email: "ai-callback@teruisi.internal", displayName: "AI callback", role: "viewer", scope: null }, {
      path: `/api/ai/callback/${channelId}`, method: "POST",
      payload: { method: request.method, query: Object.fromEntries(params), body: request.method === "GET" ? "" : await readAiBoundedText(request, 256 * 1024) },
    }, { signal: request.signal });
    return new Response(result.data.text, { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });
  } catch (error) { return new Response(error instanceof PublicApiError && error.status === 403 ? "invalid callback" : "not found", { status: error instanceof PublicApiError && error.status === 403 ? 403 : 404, headers: { "cache-control": "no-store" } }); }
}
export const GET = callback;
export const POST = callback;
