export const TMALL_YIJIU_STORE_KEY = "tmall-yijiu" as const;
export const TMALL_YIJIU_DIRECT_PM_PROTOCOL = "yijiu-direct-pm-v1" as const;
export const TMALL_YIYONG_DIRECT_PM_PROTOCOL = "yiyong-direct-pm-v1" as const;
export const TMALL_LILI_DIRECT_M_PROTOCOL = "lili-direct-m-v1" as const;
const directPmProtocols: Readonly<Record<string, string>> = Object.freeze({
  "tmall-yijiu": TMALL_YIJIU_DIRECT_PM_PROTOCOL,
  "tmall-yiyong": TMALL_YIYONG_DIRECT_PM_PROTOCOL,
});

export function tmallDirectPmProtocolForStore(storeKey: string | null): string | null {
  return storeKey !== null && Object.hasOwn(directPmProtocols, storeKey)
    ? directPmProtocols[storeKey]!
    : null;
}

export function assertTmallDirectPmStore(storeKey: string): void {
  if (!tmallDirectPmProtocolForStore(storeKey)) throw new Error("天猫 P/M 直连只允许已批准的亿玖、亿用店铺");
}
export function assertTmallDirectMasterStore(storeKey: string): void {
  if (storeKey !== "tmall-lili") assertTmallDirectPmStore(storeKey);
}
export const tmallDirectPmProtocolHeader = "x-teruisi-tmall-candidate-protocol" as const;
export const tmallDirectPromotionRoute = "/promotion-direct-v1" as const;
export const tmallDirectProductMasterRoute = "/product-master-direct-v1" as const;

export type TmallDirectPmRoute =
  | typeof tmallDirectPromotionRoute
  | typeof tmallDirectProductMasterRoute;

export function isTmallDirectPmRoute(value: string): value is TmallDirectPmRoute {
  return value === tmallDirectPromotionRoute || value === tmallDirectProductMasterRoute;
}

export function tmallDirectPmProtocolError(input: {
  route: string;
  storeKey: string | null;
  protocol: string | string[] | undefined;
}) {
  if (!isTmallDirectPmRoute(input.route)) return null;
  const expectedProtocol = input.storeKey === "tmall-lili" && input.route === tmallDirectProductMasterRoute
    ? TMALL_LILI_DIRECT_M_PROTOCOL
    : tmallDirectPmProtocolForStore(input.storeKey);
  if (!expectedProtocol) {
    return { error: "tmall_direct_pm_store_not_allowed" as const };
  }
  if (input.protocol !== expectedProtocol) {
    return { error: "missing_or_invalid_tmall_direct_pm_protocol" as const };
  }
  return null;
}
