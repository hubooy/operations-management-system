import { AuthorizationError, type AppPrincipal } from "@/lib/auth/authorization";
import { NetshopQueryError, type NetshopOutletFilter } from "@/lib/netshop/query-contract";

export const NETSHOP_SUPPORTED_PLATFORMS = ["京东", "天猫"] as const;

export function netshopPlatformOptionsForPrincipal(principal: AppPrincipal) {
  if (principal.scope === null) return [...NETSHOP_SUPPORTED_PLATFORMS];
  const allowed = new Set(principal.scope.platforms.map((value) => value.trim()).filter(Boolean));
  return NETSHOP_SUPPORTED_PLATFORMS.filter((platform) => allowed.has(platform));
}

export function netshopPlatformsForPrincipal(principal: AppPrincipal, requestedValues: readonly string[]) {
  const requested = [...new Set(requestedValues.map((value) => value.trim()).filter(Boolean))].slice(0, 20);
  if (principal.scope === null) return requested;
  const allowed = new Set(principal.scope.platforms.map((value) => value.trim()).filter(Boolean));
  if (allowed.size === 0) {
    throw new AuthorizationError(403, "access_denied", "当前账号没有可读取的网店平台范围");
  }
  if (requested.some((platform) => !allowed.has(platform))) {
    throw new AuthorizationError(403, "access_denied", "请求包含当前账号无权读取的网店平台");
  }
  return requested.length > 0 ? requested : [...allowed];
}

export function netshopOutletsForPrincipal(
  principal: AppPrincipal,
  requestedOutlets: readonly NetshopOutletFilter[],
  requestedPlatforms: readonly string[] = [],
) {
  const supported = new Set<string>(NETSHOP_SUPPORTED_PLATFORMS);
  if (requestedOutlets.some((outlet) => !supported.has(outlet.platform))) {
    throw new NetshopQueryError("invalid_outlet_filter", "outlet 包含不支持的网店平台");
  }
  const explicitPlatforms = new Set(requestedPlatforms.map((value) => value.trim()).filter(Boolean));
  if (explicitPlatforms.size > 0 && requestedOutlets.some((outlet) => !explicitPlatforms.has(outlet.platform))) {
    throw new NetshopQueryError("invalid_outlet_filter", "outlet 平台必须属于当前 platform 筛选");
  }
  if (principal.scope === null) return [...requestedOutlets];
  const allowed = new Set(principal.scope.platforms.map((value) => value.trim()).filter(Boolean));
  if (allowed.size === 0) {
    throw new AuthorizationError(403, "access_denied", "当前账号没有可读取的网店平台范围");
  }
  if (requestedOutlets.some((outlet) => !allowed.has(outlet.platform))) {
    throw new AuthorizationError(403, "access_denied", "请求包含当前账号无权读取的网店平台");
  }
  return [...requestedOutlets];
}
