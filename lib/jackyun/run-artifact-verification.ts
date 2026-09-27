import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { isDeepStrictEqual } from "node:util";
import path from "node:path";

import { assertBoundDownloadProvenance, type JackyunDownloadProvenance } from "./download-provenance";
import { readJsonFile } from "./json-file";
import type { JackyunModule } from "./post-download";
import { jackyunSalesPeriod } from "./sales-period";
import {
  assertJackyunSnapshotEvidence,
  assertJackyunHandoffEvidence,
  createJackyunInputContractHash,
  type JackyunInputContract,
  jackyunExportFirstPolicyVersion,
} from "./run-contract";

export type JackyunArtifactManifestModule = {
  module?: JackyunModule;
  status?: string;
  batchId?: string | null;
  sourcePath?: string;
  sourceSha256?: string;
  inputContractHash?: string;
  outputPath?: string;
  outputSha256?: string;
};

export type JackyunArtifactSummaryResult = {
  module?: JackyunModule;
  status?: string;
  batchId?: string | null;
  rowCount?: number | null;
  warningCount?: number | null;
  auditPath?: string;
};

function inside(parent: string, child: string) {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

async function sha256File(filePath: string) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(filePath)) hash.update(chunk as Buffer);
  return hash.digest("hex");
}

function record(value: unknown) {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

function validSha(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
}

export async function verifyJackyunModuleArtifact(options: {
  runDirectory: string;
  runId: string;
  module: JackyunModule;
  snapshotDate: string;
  salesStartDate?: string;
  policyVersion: string;
  allowedDownloadHosts?: readonly string[];
  manifestModule: JackyunArtifactManifestModule;
  expectedStatus?: "completed" | "prepared";
  summaryResult?: JackyunArtifactSummaryResult;
  handoffPath?: string;
  requireAtomicHandoff?: boolean;
}) {
  const expectedStatus = options.expectedStatus ?? "completed";
  const auditPath = path.join(options.runDirectory, "audit", `${options.module}.json`);
  const audit = await readJsonFile<Record<string, unknown>>(auditPath);
  const source = record(audit.source);
  const output = record(audit.output);
  const imported = record(audit.import);
  const batch = record(imported?.batch);
  const manifest = options.manifestModule;

  if (audit.version !== 1 || audit.runId !== options.runId || audit.module !== options.module
    || audit.status !== expectedStatus || manifest.module !== options.module || manifest.status !== expectedStatus) {
    throw new Error(`吉客云 ${options.module} 审计或清单身份/状态无效`);
  }
  if (options.summaryResult) {
    const expectedAuditPath = path.resolve(auditPath);
    if (options.summaryResult.module !== options.module
      || typeof options.summaryResult.auditPath !== "string"
      || path.resolve(options.summaryResult.auditPath) !== expectedAuditPath
      || !["completed", "duplicate_ignored"].includes(String(options.summaryResult.status))) {
      throw new Error(`吉客云 ${options.module} 汇总身份、状态或审计路径无效`);
    }
  }

  const sourcePath = typeof source?.path === "string" ? path.resolve(source.path) : "";
  const copiedPath = typeof source?.copiedPath === "string" ? path.resolve(source.copiedPath) : "";
  const manifestSourcePath = typeof manifest.sourcePath === "string" ? path.resolve(manifest.sourcePath) : "";
  const copiedStat = copiedPath && inside(options.runDirectory, copiedPath)
    ? await stat(copiedPath).catch(() => null)
    : null;
  if (!sourcePath || sourcePath !== manifestSourcePath || !copiedStat?.isFile()
    || !validSha(source?.sha256) || source.sha256 !== manifest.sourceSha256
    || source.bytes !== copiedStat.size || await sha256File(copiedPath) !== source.sha256) {
    throw new Error(`吉客云 ${options.module} 归档源文件、字节数或哈希证据不一致`);
  }

  const inputContract = record(source.inputContract) as JackyunInputContract | null;
  if (!inputContract || !validSha(source.inputContractHash)
    || source.inputContractHash !== manifest.inputContractHash
    || createJackyunInputContractHash(inputContract) !== source.inputContractHash) {
    throw new Error(`吉客云 ${options.module} 输入证据摘要无法从归档审计重算`);
  }
  if (inputContract.runId !== options.runId || inputContract.policyVersion !== options.policyVersion
    || inputContract.module !== options.module || inputContract.rawSha256 !== source.sha256
    || inputContract.expectedSourceRows !== source.expectedSourceRows
    || inputContract.exportStart !== source.exportStart
    || inputContract.downloadEventAt !== source.downloadEventAt
    || typeof inputContract.baseUrl !== "string" || !inputContract.baseUrl) {
    throw new Error(`吉客云 ${options.module} 输入契约与审计事实不一致`);
  }
  assertJackyunHandoffEvidence(inputContract.handoffEvidence, options.module);
  if (!isDeepStrictEqual(inputContract.handoffEvidence, source.handoffEvidence)
    || inputContract.handoffEvidence.exportIntentAt !== inputContract.exportStart
    || inputContract.handoffEvidence.downloadEventAt !== inputContract.downloadEventAt) {
    throw new Error(`吉客云 ${options.module} 原子 handoff 时间线未与输入契约精确绑定`);
  }
  const provenance = source.downloadProvenance as JackyunDownloadProvenance | undefined;
  assertBoundDownloadProvenance(provenance, options.allowedDownloadHosts, {
    runId: options.runId,
    module: options.module,
    policyVersion: options.policyVersion,
  });
  if (!isDeepStrictEqual(inputContract.downloadProvenance, provenance)
    || inputContract.downloadEventAt !== provenance.completedAt
    || provenance.sha256 !== source.sha256 || provenance.bytes !== source.bytes) {
    throw new Error(`吉客云 ${options.module} 下载 provenance 未与输入契约和归档源精确绑定`);
  }
  if (options.requireAtomicHandoff && !options.handoffPath) {
    throw new Error(`吉客云 ${options.module} 缺少原子 handoff 路径`);
  }
  if (options.handoffPath) {
    const handoff = record(await readJsonFile<unknown>(options.handoffPath));
    const handoffTimeline = handoff ? {
      navigationIntentAt: handoff.navigationIntentAt,
      ...(handoff.queryIntentAt ? { queryIntentAt: handoff.queryIntentAt } : {}),
      tableStableAt: handoff.tableStableAt,
      exportIntentAt: handoff.exportIntentAt,
      downloadEventAt: handoff.downloadEventAt,
    } : null;
    if (!handoff || handoff.schemaVersion !== 2 || handoff.runId !== options.runId
      || handoff.policyVersion !== options.policyVersion || handoff.module !== options.module
      || typeof handoff.filePath !== "string" || path.resolve(handoff.filePath) !== sourcePath
      || handoff.expectedSourceRows !== source?.expectedSourceRows
      || !isDeepStrictEqual(handoffTimeline, inputContract.handoffEvidence)
      || !isDeepStrictEqual(handoff.downloadProvenance, provenance)
      || !isDeepStrictEqual(handoff.snapshotEvidence ?? null, source?.snapshotEvidence)) {
      throw new Error(`吉客云 ${options.module} 原子 handoff 与归档审计证据不一致`);
    }
  }
  const exportAt = Date.parse(inputContract.exportStart);
  const downloadAt = Date.parse(provenance.completedAt);
  if (!Number.isFinite(exportAt) || !Number.isFinite(downloadAt) || downloadAt < exportAt) {
    throw new Error(`吉客云 ${options.module} 导出/下载时间线无效`);
  }

  if (options.module === "inventory" || options.module === "inventory_age") {
    if (inputContract.snapshotDate !== options.snapshotDate
      || !isDeepStrictEqual(inputContract.snapshotEvidence, source.snapshotEvidence)) {
      throw new Error(`吉客云 ${options.module} 历史快照证据与输入契约不一致`);
    }
    assertJackyunSnapshotEvidence(inputContract.snapshotEvidence, {
      module: options.module,
      runId: options.runId,
      snapshotDate: options.snapshotDate,
      policyVersion: options.policyVersion,
      exportIntentAt: inputContract.exportStart,
    });
  } else if (inputContract.snapshotDate !== undefined || inputContract.snapshotEvidence !== undefined
    || source.snapshotEvidence !== null) {
    throw new Error(`吉客云 ${options.module} 不应携带历史库存快照证据`);
  }
  if (options.module === "sales" && inputContract.asOfDate !== options.snapshotDate) {
    throw new Error(`吉客云 sales 截止日期不是 ${options.snapshotDate}`);
  }
  if (options.module === "sales") {
    const period = jackyunSalesPeriod(options.snapshotDate, options.salesStartDate);
    if (inputContract.salesStartDate !== options.salesStartDate) throw new Error("销售起始日期与输入契约不一致。");
    if (options.salesStartDate !== undefined && !isDeepStrictEqual(record(imported?.result)?.salesPeriod, period)) {
      throw new Error("销售处理范围未与滚动输入契约一致。");
    }
  } else if (inputContract.salesStartDate !== undefined || options.salesStartDate !== undefined) {
    throw new Error("非销售模块不得携带销售起始日期。");
  }

  const outputPath = typeof output?.path === "string" ? path.resolve(output.path) : "";
  const manifestOutputPath = typeof manifest.outputPath === "string" ? path.resolve(manifest.outputPath) : "";
  const outputStat = outputPath && inside(options.runDirectory, outputPath)
    ? await stat(outputPath).catch(() => null)
    : null;
  if (!outputPath || outputPath !== manifestOutputPath || !outputStat?.isFile()
    || !validSha(output?.sha256) || output.sha256 !== manifest.outputSha256
    || output.bytes !== outputStat.size || await sha256File(outputPath) !== output.sha256) {
    throw new Error(`吉客云 ${options.module} 输出文件、字节数或哈希证据不一致`);
  }

  if (expectedStatus === "prepared") {
    if (batch !== null || manifest.batchId) throw new Error(`吉客云 ${options.module} prepared 结果不得携带正式批次`);
    return { auditPath, status: expectedStatus, inputContractHash: source.inputContractHash };
  }
  const batchId = typeof batch?.id === "string" ? batch.id : "";
  const rowCount = Number(batch?.rowCount);
  const warningCount = typeof batch?.warningCount === "number"
    ? batch.warningCount
    : Array.isArray(batch?.warnings) ? batch.warnings.length : 0;
  if (!batchId || batch?.status !== "completed" || !Number.isSafeInteger(rowCount) || rowCount <= 0
    || manifest.batchId !== batchId) {
    throw new Error(`吉客云 ${options.module} 精确完成批次证据无效`);
  }
  if (options.module !== "sales") {
    if (options.policyVersion === jackyunExportFirstPolicyVersion) {
      const receipt = record(record(imported?.result)?.djangoReceipt);
      const expectedSource = options.module === "inventory" ? "inventory_stock" : options.module;
      if (!receipt || !["imported", "duplicate"].includes(String(receipt.status))
        || receipt.batchId !== batchId || receipt.sourceKey !== expectedSource
        || receipt.inputSha256 !== output.sha256 || !validSha(receipt.contentHash) || !validSha(receipt.rawFileHash)
        || (receipt.status === "imported" && receipt.rawFileHash !== output.sha256)) {
        throw new Error(`吉客云 ${options.module} Django 回执未与本轮归档和精确批次绑定`);
      }
    } else {
      const expectedBatchId = options.module === "inventory"
        ? output.sha256
        : `${options.module}:${output.sha256}`;
      if (batchId !== expectedBatchId) {
        throw new Error(`吉客云 ${options.module} 批次号未与本轮归档输出 SHA-256 绑定`);
      }
    }
  }
  if (options.summaryResult && (options.summaryResult.batchId !== batchId
      || options.summaryResult.rowCount !== rowCount || options.summaryResult.warningCount !== warningCount)) {
    throw new Error(`吉客云 ${options.module} 汇总与审计批次/行数不一致`);
  }
  if ((options.module === "inventory" || options.module === "inventory_age")
    && batch.snapshotDate !== options.snapshotDate) {
    throw new Error(`吉客云 ${options.module} 落库批次快照日期不是 ${options.snapshotDate}`);
  }
  if ((options.module === "products" || options.module === "combos") && batch.snapshotDate !== null) {
    throw new Error(`吉客云 ${options.module} 不应携带快照日期`);
  }
  return { auditPath, status: expectedStatus, batchId, rowCount, warningCount, inputContractHash: source.inputContractHash };
}
