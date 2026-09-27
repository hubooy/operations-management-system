import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";

const domains = [
  ["SALES", ""], ["FINANCE", "finance"], ["NETSHOP", "netshop"], ["MARKET", "market"],
  ["PRODUCTS", "products"], ["INVENTORY", "inventory"], ["WORKFLOW", "workflow"],
  ["CUSTOMER_SERVICE", "customerService"], ["ERP_REFERENCE", "erpReference"],
  ["ACCESS_CONTROL", "accessControl"], ["AI", "ai"], ["BI", "bi"],
] as const;
export const DJANGO_READINESS_SERVICES = domains.flatMap(([key, field]) =>
  (key === "BI" ? ["reader"] : ["reader", "writer"]).map((role) => ({
    name: `${key.toLowerCase()}.${role}`,
    variable: `TERUISI_DJANGO_${key === "ERP_REFERENCE" ? "ERP" : key}_${role.toUpperCase()}_BASE_URL`,
    field: field ? `${field}${role === "reader" ? "Reader" : "Writer"}` : role,
    aiRole: key === "AI" ? `ai_${role}` : null,
  })));

/** Read-only health probes, with no business query, credentials, or lifecycle action. */
export async function probeDjangoBackendReadiness(
  environment: Record<string, unknown>,
  options: { fetchImpl?: typeof fetch; signal?: AbortSignal; timeoutMs?: number } = {},
) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (options.signal?.aborted) abort();
  else options.signal?.addEventListener("abort", abort, { once: true });
  const timeout = Number.isSafeInteger(options.timeoutMs) && Number(options.timeoutMs) > 0
    ? Math.min(4_000, Number(options.timeoutMs)) : 4_000;
  const timer = setTimeout(abort, timeout);
  const unavailable = new Set<string>();
  let cursor = 0;
  try {
    await Promise.all(Array.from({ length: 6 }, async () => {
      while (cursor < DJANGO_READINESS_SERVICES.length) {
        const service = DJANGO_READINESS_SERVICES[cursor++];
        try {
          if (controller.signal.aborted) throw new Error("cancelled");
          const value = environment[service.variable]
            ?? (service.name === "sales.reader" ? environment.TERUISI_DJANGO_SALES_BASE_URL : undefined);
          if (typeof value !== "string") throw new Error("missing configuration");
          const url = new URL(value);
          if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash
            || url.pathname !== "/" || (url.protocol === "http:"
              && !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))) throw new Error("invalid endpoint");
          const { response, data } = await fetchBoundedJson({
            url: new URL("/health/ready", url).toString(), init: { method: "GET", cache: "no-store" },
            timeoutMs: timeout, maxBytes: 16 * 1024, fetcher: options.fetchImpl, signal: controller.signal,
          });
          const payload = data as Record<string, unknown> | null;
          if (response.status !== 200 || !response.headers.get("content-type")?.toLowerCase().startsWith("application/json")
            || !payload || payload.status !== "ready" || payload.service !== "teruisi-django"
            || payload.database !== "ready" || (service.aiRole
              ? payload.processRole !== service.aiRole || payload.authority !== "postgres"
              : payload[service.field] !== "ready")) throw new Error("not ready");
        } catch {
          unavailable.add(service.name);
        }
      }
    }));
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
  const unavailableServices = DJANGO_READINESS_SERVICES.map(({ name }) => name).filter((name) => unavailable.has(name));
  return { ok: unavailableServices.length === 0, backend: "django-postgresql" as const, unavailableServices };
}
