export type TmallImportVerificationProof = {
  verified?: boolean;
  rowCount?: number;
  parsedRowCount?: number;
  readbackRowCount?: number;
  dataset?: string;
  platform?: string;
  shopName?: string;
  dateMin?: string | null;
  dateMax?: string | null;
};

type ExpectedTmallImportVerification = {
  status: "imported" | "duplicate";
  rowCount: number;
  dataset: string;
  platform: "天猫";
  shopName: string;
  dateMin?: string;
  dateMax?: string;
};

export function hasExactTmallImportVerification(
  verification: TmallImportVerificationProof | null | undefined,
  expected: ExpectedTmallImportVerification,
) {
  if (!verification || verification.verified !== true
    || !Number.isInteger(expected.rowCount) || expected.rowCount <= 0) {
    return false;
  }

  const hasCurrentRowCount = verification.rowCount !== undefined;
  const hasParsedRowCount = verification.parsedRowCount !== undefined;
  const hasLegacyReadbackRowCount = verification.readbackRowCount !== undefined;
  // The current Django contract returns rowCount plus readbackRowCount. The
  // legacy contract used parsedRowCount plus readbackRowCount. Only require
  // the legacy pair when the current rowCount proof is absent.
  if (!hasCurrentRowCount && hasParsedRowCount !== hasLegacyReadbackRowCount) return false;

  const suppliedCounts = [
    verification.rowCount,
    verification.parsedRowCount,
    verification.readbackRowCount,
  ].filter((value): value is number => value !== undefined);
  if (suppliedCounts.length === 0
    || suppliedCounts.some((value) => !Number.isInteger(value) || value !== expected.rowCount)) {
    return false;
  }

  const hasLegacyProof = hasParsedRowCount && hasLegacyReadbackRowCount;
  if (!hasCurrentRowCount && !hasLegacyProof) return false;

  const identities = [
    [verification.dataset, expected.dataset],
    [verification.platform, expected.platform],
    [verification.shopName, expected.shopName],
    ...(expected.dateMin === undefined ? [] : [[verification.dateMin, expected.dateMin]]),
    ...(expected.dateMax === undefined ? [] : [[verification.dateMax, expected.dateMax]]),
  ] as const;
  if (identities.some(([actual, wanted]) => actual !== undefined && actual !== wanted)) return false;

  // Current Django duplicate responses rely on the exact completed batch identity
  // plus a compact verified row-count proof. Imported responses, and the legacy
  // two-count proof, must continue to repeat the complete verification identity.
  const requiresCompleteIdentity = expected.status === "imported" || !hasCurrentRowCount;
  return !requiresCompleteIdentity || identities.every(([actual, wanted]) => actual === wanted);
}
