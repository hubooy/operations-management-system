import { PublicApiError } from "@/lib/http/api-error";

export type AiEndpointSecurityContext = {
  runtimeEnvironment?: string;
  allowLocalModelEndpoints?: string | boolean;
  modelEndpointOriginAllowlist?: string;
};

const SENSITIVE_MODEL_QUERY_MARKERS = [
  "apikey",
  "accesskey",
  "accesstoken",
  "authtoken",
  "bearertoken",
  "token",
  "secret",
  "signature",
  "credential",
  "authorization",
  "password",
  "passwd",
  "privatekey",
  "securitytoken",
] as const;

const SENSITIVE_MODEL_QUERY_TOKENS = new Set([
  "auth",
  "authorization",
  "bearer",
  "credential",
  "key",
  "password",
  "passwd",
  "pwd",
  "sas",
  "secret",
  "sig",
  "signature",
  "token",
]);

const SENSITIVE_MODEL_QUERY_COMPACT_KEYS = new Set([
  "code",
  "subscriptionkey",
  "ocpapimsubscriptionkey",
  "xfunctionskey",
  "functionkey",
  "azurefunctionkey",
]);

export function normalizeAiEndpointUrl(
  value: string,
  target: "model" | "channel",
  context: AiEndpointSecurityContext = {},
): string {
  const input = value.trim();
  if (!input) throw new Error(target === "model" ? "模型地址不能为空" : "Webhook 地址不能为空");

  let url: URL;
  try {
    url = new URL(input);
  } catch {
    throw new Error(target === "model" ? "模型地址格式无效" : "Webhook 地址格式无效");
  }

  const canonicalHost = canonicalHostname(url.hostname);
  if (!canonicalHost.includes(":")) url.hostname = canonicalHost;
  const production = isProductionRuntime(context);
  const allowLocalValue = context.allowLocalModelEndpoints ?? globalThis.process?.env?.AI_ALLOW_LOCAL_MODEL_ENDPOINTS;
  const allowLocalModel = !production && (allowLocalValue === true || allowLocalValue === "true");
  const isLocal = isLocalOrPrivateHost(canonicalHost);
  const isLocalHttp = target === "model" && allowLocalModel && url.protocol === "http:" && isLocal;
  if (url.protocol !== "https:" && !isLocalHttp) {
    throw new Error(target === "model" ? "模型地址必须使用 HTTPS；本地调试需显式设置 AI_ALLOW_LOCAL_MODEL_ENDPOINTS=true" : "Webhook 地址必须使用 HTTPS");
  }
  if (url.username || url.password) throw new Error("地址中不能包含用户名或密码");
  if (isLocal && !isLocalHttp) throw new Error("地址不能指向 localhost、内网或保留网段");
  if (target === "model" && production && !approvedProductionModelOrigins(context).has(url.origin)) {
    throw new Error("生产模型地址来源不在 AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST 白名单中");
  }
  if (url.hash) url.hash = "";
  return url.toString().replace(/\/$/, "");
}

export function isSensitiveAiModelQueryKey(value: string): boolean {
  const normalizedWithCamelBoundaries = value
    .normalize("NFKC")
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2");
  const normalized = normalizedWithCamelBoundaries.toLowerCase();
  if (/密钥|秘钥|令牌|口令|密码|签名|凭证|授权/.test(normalized)) return true;
  const tokens = normalized.split(/[^a-z0-9]+/).filter(Boolean);
  if (tokens.some((token) => SENSITIVE_MODEL_QUERY_TOKENS.has(token))) return true;
  const compact = normalized.replace(/[^a-z0-9]/g, "");
  if (!compact) return false;
  if (SENSITIVE_MODEL_QUERY_COMPACT_KEYS.has(compact)) return true;
  if (["auth", "bearer", "jwt", "key", "pwd", "sas", "sig"].includes(compact)) return true;
  return SENSITIVE_MODEL_QUERY_MARKERS.some((marker) => compact.includes(marker));
}

export function normalizeAiModelEndpointForStorage(value: string, context: AiEndpointSecurityContext = {}): string {
  let normalized: string;
  try {
    normalized = normalizeAiEndpointUrl(value, "model", context);
  } catch (error) {
    throw new PublicApiError(
      400,
      "invalid_request",
      error instanceof Error ? error.message : "模型地址格式无效",
    );
  }
  const url = new URL(normalized);
  if ([...url.searchParams.keys()].some(isSensitiveAiModelQueryKey)) {
    throw new PublicApiError(
      400,
      "invalid_request",
      "模型地址不能包含 API Key、Token、签名或凭证等敏感查询参数，请使用独立密钥配置。",
    );
  }
  return normalized;
}

/** Removes legacy query-string credentials from browser-facing model DTOs. */
export function redactAiModelEndpointUrl(value: string): string {
  if (!value) return "";
  try {
    const url = new URL(value);
    url.username = "";
    url.password = "";
    url.hash = "";
    const sensitiveKeys = [...new Set([...url.searchParams.keys()].filter(isSensitiveAiModelQueryKey))];
    for (const key of sensitiveKeys) url.searchParams.delete(key);
    const serialized = url.toString();
    return !url.search && serialized.endsWith("/") ? serialized.slice(0, -1) : serialized;
  } catch {
    return "";
  }
}

export function resolveAiModelEndpointUrl(
  value: string,
  protocol: "openai_compatible" | "anthropic",
  context: AiEndpointSecurityContext = {},
): string {
  const normalized = normalizeAiEndpointUrl(value, "model", context);
  const suffix = protocol === "anthropic" ? "/messages" : "/chat/completions";
  const url = new URL(normalized);
  const path = url.pathname.replace(/\/$/, "");
  if (!path.toLowerCase().endsWith(suffix)) url.pathname = `${path}${suffix}`;
  return url.toString();
}

export function resolveAiImageGenerationEndpointUrl(value: string, context: AiEndpointSecurityContext = {}): string {
  const normalized = normalizeAiEndpointUrl(value, "model", context);
  const suffix = "/images/generations";
  const url = new URL(normalized);
  const path = url.pathname.replace(/\/$/, "");
  if (!path.toLowerCase().endsWith(suffix)) url.pathname = `${path}${suffix}`;
  return url.toString();
}

/** Never expose a usable webhook URL. Paths and query strings can both contain credentials. */
export function maskWebhookUrl(value: string): string {
  if (!value) return "未配置";
  try {
    const url = new URL(value);
    const suffix = value.slice(-4);
    return `${url.protocol}//${url.host}/•••${url.search ? "?…" : ""} ••••${suffix}`;
  } catch {
    return "已配置 ••••";
  }
}

function isProductionRuntime(context: AiEndpointSecurityContext): boolean {
  const environment = globalThis.process?.env;
  const explicit = context.runtimeEnvironment?.trim().toLowerCase()
    || environment?.TERUISI_RUNTIME_ENV?.trim().toLowerCase()
    || environment?.NODE_ENV?.trim().toLowerCase();
  const viteEnvironment = (import.meta as ImportMeta & { env?: { DEV?: boolean } }).env;
  if (explicit === "development" || explicit === "test" || viteEnvironment?.DEV === true) return false;
  if (explicit === "production") return true;
  if (environment?.NODE_TEST_CONTEXT) return false;
  // Missing or unknown runtime evidence is production-equivalent and therefore fails closed.
  return true;
}

function canonicalHostname(hostname: string): string {
  return hostname.replace(/^\[|\]$/g, "").replace(/\.+$/g, "").toLowerCase();
}

function approvedProductionModelOrigins(context: AiEndpointSecurityContext): Set<string> {
  const configured = context.modelEndpointOriginAllowlist
    ?? globalThis.process?.env?.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST
    ?? "";
  const origins = configured.split(",").map((value) => canonicalHttpsOrigin(value.trim())).filter(Boolean) as string[];
  return new Set(["https://api.openai.com", ...origins]);
}

function canonicalHttpsOrigin(value: string): string | null {
  if (!value) return null;
  try {
    const url = new URL(value.includes("://") ? value : `https://${value}`);
    const host = canonicalHostname(url.hostname);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash || !host) return null;
    if (!host.includes(":")) url.hostname = host;
    return url.origin;
  } catch {
    return null;
  }
}

export async function loadAiEndpointSecurityContext(): Promise<AiEndpointSecurityContext> {
  try {
    const cloudflare = await import("cloudflare:workers");
    const runtime = cloudflare.env as unknown as Record<string, unknown>;
    return {
      runtimeEnvironment: typeof runtime.TERUISI_RUNTIME_ENV === "string" ? runtime.TERUISI_RUNTIME_ENV : undefined,
      allowLocalModelEndpoints: typeof runtime.AI_ALLOW_LOCAL_MODEL_ENDPOINTS === "string" ? runtime.AI_ALLOW_LOCAL_MODEL_ENDPOINTS : undefined,
      modelEndpointOriginAllowlist: typeof runtime.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST === "string"
        ? runtime.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST
        : undefined,
    };
  } catch {
    return {};
  }
}

function isLocalOrPrivateHost(hostname: string): boolean {
  const host = canonicalHostname(hostname);
  if (!host || host === "localhost" || host.endsWith(".localhost")) return true;
  if (host.includes(":")) return isNonPublicIpv6(host);
  return isPrivateIpv4(host);
}

function isNonPublicIpv6(host: string): boolean {
  // Direct IPv4-mapped literals are rejected conservatively; WHATWG canonicalizes
  // 127.0.0.1 to ::ffff:7f00:1, so dotted-only checks are insufficient.
  if (!/^[0-9a-f:]+$/.test(host) || host.startsWith("::ffff:")) return true;
  const first = Number.parseInt(host.split(":", 1)[0] ?? "", 16);
  if (!Number.isFinite(first) || first < 0x2000 || first > 0x3fff) return true;
  return host === "2001:db8" || host.startsWith("2001:db8:")
    || host === "2001:10" || host.startsWith("2001:10:")
    || host === "2001:20" || host.startsWith("2001:20:");
}

function isPrivateIpv4(host: string): boolean {
  const parts = host.split(".");
  if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part))) return false;
  const numbers = parts.map(Number);
  if (numbers.some((part) => part > 255)) return true;
  const [a, b] = numbers;
  return a === 0 || a === 10 || a === 127 || a >= 224
    || (a === 100 && b >= 64 && b <= 127)
    || (a === 169 && b === 254)
    || (a === 172 && b >= 16 && b <= 31)
    || (a === 192 && b === 168)
    || (a === 192 && b === 0 && (numbers[2] === 0 || numbers[2] === 2))
    || (a === 192 && b === 88 && numbers[2] === 99)
    || (a === 198 && (b === 18 || b === 19 || (b === 51 && numbers[2] === 100)))
    || (a === 203 && b === 0 && numbers[2] === 113);
}
