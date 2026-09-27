export type MarketImageRepairIdentity = {
  category: string;
  scope: string;
  rankingDimension: "SKU";
  skuCode: string;
};

export type MarketImageRepairCandidate = MarketImageRepairIdentity & {
  productUrl: string;
  reusableImageUrl: string;
};

export type MarketImageRepairMapping = MarketImageRepairIdentity & {
  imageUrl: string;
};

export function normalizeJdMarketRepairImageUrl(value: unknown) {
  const raw = String(value ?? "").trim().replace(/^\/\//, "https://").replace(/^http:\/\//i, "https://");
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || !/^img\d+\.360buyimg\.com$/i.test(url.hostname) || url.port || url.username || url.password) return "";
    url.search = "";
    url.hash = "";
    url.pathname = url.pathname.replace(/\/n\d+\//, "/n5/");
    if (!/(^|\/)n5\/|(^|\/)imgzone\//i.test(url.pathname)) return "";
    return url.toString();
  } catch {
    return "";
  }
}
