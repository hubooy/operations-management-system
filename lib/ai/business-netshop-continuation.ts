import type { AiToolExecutionContext } from "./tool-registry-contract";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { createDjangoNetshopService, NETSHOP_ANALYSIS_CONTINUATION_PATH } from "@/lib/django/netshop-service";
import { PublicApiError } from "@/lib/http/api-error";

/** Checkpoint expectations come from the AI owner; this bridge grants no resume authority. */
export async function readBusinessNetshopContinuation(args: Record<string, unknown>, context: AiToolExecutionContext) {
  requireAnalysisPrincipal(context.principal);
  if (context.surface !== "business_collection") throw new PublicApiError(403, "access_denied", "仅限持久证据后台续读");
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(args)) query.set(key, String(value));
  const { data, revision } = await createDjangoNetshopService().request<Record<string, unknown>>(context.principal,
    { method: "GET", path: NETSHOP_ANALYSIS_CONTINUATION_PATH, service: "reader", query }, { signal: context.signal });
  if (revision !== args.expectedRevision || data.sourceRevision !== revision || data.sourceRef !== args.expectedSourceRef)
    throw new PublicApiError(409, "conflict", "续读来源版本或绑定与原检查点不一致");
  if (new TextEncoder().encode(JSON.stringify(data)).byteLength > 131_072)
    throw new PublicApiError(413, "payload_too_large", "续读完整页超过容量，原检查点保持不变");
  return data;
}
