// The deployed trusted verifier must stay self-contained. Re-export its single
// implementation so the collector, guard and tests cannot drift apart.
export {
  d1ProofVersion, d1ReceiptVersion, d1ReceiptRelativePath, d1RetiredDomains,
  canonicalProofJson, proofHash, proofBytesHash, sealProof,
  validateD1RetirementProof, createD1RetirementReceipt, readD1RetirementReceipt,
} from "./worker-local-release.mjs";
