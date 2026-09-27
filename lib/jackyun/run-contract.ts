import { createHash } from "node:crypto";
import type { JackyunDownloadProvenance } from "./download-provenance";
import type { JackyunModule } from "./post-download";
import { jackyunSalesPeriod } from "./sales-period";

export type JackyunInputContract = {
  runId: string;
  policyVersion: string;
  module: JackyunModule;
  rawSha256: string;
  snapshotDate?: string;
  snapshotEvidence?: JackyunSnapshotEvidence;
  asOfDate?: string;
  salesStartDate?: string;
  expectedSourceRows: number;
  previousComboRows?: number;
  costOutputSha256?: string;
  costSourcePath?: string;
  exportStart: string;
  downloadEventAt: string;
  downloadProvenance: JackyunDownloadProvenance;
  handoffEvidence?: JackyunHandoffEvidence;
  baseUrl: string;
};

export type JackyunHandoffEvidence = {
  navigationIntentAt: string;
  queryIntentAt?: string;
  tableStableAt: string;
  exportIntentAt: string;
  downloadEventAt: string;
};

export type JackyunHistoricalSnapshotModule = Extract<JackyunModule, "inventory" | "inventory_age">;

/**
 * Evidence that the exported table came from an explicit historical-date
 * query.  A caller-supplied snapshot date, a file modification time, or a
 * generated workbook metadata sheet are not source evidence.
 */
export type JackyunHistoricalSnapshotEvidence = {
  version: 1;
  module: JackyunHistoricalSnapshotModule;
  runId: string;
  source: "historical_date_control";
  targetDate: string;
  observedDate: string;
  controlReadbackAt: string;
  queryIntentAt: string;
  queryRefreshSource: "miniui_grid_lifecycle" | "module_network_request";
  queryRefreshCompletedAt: string;
  tableStableAt: string;
};

export const jackyunExportFirstPolicyVersion = "2026-09-06.export-first.1";
export const jackyunExportOrder = ["inventory", "combos", "sales", "inventory_age", "products"] as const;

/** A live query is a capture on its actual Shanghai date, never a historical balance. */
export type JackyunCurrentSnapshotEvidence = {
  version: 1;
  module: JackyunHistoricalSnapshotModule;
  runId: string;
  source: "current_query";
  targetDate: string;
  queryIntentAt: string;
  queryRefreshSource: "module_network_request";
  queryRefreshCompletedAt: string;
  tableStableAt: string;
};
export type JackyunSnapshotEvidence = JackyunHistoricalSnapshotEvidence | JackyunCurrentSnapshotEvidence;

export function jackyunCaptureDate(timestamp: string) {
  const milliseconds = Date.parse(timestamp);
  if (!Number.isFinite(milliseconds)) throw contractError("FIELD_MISMATCH", "采集时间无效。");
  return new Date(milliseconds + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

export function assertJackyunSnapshotEvidence(
  value: unknown,
  expected: ExpectedHistoricalSnapshotEvidence & { policyVersion: string },
): asserts value is JackyunSnapshotEvidence {
  if (expected.policyVersion !== jackyunExportFirstPolicyVersion) {
    assertJackyunHistoricalSnapshotEvidence(value, expected);
    return;
  }
  const evidence = value as Partial<JackyunCurrentSnapshotEvidence> | undefined;
  if (!evidence || evidence.version !== 1 || evidence.source !== "current_query"
    || evidence.runId !== expected.runId || evidence.module !== expected.module
    || !isIsoDate(expected.snapshotDate) || evidence.targetDate !== expected.snapshotDate
    || evidence.queryRefreshSource !== "module_network_request") {
    throw contractError("FIELD_MISMATCH", "当前查询的身份、日期或刷新来源不一致。");
  }
  const timestamps = [expected.navigationIntentAt, evidence.queryIntentAt,
    evidence.queryRefreshCompletedAt, evidence.tableStableAt, expected.exportIntentAt];
  if (timestamps.slice(1, 4).some((item) => !item)) {
    throw contractError("FIELD_MISMATCH", "当前查询缺少查询、响应或表格稳定证据。");
  }
  const present = timestamps.filter((item): item is string => typeof item === "string");
  if (present.some((item) => jackyunCaptureDate(item) !== expected.snapshotDate)
    || present.some((item, index) => index > 0 && Date.parse(item) < Date.parse(present[index - 1]))) {
    throw contractError("TABLE_TIMEOUT", "当前查询跨日或时间线倒序；不能回填为昨天的历史快照。");
  }
}

type ExpectedHistoricalSnapshotEvidence = {
  module: JackyunHistoricalSnapshotModule;
  runId: string;
  snapshotDate: string;
  navigationIntentAt?: string;
  exportIntentAt?: string;
};

function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day;
}

function contractError(code: "FIELD_MISMATCH" | "TABLE_TIMEOUT", message: string) {
  return Object.assign(new Error(`${code}: ${message}`), { code });
}

export function assertJackyunHandoffEvidence(
  value: unknown,
  module: JackyunModule,
): asserts value is JackyunHandoffEvidence {
  if (!value || typeof value !== "object") {
    throw contractError("FIELD_MISMATCH", `${module} 缺少原子 handoff 时间线证据。`);
  }
  const evidence = value as Partial<JackyunHandoffEvidence>;
  const navigationAt = Date.parse(evidence.navigationIntentAt ?? "");
  const queryAt = Date.parse(evidence.queryIntentAt ?? "");
  const tableAt = Date.parse(evidence.tableStableAt ?? "");
  const exportAt = Date.parse(evidence.exportIntentAt ?? "");
  const downloadAt = Date.parse(evidence.downloadEventAt ?? "");
  const requiresQuery = module === "inventory" || module === "inventory_age" || module === "sales";
  if (![navigationAt, tableAt, exportAt, downloadAt].every(Number.isFinite)
    || (requiresQuery && !Number.isFinite(queryAt))) {
    throw contractError("FIELD_MISMATCH", `${module} 的 handoff 时间线证据不完整。`);
  }
  const refreshBaseAt = requiresQuery ? queryAt : navigationAt;
  if (refreshBaseAt < navigationAt || tableAt < refreshBaseAt || exportAt < tableAt || downloadAt < exportAt) {
    throw contractError("TABLE_TIMEOUT", `${module} 的导航、查询、表格、导出、下载时间线顺序无效。`);
  }
}

export function assertJackyunHistoricalSnapshotEvidence(
  value: unknown,
  expected: ExpectedHistoricalSnapshotEvidence,
): asserts value is JackyunHistoricalSnapshotEvidence {
  if (!value || typeof value !== "object") {
    throw contractError("FIELD_MISMATCH", `${expected.module} 缺少历史快照页面证据，不能把实时数据标记为 ${expected.snapshotDate}。`);
  }
  const evidence = value as Partial<JackyunHistoricalSnapshotEvidence>;
  if (evidence.version !== 1 || evidence.source !== "historical_date_control") {
    throw contractError("FIELD_MISMATCH", `${expected.module} 的快照证据来源不是历史日期控件。`);
  }
  if (evidence.module !== expected.module || evidence.runId !== expected.runId) {
    throw contractError("FIELD_MISMATCH", `${expected.module} 的快照证据与本轮运行身份不一致。`);
  }
  if (!isIsoDate(evidence.targetDate) || !isIsoDate(evidence.observedDate)
    || evidence.targetDate !== expected.snapshotDate || evidence.observedDate !== expected.snapshotDate) {
    throw contractError(
      "FIELD_MISMATCH",
      `${expected.module} 历史日期读回不一致：期望 ${expected.snapshotDate}，目标 ${evidence.targetDate ?? "缺失"}，读回 ${evidence.observedDate ?? "缺失"}。`,
    );
  }
  const readbackAt = Date.parse(evidence.controlReadbackAt ?? "");
  const queryAt = Date.parse(evidence.queryIntentAt ?? "");
  const refreshCompletedAt = Date.parse(evidence.queryRefreshCompletedAt ?? "");
  const stableAt = Date.parse(evidence.tableStableAt ?? "");
  if (evidence.queryRefreshSource !== "module_network_request"
    || ![readbackAt, queryAt, refreshCompletedAt, stableAt].every(Number.isFinite)) {
    throw contractError("FIELD_MISMATCH", `${expected.module} 的历史日期读回、查询刷新或表格稳定时间证据不完整。`);
  }
  if (readbackAt > queryAt) {
    throw contractError("FIELD_MISMATCH", `${expected.module} 的历史日期读回发生在查询之后，不能证明本轮查询条件。`);
  }
  if (queryAt > stableAt) {
    throw contractError("TABLE_TIMEOUT", `${expected.module} 的表格稳定时间早于本轮历史查询。`);
  }
  if (refreshCompletedAt < queryAt || refreshCompletedAt > stableAt) {
    throw contractError("TABLE_TIMEOUT", `${expected.module} 的查询刷新完成时间不在本轮查询与表格稳定窗口内。`);
  }
  if (expected.navigationIntentAt) {
    const navigationAt = Date.parse(expected.navigationIntentAt);
    if (!Number.isFinite(navigationAt)) {
      throw contractError("FIELD_MISMATCH", `${expected.module} 的页面导航时间无效。`);
    }
    if (readbackAt < navigationAt) {
      throw contractError("FIELD_MISMATCH", `${expected.module} 的历史日期读回早于本轮页面导航。`);
    }
  }
  if (expected.exportIntentAt) {
    const exportAt = Date.parse(expected.exportIntentAt);
    if (!Number.isFinite(exportAt)) {
      throw contractError("FIELD_MISMATCH", `${expected.module} 的导出时间无效。`);
    }
    if (stableAt > exportAt) {
      throw contractError("TABLE_TIMEOUT", `${expected.module} 在表格稳定前已触发导出。`);
    }
  }
}

export type JackyunSourceRowCountCorrection = {
  reason: "exact_total_after_approximate_count";
  previousExpectedSourceRows: number;
  exactExpectedSourceRows: number;
  observedAt: string;
};

export function isValidJackyunSourceRowCountCorrection(
  value: unknown,
  expectedSourceRows?: number,
): value is JackyunSourceRowCountCorrection {
  if (!value || typeof value !== "object") return false;
  const correction = value as Partial<JackyunSourceRowCountCorrection>;
  return correction.reason === "exact_total_after_approximate_count"
    && Number.isSafeInteger(correction.previousExpectedSourceRows)
    && Number(correction.previousExpectedSourceRows) > 0
    && Number.isSafeInteger(correction.exactExpectedSourceRows)
    && Number(correction.exactExpectedSourceRows) > 0
    && correction.previousExpectedSourceRows !== correction.exactExpectedSourceRows
    && (expectedSourceRows === undefined || correction.exactExpectedSourceRows === expectedSourceRows)
    && typeof correction.observedAt === "string"
    && Number.isFinite(Date.parse(correction.observedAt));
}

export function createJackyunInputContractHash(contract: JackyunInputContract) {
  if (contract.salesStartDate !== undefined) {
    if (contract.module !== "sales" || !contract.asOfDate) throw new Error("销售滚动范围不能绑定其他模块。");
    jackyunSalesPeriod(contract.asOfDate, contract.salesStartDate);
  }
  const stableContract = {
    version: contract.salesStartDate === undefined ? 3 : 4,
    runId: contract.runId,
    policyVersion: contract.policyVersion,
    module: contract.module,
    rawSha256: contract.rawSha256,
    snapshotDate: contract.snapshotDate ?? null,
    snapshotEvidence: contract.snapshotEvidence ?? null,
    asOfDate: contract.asOfDate ?? null,
    ...(contract.salesStartDate !== undefined ? { salesStartDate: contract.salesStartDate } : {}),
    expectedSourceRows: contract.expectedSourceRows,
    previousComboRows: contract.previousComboRows ?? null,
    costOutputSha256: contract.costOutputSha256 ?? null,
    costSourcePath: contract.costSourcePath ?? null,
    exportStart: contract.exportStart,
    downloadEventAt: contract.downloadEventAt,
    downloadProvenance: contract.downloadProvenance,
    handoffEvidence: contract.handoffEvidence ?? null,
    baseUrl: contract.baseUrl,
  };
  return createHash("sha256").update(JSON.stringify(stableContract), "utf8").digest("hex");
}
