export type JdProductDetailStoreIdentity = {
  shopId: string;
  shopName: string;
};

export function parseJdProductDetailStoreIdentity(
  links: Array<{ href: string | null | undefined; text: string | null | undefined }>,
): JdProductDetailStoreIdentity {
  const candidates = links.flatMap((link) => {
    const match = /(?:^|\/)index-(\d+)\.html(?:[?#].*)?$/i.exec(String(link.href ?? "").trim());
    const shopName = String(link.text ?? "")
      .replace(/\s+/g, " ")
      .replace(/\s+POP$/i, "")
      .trim();
    return match && shopName ? [{ shopId: match[1]!, shopName }] : [];
  });
  if (candidates.length !== 1) {
    throw new Error(`京东商智店铺身份链接应唯一可见，实际识别 ${candidates.length} 个。`);
  }
  return candidates[0]!;
}

export function assertJdProductDetailStoreIdentity(
  actual: JdProductDetailStoreIdentity,
  expected: JdProductDetailStoreIdentity,
) {
  if (actual.shopId !== expected.shopId || actual.shopName !== expected.shopName) {
    throw new Error(
      `京东商智店铺身份不一致：预期 ${expected.shopName}（${expected.shopId}），实际 ${actual.shopName}（${actual.shopId}）。`,
    );
  }
  return actual;
}
