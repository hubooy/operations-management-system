export type JdProductDetailTaskRow = {
  fingerprint: string;
  taskId?: string;
  createdAt?: string;
};

export type JdProductDetailTaskManifest = {
  version: 1 | 2;
  status: "submitting" | "pending";
  dimension: "SKU" | "SPU";
  shopId: string;
  /** Version 2 binds recovery to the controlled registry entry and visible store. */
  storeKey?: string;
  shopName?: string;
  startDate: string;
  endDate: string;
  baseline: string[];
  rowFingerprint?: string;
  taskId?: string;
  createdAt: string;
};

export function jdProductDetailTaskFingerprint(rowText: string, expectedPrefix: string, taskId?: string) {
  const timestamp = rowText.match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}/)?.[0];
  if (taskId) return `task:${taskId}`;
  if (!timestamp || !rowText.includes(expectedPrefix)) return null;
  // The report title and creation timestamp are stable while its status changes.
  return `title:${expectedPrefix}|created:${timestamp}`;
}

/** JD displays row timestamps in Shanghai time while manifests use ISO UTC. */
export function isJdProductDetailTaskCreatedNear(manifestCreatedAt: string, rowCreatedAt: string | undefined, windowMs = 2 * 60_000) {
  if (!rowCreatedAt || !Number.isFinite(Date.parse(manifestCreatedAt))) return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$/.exec(rowCreatedAt);
  if (!match) return false;
  const rowUtc = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]) - 8, Number(match[5]), Number(match[6]));
  return Math.abs(rowUtc - Date.parse(manifestCreatedAt)) <= windowMs;
}

export function selectManifestTaskRow(
  manifest: JdProductDetailTaskManifest,
  rows: JdProductDetailTaskRow[],
): JdProductDetailTaskRow | null {
  const matches = manifest.rowFingerprint
    ? rows.filter((row) => row.fingerprint === manifest.rowFingerprint)
    : rows.filter((row) => !manifest.baseline.includes(row.fingerprint) && isJdProductDetailTaskCreatedNear(manifest.createdAt, row.createdAt));
  if (matches.length > 1) throw new Error("Ambiguous JD product-detail download task rows; refusing to choose one.");
  return matches[0] ?? null;
}

export function assertJdProductDetailTaskManifest(
  manifest: JdProductDetailTaskManifest,
  input: Pick<JdProductDetailTaskManifest, "dimension" | "shopId" | "startDate" | "endDate"> & { storeKey: string; shopName: string },
) {
  if ((manifest.version !== 1 && manifest.version !== 2) || manifest.dimension !== input.dimension
    || manifest.shopId !== input.shopId || manifest.startDate !== input.startDate || manifest.endDate !== input.endDate) {
    throw new Error("JD product-detail task manifest does not match this requested export.");
  }
  if (manifest.version === 2 && (manifest.storeKey !== input.storeKey || manifest.shopName !== input.shopName)) {
    throw new Error("JD product-detail task manifest does not match this controlled store identity.");
  }
}
