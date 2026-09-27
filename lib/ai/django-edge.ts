import type { AppPrincipal } from "@/lib/auth/authorization";
import { aiEnvironment, aiSha256 } from "@/lib/django/ai-service";
import { resolveAiBackgroundPrincipal } from "@/lib/ai/background-principal";
import { executeRegisteredToolCall, getMarketV2EnabledRegistry, getToolsForPrincipal, type AiToolSurface } from "@/lib/ai/tool-registry";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";
import { readAiBoundedText } from "@/app/api/ai/route-helpers";

const encoder = new TextEncoder();
const LIMIT = 9 * 1024 * 1024;
const denied = () => new PublicApiError(403, "access_denied", "AI 内部执行请求无效。");
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw denied();
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: string[]) {
  if (Object.keys(value).some(key => !keys.includes(key))) throw denied();
}
function text(value: unknown, limit = 160) {
  if (typeof value !== "string" || !value.length || value.length > limit) throw denied();
  return value;
}
export function canonicalAiEdge(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalAiEdge).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalAiEdge((value as Record<string, unknown>)[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

export async function verifyAiEdgeEnvelope(request: Request, raw: string, secret: string): Promise<AppPrincipal> {
  const url = new URL(request.url);
  const timestamp = request.headers.get("x-teruisi-timestamp") ?? "";
  const requestId = request.headers.get("x-teruisi-request-id") ?? "";
  const encoded = request.headers.get("x-teruisi-principal") ?? "";
  const sha = request.headers.get("x-teruisi-content-sha256") ?? "";
  const signature = request.headers.get("x-teruisi-signature") ?? "";
  if (request.method !== "POST" || url.pathname !== "/api/ai/internal/edge" || url.search
    || encoder.encode(secret).length < 32 || !/^\d{10}$/.test(timestamp)
    || Math.abs(Date.now() / 1000 - Number(timestamp)) > 60
    || !/^[A-Za-z0-9._:-]{1,128}$/.test(requestId) || !/^[A-Za-z0-9_-]{1,16384}$/.test(encoded)
    || !/^[a-f0-9]{64}$/.test(sha) || !/^v1=[a-f0-9]{64}$/.test(signature)
    || sha !== await aiSha256(raw)) throw denied();
  const canonical = ["v1", timestamp, requestId, "POST", url.pathname, "", sha, encoded].join("\n");
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  const bytes = Uint8Array.from(signature.slice(3).match(/../g)!, pair => parseInt(pair, 16));
  if (!await crypto.subtle.verify("HMAC", key, bytes, encoder.encode(canonical))) throw denied();
  const principal = object(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(atob(encoded.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0)))));
  exact(principal, ["email", "displayName", "role", "scope"]);
  text(principal.email, 320); text(principal.displayName, 320);
  if (!["viewer", "analyst", "operator", "admin"].includes(String(principal.role))) throw denied();
  if (principal.scope !== null) {
    const scope = object(principal.scope); exact(scope, ["warehouses", "channels", "platforms"]);
    for (const name of ["warehouses", "channels", "platforms"]) {
      if (!Array.isArray(scope[name]) || scope[name].length > 1000 || !(scope[name] as unknown[]).every(v => typeof v === "string" && v.trim().length > 0 && v.length <= 200)) throw denied();
    }
  }
  return principal as AppPrincipal;
}

function surface(value: unknown, marketV2Enabled = false): AiToolSurface {
  if (value === "business_agent_screening_promotion_market_v2") {
    if (!marketV2Enabled) throw denied();
    return value;
  }
  if (!["ai_chat", "dingtalk_chat", "ai_agent", "ai_sandbox", "business_collection", "business_agent_v2", "business_agent_budget_v1", "business_agent_integrated_v1", "business_agent_screening_v1", "business_agent_screening_promotion_v1"].includes(String(value))) throw denied();
  return value as AiToolSurface;
}
function entries(principal: AppPrincipal, value: unknown, marketV2Enabled = false) {
  return getToolsForPrincipal(principal, surface(value, marketV2Enabled), getMarketV2EnabledRegistry(marketV2Enabled)).map(({ handler: _handler, ...entry }) => { void _handler; return entry; });
}
async function dataset(body: Record<string, unknown>, principal: AppPrincipal) {
  exact(body, ["action", "dataset", "query"]);
  const query = object(body.query);
  const limit = query.limit ?? 50;
  if (!Number.isInteger(limit) || Number(limit) < 1 || Number(limit) > 50) throw denied();
  let payload: Record<string, unknown>;
  if (body.dataset === "sales_category") {
    const { getSalesCategoryAnalysisForAi } = await import("@/lib/sales/category-ai-tool");
    payload = await getSalesCategoryAnalysisForAi(query, principal);
  } else {
    if (!["netshop_product_daily", "netshop_promotion"].includes(String(body.dataset))) throw denied();
    const { getNetshopPerformanceForAi } = await import("@/lib/netshop/ai-tool");
    payload = await getNetshopPerformanceForAi({ ...query, dataset: body.dataset === "netshop_promotion" ? "promotion" : "product_daily" }, principal);
  }
  const rows = Array.isArray(payload.items) ? payload.items.slice(0, 50) : [];
  const sourceTotal = Number(payload.totalMatched ?? (payload.truncated ? rows.length + 1 : payload.returned ?? rows.length));
  return { rows, sourceTotal, truncated: sourceTotal > rows.length, dataCutoffDate: payload.dataCutoffDate ?? null, filtersApplied: payload.filtersApplied ?? { requestedPeriod: payload.requestedPeriod ?? null, coverage: payload.coverage ?? null } };
}

export async function handleAiEdge(request: Request) {
  try {
    const environment = await aiEnvironment();
    const raw = await readAiBoundedText(request, LIMIT);
    const principal = await verifyAiEdgeEnvelope(request, raw, environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "");
    const body = object(JSON.parse(raw));
    const marketV2Enabled = environment.AI_MARKET_V2_AGENT_RUNTIME_ENABLED === "true";
    let result: unknown;
    if (body.action === "authorize_background") {
      exact(body, ["action", "ownerEmail", "scopeJson"]);
      if (body.ownerEmail !== principal.email) throw denied();
      result = await resolveAiBackgroundPrincipal(text(body.ownerEmail, 320), text(body.scopeJson, 16000));
    } else if (body.action === "catalog") {
      exact(body, ["action", "surface"]); result = { entries: entries(principal, body.surface, marketV2Enabled) };
    } else if (body.action === "execute") {
      exact(body, ["action", "name", "arguments", "surface", "requestId", "providerCallId", "policyDigest"]);
      if (body.policyDigest !== await aiSha256(canonicalAiEdge(entries(principal, body.surface, marketV2Enabled)))) throw denied();
      result = await executeRegisteredToolCall(text(body.name, 100), body.arguments, { principal, surface: surface(body.surface, marketV2Enabled), requestId: text(body.requestId, 128), providerCallId: typeof body.providerCallId === "string" ? body.providerCallId.slice(0, 200) : undefined, signal: request.signal }, { entries: getMarketV2EnabledRegistry(marketV2Enabled) });
    } else if (body.action === "dataset") result = await dataset(body, principal);
    else throw denied();
    return Response.json(result, { headers: { "cache-control": "no-store" } });
  } catch (error) { return safeApiErrorResponse(error, "AI 内部执行失败", { headers: { "cache-control": "no-store" } }); }
}
