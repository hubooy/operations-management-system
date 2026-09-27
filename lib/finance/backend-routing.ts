export { requireDjangoRecord } from "@/lib/django/response-contract";

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(Object.keys(record).sort().map((key) => [key, canonicalValue(record[key])]));
  }
  return value;
}

async function digest(value: unknown) {
  const bytes = new TextEncoder().encode(JSON.stringify(canonicalValue(value)));
  const hashed = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hashed)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Offline migration/rehearsal helper; unreachable from production entries.
 * Shadow reads must never delay or replace the legacy response. The detached
 * comparison logs only hashes and a contract label; no finance values, names,
 * filenames, principal data, or query strings enter logs.
 */
export function observeFinanceShadow(
  label: "analysis" | "targets" | "imports",
  legacyPayload: unknown,
  djangoPayload: Promise<unknown>,
) {
  void djangoPayload.then(async (candidate) => {
    const [legacyHash, djangoHash] = await Promise.all([digest(legacyPayload), digest(candidate)]);
    if (legacyHash !== djangoHash) {
      console.warn("finance_shadow_mismatch", { label, legacyHash, djangoHash });
    }
  }).catch((error: unknown) => {
    console.warn("finance_shadow_unavailable", {
      label,
      errorType: error instanceof Error ? error.name : "unknown",
    });
  });
}
