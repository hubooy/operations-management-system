import { createHash } from "node:crypto";

import { parseMarketRows } from "@/lib/market/parser";

export type JdMarketRankingIdentity = {
  category: string;
  scope: string;
  rankingDimension: "SKU";
  priceBandFilter: string;
};

export type JdMarketImportRange = JdMarketRankingIdentity & {
  periodStart: string;
  periodEnd: string;
};

export type JdMarketSignedFileEvidence = {
  rawFileSha256: string;
  fileName: string;
  fileSizeBytes: number;
  sourceType: "market_ranking";
  rowCount: number;
  warningCount: 0;
  ranges: JdMarketImportRange[];
};

export type JdMarketImportReceipt = JdMarketSignedFileEvidence & {
  batchId: string;
};

export type JdMarketImportProof = {
  httpStatus: 200 | 201;
  resultStatus: "imported" | "duplicate";
  batchId: string;
  batchStatus: "completed";
  sourceType: "market_ranking";
  rowCount: number;
  warningCount: 0;
  periodStart: string;
  periodEnd: string;
  receipt: JdMarketImportReceipt;
};

export type JdMarketRecoveryPlanShape = {
  runId: string;
  version: number;
  ownerExecutionId: string;
  stage: "planned" | "running" | "executed" | "completed" | "failed";
  failure?: unknown;
  baseUrl: string;
  silentNoWindow: boolean;
  storeKey: string;
  shopId: string;
  shopName: string;
  browserProfileName: string;
  browserDebugPort: number;
  startDate: string;
  endDate: string;
  targets: Array<{
    key: string;
    categoryPath: [string, string];
    identity: JdMarketRankingIdentity & { secondIndId?: string; thirdIndId?: string };
    chunks: Array<{ importProof?: JdMarketImportProof }>;
  }>;
};

export type JdMarketPlanIdentity = Omit<JdMarketRecoveryPlanShape,
  "runId" | "ownerExecutionId" | "stage" | "failure" | "targets"> & {
    targets: Array<{
      key: string;
      categoryPath: [string, string];
      identity: JdMarketRankingIdentity & { secondIndId: string; thirdIndId: string };
    }>;
  };

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, child]) => `${JSON.stringify(key)}:${canonicalJson(child)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

function sameJson(left: unknown, right: unknown) {
  return canonicalJson(left) === canonicalJson(right);
}

function normalizedRanges(rows: ReturnType<typeof parseMarketRows>["rows"]) {
  return [...new Set(rows.map((row) => JSON.stringify({
    category: row.category,
    scope: row.scope,
    rankingDimension: row.rankingDimension,
    priceBandFilter: row.priceBandFilter,
    periodStart: row.periodStart,
    periodEnd: row.periodEnd,
  })))].sort().map((value) => JSON.parse(value) as JdMarketImportRange);
}

export function inspectJdMarketSignedCsv(input: {
  bytes: Uint8Array;
  fileName: string;
  expectedFileSizeBytes: number;
  expectedRawFileSha256: string;
  dates: string[];
  identity: JdMarketRankingIdentity;
}): JdMarketSignedFileEvidence {
  if (!/^[a-f0-9]{64}$/.test(input.expectedRawFileSha256) || input.expectedFileSizeBytes <= 0
    || input.bytes.byteLength !== input.expectedFileSizeBytes
    || createHash("sha256").update(input.bytes).digest("hex") !== input.expectedRawFileSha256) {
    throw new Error("市场榜单签收文件大小或 SHA-256 与计划不一致");
  }
  const dates = [...new Set(input.dates)];
  if (!dates.length || dates.length !== input.dates.length || dates.some((date) => !/^\d{4}-\d{2}-\d{2}$/.test(date))) {
    throw new Error("市场榜单签收文件目标日期集合无效");
  }
  const parsed = parseMarketRows({
    bytes: input.bytes,
    fileName: input.fileName,
    defaultStartDate: dates[0]!,
    defaultEndDate: dates.at(-1)!,
    defaultCategory: input.identity.category,
    defaultScope: input.identity.scope,
    defaultPriceBandFilter: input.identity.priceBandFilter,
  });
  if (parsed.warnings.length !== 0) throw new Error("市场榜单签收文件解析产生警告，拒绝恢复导入");
  const expectedDates = new Set(dates);
  const counts = new Map(dates.map((date) => [date, 0]));
  for (const row of parsed.rows) {
    if (row.periodStart !== row.periodEnd || !expectedDates.has(row.periodStart)
      || row.category !== input.identity.category || row.scope !== input.identity.scope
      || row.rankingDimension !== input.identity.rankingDimension || row.priceBandFilter !== input.identity.priceBandFilter) {
      throw new Error("市场榜单签收文件来源身份或目标日期与计划不一致");
    }
    counts.set(row.periodStart, (counts.get(row.periodStart) ?? 0) + 1);
  }
  if ([...counts.values()].some((count) => count < 1 || count > 200)) {
    throw new Error("市场榜单签收文件存在空目标日或单日行数超过 200");
  }
  const ranges = normalizedRanges(parsed.rows);
  const expectedRanges = dates.map((date) => ({ ...input.identity, periodStart: date, periodEnd: date }));
  if (!sameJson(ranges, [...expectedRanges].sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))))) {
    throw new Error("市场榜单签收文件业务范围与计划不一致");
  }
  return {
    rawFileSha256: input.expectedRawFileSha256,
    fileName: input.fileName,
    fileSizeBytes: input.expectedFileSizeBytes,
    sourceType: "market_ranking",
    rowCount: parsed.rows.length,
    warningCount: 0,
    ranges,
  };
}

export function assertJdMarketImportProof(proof: JdMarketImportProof, expected: JdMarketSignedFileEvidence) {
  if (proof.resultStatus !== "imported" && proof.resultStatus !== "duplicate") {
    throw new Error("市场榜单导入证明结果状态无效");
  }
  const expectedStatus = proof.resultStatus === "imported" ? 201 : 200;
  if (proof.httpStatus !== expectedStatus || !/^[A-Za-z0-9._:-]{1,160}$/.test(proof.batchId)
    || proof.batchStatus !== "completed" || proof.sourceType !== "market_ranking"
    || proof.rowCount !== expected.rowCount || proof.warningCount !== 0
    || proof.periodStart !== expected.ranges[0]?.periodStart || proof.periodEnd !== expected.ranges.at(-1)?.periodEnd
    || proof.receipt.batchId !== proof.batchId
    || !sameJson(proof.receipt, { ...expected, batchId: proof.batchId })) {
    throw new Error("市场榜单导入响应与签收文件、身份、日期或行数不一致");
  }
  return proof;
}

export function validateJdMarketImportResponse(httpStatus: number, payload: unknown, expected: JdMarketSignedFileEvidence): JdMarketImportProof {
  const body = payload && typeof payload === "object" && !Array.isArray(payload) ? payload as Record<string, unknown> : null;
  const resultStatus = body?.status;
  const statusMatches = (httpStatus === 201 && resultStatus === "imported") || (httpStatus === 200 && resultStatus === "duplicate");
  const batch = body?.batch && typeof body.batch === "object" && !Array.isArray(body.batch) ? body.batch as Record<string, unknown> : null;
  const receipt = body?.importReceipt && typeof body.importReceipt === "object" && !Array.isArray(body.importReceipt)
    ? body.importReceipt as JdMarketImportReceipt
    : null;
  if (!statusMatches || body?.ok !== true || !batch || !receipt || !Array.isArray(batch.warnings)
    || batch.warnings.length !== 0 || Number(batch.warningCount) !== 0) {
    const observedStatus = typeof resultStatus === "string" && resultStatus.trim()
      ? `，status=${resultStatus.trim().replace(/\s+/g, " ").slice(0, 40)}`
      : "";
    const backendError = typeof body?.error === "string" && body.error.trim()
      ? `：${body.error.trim().replace(/\s+/g, " ").slice(0, 240)}`
      : "";
    throw new Error(`市场榜单导入响应不符合严格 imported/duplicate 契约（HTTP ${httpStatus}${observedStatus}${backendError}）`);
  }
  return assertJdMarketImportProof({
    httpStatus: httpStatus as 200 | 201,
    resultStatus: resultStatus as "imported" | "duplicate",
    batchId: String(batch.id ?? ""),
    batchStatus: String(batch.status ?? "") as "completed",
    sourceType: String(batch.sourceType ?? "") as "market_ranking",
    rowCount: Number(batch.rowCount),
    warningCount: 0,
    periodStart: String(batch.periodStart ?? ""),
    periodEnd: String(batch.periodEnd ?? ""),
    receipt,
  }, expected);
}

function samePlanIdentity(plan: JdMarketRecoveryPlanShape, identity: JdMarketPlanIdentity) {
  return plan.version === identity.version && plan.baseUrl === identity.baseUrl && plan.silentNoWindow === identity.silentNoWindow
    && plan.storeKey === identity.storeKey && plan.shopId === identity.shopId && plan.shopName === identity.shopName
    && plan.browserProfileName === identity.browserProfileName && plan.browserDebugPort === identity.browserDebugPort
    && plan.startDate === identity.startDate && /^\d{4}-\d{2}-\d{2}$/.test(plan.endDate) && plan.endDate <= identity.endDate
    && sameJson(plan.targets.map((target) => ({
      key: target.key,
      categoryPath: target.categoryPath,
      identity: target.identity,
    })), identity.targets);
}

export function claimRecoverableJdMarketPlan<T extends JdMarketRecoveryPlanShape>(
  plans: T[],
  identity: JdMarketPlanIdentity,
  executionId: string,
): T | null {
  const matching = plans.filter((plan) => samePlanIdentity(plan, identity));
  if (matching.some((plan) => plan.stage === "running")) {
    throw new Error("同一市场榜单身份已有 running 计划，拒绝跨执行接管");
  }
  const unfinished = matching.filter((plan) => plan.stage === "planned" || plan.stage === "failed" || plan.stage === "executed");
  if (unfinished.length > 1) throw new Error("同一市场榜单身份存在多个未闭环计划，拒绝猜测接管");
  const completed = matching.filter((plan) => plan.stage === "completed"
    && plan.ownerExecutionId === executionId && plan.endDate === identity.endDate);
  if (!unfinished.length && completed.length > 1) {
    throw new Error("同一市场榜单执行存在多个已完成计划，拒绝猜测接管");
  }
  const candidate = unfinished[0] ?? completed[0] ?? null;
  if (!candidate) return null;
  candidate.ownerExecutionId = executionId;
  if (candidate.stage !== "completed") {
    candidate.stage = candidate.targets.every((target) => target.chunks.every((chunk) => Boolean(chunk.importProof))) ? "executed" : "planned";
    delete candidate.failure;
  }
  return candidate;
}

export function claimExactJdMarketPlan<T extends JdMarketRecoveryPlanShape>(
  plans: T[],
  identity: JdMarketPlanIdentity,
  executionId: string,
  resumeRunId: string,
): T {
  if (!/^[A-Za-z0-9._-]{1,96}$/.test(resumeRunId)) throw new Error("市场榜单显式恢复运行编号无效");
  const matchingRunId = plans.filter((plan) => plan.runId === resumeRunId);
  if (matchingRunId.length !== 1) throw new Error("市场榜单显式恢复计划不存在或运行编号不唯一");
  const candidate = matchingRunId[0]!;
  if (candidate.stage === "running" || candidate.stage === "completed"
    || (candidate.stage !== "planned" && candidate.stage !== "failed" && candidate.stage !== "executed")) {
    throw new Error(`市场榜单显式恢复拒绝 ${candidate.stage} 计划`);
  }
  if (candidate.version !== identity.version || candidate.baseUrl !== identity.baseUrl
    || candidate.silentNoWindow !== identity.silentNoWindow || candidate.storeKey !== identity.storeKey
    || candidate.shopId !== identity.shopId || candidate.shopName !== identity.shopName
    || candidate.browserProfileName !== identity.browserProfileName || candidate.browserDebugPort !== identity.browserDebugPort
    || candidate.startDate !== identity.startDate || !/^\d{4}-\d{2}-\d{2}$/.test(candidate.endDate)
    || candidate.endDate > identity.endDate || candidate.targets.length !== identity.targets.length) {
    throw new Error("市场榜单显式恢复计划的店铺、Profile、日期或隐藏模式身份不一致");
  }
  const sameStableIdentity = (plan: T) => plan.version === identity.version && plan.baseUrl === identity.baseUrl
    && plan.silentNoWindow === identity.silentNoWindow && plan.storeKey === identity.storeKey
    && plan.shopId === identity.shopId && plan.shopName === identity.shopName
    && plan.browserProfileName === identity.browserProfileName && plan.browserDebugPort === identity.browserDebugPort
    && plan.startDate === identity.startDate && /^\d{4}-\d{2}-\d{2}$/.test(plan.endDate)
    && plan.endDate <= identity.endDate && plan.targets.length === identity.targets.length
    && plan.targets.every((target, index) => {
      const expected = identity.targets[index];
      if (!expected || target.key !== expected.key || !sameJson(target.categoryPath, expected.categoryPath)
        || target.identity.category !== expected.identity.category || target.identity.scope !== expected.identity.scope
        || target.identity.rankingDimension !== expected.identity.rankingDimension
        || target.identity.priceBandFilter !== expected.identity.priceBandFilter) return false;
      const secondMissing = target.identity.secondIndId === undefined;
      const thirdMissing = target.identity.thirdIndId === undefined;
      return secondMissing === thirdMissing && (secondMissing
        || (target.identity.secondIndId === expected.identity.secondIndId
          && target.identity.thirdIndId === expected.identity.thirdIndId));
    });
  if (plans.some((plan) => plan.runId !== resumeRunId && plan.stage === "running" && sameStableIdentity(plan))) {
    throw new Error("同一市场榜单身份已有其他 running 计划，拒绝显式跨执行接管");
  }
  for (const [index, target] of candidate.targets.entries()) {
    const expected = identity.targets[index];
    if (!expected || target.key !== expected.key || !sameJson(target.categoryPath, expected.categoryPath)
      || target.identity.category !== expected.identity.category || target.identity.scope !== expected.identity.scope
      || target.identity.rankingDimension !== expected.identity.rankingDimension
      || target.identity.priceBandFilter !== expected.identity.priceBandFilter) {
      throw new Error("市场榜单显式恢复计划的类目稳定身份不一致");
    }
    const secondMissing = target.identity.secondIndId === undefined;
    const thirdMissing = target.identity.thirdIndId === undefined;
    if (secondMissing !== thirdMissing) throw new Error("市场榜单显式恢复计划的行业 ID 仅部分存在");
    if (!secondMissing && (target.identity.secondIndId !== expected.identity.secondIndId
      || target.identity.thirdIndId !== expected.identity.thirdIndId)) {
      throw new Error("市场榜单显式恢复计划的行业 ID 与当前受控配置不一致");
    }
    if (secondMissing) {
      target.identity.secondIndId = expected.identity.secondIndId;
      target.identity.thirdIndId = expected.identity.thirdIndId;
    }
  }
  candidate.ownerExecutionId = executionId;
  candidate.stage = candidate.targets.every((target) => target.chunks.every((chunk) => Boolean(chunk.importProof))) ? "executed" : "planned";
  delete candidate.failure;
  return candidate;
}
