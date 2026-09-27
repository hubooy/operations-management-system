import type { JackyunWorkbookModule } from "./post-download";

export type JackyunDjangoImportReceipt = {
  status: "imported" | "duplicate";
  inputSha256: string;
  batchId: string;
  contentHash: string;
  rawFileHash: string;
  sourceKey: string;
};
const validSha = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export function jackyunDjangoImportReceipt(
  module: JackyunWorkbookModule,
  inputSha256: string,
  response: Record<string, unknown>,
): JackyunDjangoImportReceipt {
  const batch = response.batch as Record<string, unknown> | undefined;
  const totals = batch?.totals as Record<string, unknown> | undefined;
  const sourceKey = module === "inventory" ? "inventory_stock" : module;
  if (response.ok === false || !["imported", "duplicate"].includes(String(response.status))
    || !batch || batch.sourceKey !== sourceKey || batch.status !== "completed"
    || typeof batch.id !== "string" || !batch.id || batch.id.length > 256
    || !validSha(inputSha256) || !validSha(totals?.contentHash) || !validSha(totals?.rawFileHash)
    || (response.status === "imported" && totals.rawFileHash !== inputSha256)) {
    throw new Error("Django 导入回执的来源、业务摘要或原文件关联无效。");
  }
  return { status: response.status as "imported" | "duplicate", inputSha256, batchId: batch.id,
    sourceKey, contentHash: totals.contentHash, rawFileHash: totals.rawFileHash };
}
