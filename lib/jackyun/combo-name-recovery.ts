import { createHash } from "node:crypto";

// This exact Django 400 was raised by _combo_rows before transaction.atomic,
// scope ownership or any business write. It is not a general failed-import retry.
export const auditedComboNameRejection = {
  runId: "n8n-export-first-844",
  sourceSha256: "b10ec87fbb0175a7041d83361d40b9266d8bc80dc1f5ed9df24d85f36d1edb67",
  inputContractHash: "b7bf347a04436a19165434491be89edf4346adf817bc6482c250361b0a022a24",
  auditSha256: "d15f2096c8d3aa03760a74644703d99e18aa875344227f3405652dc81a26a93f",
} as const;

export function isAuditedComboNameRepair(input: {
  runId: string; module: string; sourceSha256: string; inputContractHash: string;
  priorModule: { status: string; module: string; sourceSha256?: string; inputContractHash?: string; error?: string; batchId?: string | null };
  failedAudit: unknown; relationCountVerified: boolean;
}) {
  const bound = auditedComboNameRejection;
  return input.runId === bound.runId && input.module === "combos" && input.sourceSha256 === bound.sourceSha256
    && input.inputContractHash === bound.inputContractHash && input.priorModule.status === "failed"
    && input.priorModule.module === "combos" && input.priorModule.sourceSha256 === bound.sourceSha256
    && input.priorModule.inputContractHash === bound.inputContractHash && !input.priorModule.batchId
    && input.priorModule.error === "parentName 无效" && input.relationCountVerified
    && createHash("sha256").update(JSON.stringify(input.failedAudit)).digest("hex") === bound.auditSha256;
}
