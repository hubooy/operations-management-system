import { d1ProofVersion, d1RetiredDomains, sealProof } from "../../tools/d1-retirement-proof.mjs";

export function syntheticD1Proof(bindings: Record<string, string>) {
  return sealProof({ version: d1ProofVersion, verifiedAt: "2026-09-06T00:00:00.000Z",
    sourceSchemaSha256: "a".repeat(64), postgresEvidenceSha256: "b".repeat(64), retainedEvidenceSha256: "c".repeat(64),
    ...bindings,
    domains: d1RetiredDomains.map((domain: string) => ({ domain, cutoverId: `${domain}-fixture-cutover`,
      migrationSha256: "1".repeat(64), objectsSha256: "2".repeat(64), receiptSha256: "3".repeat(64),
      viewCount: domain === "finance" ? 0 : 1, guardCount: 3 })),
  }, "proofSha256");
}
