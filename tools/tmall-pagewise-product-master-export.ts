import { createHash, randomUUID } from "node:crypto";
import { mkdir, mkdtemp, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { Frame, Locator, Page } from "playwright-core";
import * as XLSX from "xlsx";

import { writeJsonAtomic } from "../lib/jackyun/json-file";
import { inspectTmallImportBytes } from "../lib/netshop/normalized-import";
import { getTmallStore, type TmallStore } from "../lib/netshop/tmall-store-registry";
import {
  TMALL_SELLER_ON_SALE_URL,
  createTmallBrowserDownloadSession,
  dismissImportantNotice,
  ensureTmallSellerSession,
  exportRecordDownloadCandidates,
  importTmallProductMasterFile,
  inspectTmallMasterFile,
  isTmallProductWorkbookFilename,
  launchStoreChrome,
  parseTmallShanghaiTaskTime,
  resolveTmallStagedDownloadPath,
  tmallBrowserDownloadOutcome,
  type ExportRecordDownloadCandidate,
  type TmallProductMasterStageResult,
} from "./tmall-product-master-export";

export const TMALL_PAGEWISE_EXPORT_MODE = "on_sale_pagewise_excel" as const;
export const TMALL_PAGEWISE_EXPORT_MENU = "excel商品批量导出";
export const TMALL_PAGEWISE_EXPORT_SUCCESS = "商品批量导出任务创建成功";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifactDirectory = path.join(projectRoot, "outputs", "tmall-pagewise-product-master-export");
const legacyArtifactDirectory = path.join(projectRoot, "outputs", "tmall-product-master-export");
const directMtopArtifactDirectory = path.join(projectRoot, "outputs", "tmall-direct-product-master-export");
const maximumPages = 100;
const recordWaitTimeoutMs = 10 * 60 * 1000;
const recordRefreshIntervalMs = 8_000;

type PagewisePagination = {
  totalProducts: number;
  currentPage: number;
  totalPages: number;
};

type PagewisePaginationDiagnostic = {
  checkedAt: string;
  totalAnchorCount: number;
  scopedRegionCount: number;
  parsedRegionCount: number;
  uniquePaginationCount: number;
  observed: PagewisePagination[];
};

type PagewiseTask = {
  page: number;
  itemCount: number;
  submittedAt: string;
};

export type PagewiseFileEvidence = Awaited<ReturnType<typeof inspectTmallMasterFile>> & {
  page: number;
  taskCreatedAt: string;
};

export type PagewiseAuditStage =
  | "planned"
  | "browser_ready"
  | "page_export_submitting"
  | "page_export_submitted"
  | "all_tasks_submitted"
  | "downloading"
  | "downloaded"
  | "merged"
  | "completed";

type PagewiseAudit = {
  version: 1;
  strategy: typeof TMALL_PAGEWISE_EXPORT_MODE;
  runId: string;
  storeKey: string;
  shopName: string;
  snapshotDate: string;
  startedAt: string;
  updatedAt: string;
  stage: PagewiseAuditStage;
  totalProducts?: number;
  totalPages?: number;
  currentPage?: number;
  paginationDiagnostic?: PagewisePaginationDiagnostic;
  recordPageUrl?: string;
  tasks: PagewiseTask[];
  files: PagewiseFileEvidence[];
  mergedFile?: Awaited<ReturnType<typeof inspectTmallMasterFile>>;
  importResult?: {
    status: "imported" | "duplicate";
    batchId: string;
    rowCount: number;
    warningCount: number;
  };
  lastError?: string;
  abandonment?: {
    abandonedAt: string;
    previousStage: "downloaded";
    reason: string;
    integrityFailure: {
      uniqueProductCount: number;
      expectedProductCount: number;
    };
  };
};

type PositionedCandidate = {
  frame: Frame;
  locator: Locator;
  signature: string;
  left: number;
  top: number;
  width: number;
  height: number;
  tag: string;
  role: string;
  attributes: string;
};

type PagewiseRecordChoice = {
  signature: string;
  recordIdentity?: string;
  taskCreatedAt: string;
  status: string;
  downloadReady?: boolean;
};

const canonicalMasterHeaders = [
  "商品ID", "类目ID", "类目名称", "商品标题", "一口价", "导购标题", "商品商家编码", "商品发货时间",
  "最长发货时间", "销售属性", "属性对", "SKU发货时间", "SKUID", "SKU价格", "SKU库存", "SKU商家编码",
  "生产日期", "保质期",
] as const;

const exportedMasterHeaders = [
  "商品Id", "类目id", "类目名称", "商品标题", "一口价", "导购标题", "商家编码", "发货时间",
  "最长发货时间", "销售属性", "属性对", "发货时间", "skuId", "价格（元）", "数量", "商家编码",
  "生产日期（年/月/日）", "保质期",
] as const;

function safeSegment(value: string) {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/\s+/g, "-").slice(0, 80);
}

function safeError(error: unknown) {
  return (error instanceof Error ? error.message : String(error)).replace(/[\r\n]+/g, " ").slice(0, 500);
}

function inside(directory: string, filePath: string) {
  const relative = path.relative(path.resolve(directory), path.resolve(filePath));
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function shanghaiToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const read = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${read("year")}-${read("month")}-${read("day")}`;
}

function normalizeLocalBaseUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname)) {
    throw new Error("天猫货品自动导入只允许连接本机运营系统");
  }
  return url.toString().replace(/\/$/, "");
}

function activeAuditPath(storeKey: string, auditRoot = artifactDirectory) {
  return path.join(auditRoot, `active-${safeSegment(storeKey)}.json`);
}

async function readActiveAudit(storeKey: string, auditRoot = artifactDirectory) {
  const filePath = activeAuditPath(storeKey, auditRoot);
  try {
    const audit = JSON.parse(await readFile(filePath, "utf8")) as PagewiseAudit;
    if (audit.version !== 1 || audit.strategy !== TMALL_PAGEWISE_EXPORT_MODE
      || audit.storeKey !== storeKey || !audit.runId || !audit.snapshotDate || !Array.isArray(audit.tasks)
      || !Array.isArray(audit.files)) {
      throw new Error("逐页导出活动清单结构无效");
    }
    return { filePath, audit };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

async function writeActiveAudit(audit: PagewiseAudit, auditRoot = artifactDirectory) {
  const normalized = normalizeTmallPagewiseAuditForWrite(audit);
  const updated = { ...normalized, updatedAt: new Date().toISOString() };
  await writeJsonAtomic(activeAuditPath(audit.storeKey, auditRoot), updated);
  return updated;
}

function parseDownloadedIntegrityFailure(value: string | undefined) {
  const match = value?.match(/^逐页货品文件合计 (\d+) 个唯一商品，与出售中总数 (\d+) 不一致$/);
  if (!match) return null;
  const uniqueProductCount = Number(match[1]);
  const expectedProductCount = Number(match[2]);
  if (!Number.isInteger(uniqueProductCount) || uniqueProductCount < 1
    || !Number.isInteger(expectedProductCount) || expectedProductCount < 1
    || uniqueProductCount >= expectedProductCount) return null;
  return { uniqueProductCount, expectedProductCount };
}

export async function abandonInvalidDownloadedTmallPagewiseAudit(options: {
  storeKey: string;
  reason: string;
  operatorConfirmed: boolean;
  auditDirectory?: string;
  now?: Date;
}) {
  if (options.operatorConfirmed !== true) {
    throw new Error("作废无效的逐页货品下载必须取得操作者明确确认");
  }
  const reason = options.reason.replace(/\s+/g, " ").trim();
  if (reason.length < 4 || reason.length > 300) {
    throw new Error("逐页货品任务作废原因必须为 4 至 300 个字符");
  }
  const store = await getTmallStore(options.storeKey);
  const auditRoot = path.resolve(options.auditDirectory ?? artifactDirectory);
  const existing = await readActiveAudit(store.storeKey, auditRoot);
  if (!existing) throw new Error("当前店铺不存在可作废的逐页货品活动清单");
  const audit = existing.audit;
  if (audit.shopName !== store.shopName) {
    throw new Error("逐页货品活动清单店铺身份不一致，拒绝作废");
  }
  if (audit.stage !== "downloaded") {
    throw new Error(`逐页货品活动清单阶段 ${audit.stage} 不允许按无效下载作废`);
  }
  const integrityFailure = parseDownloadedIntegrityFailure(audit.lastError);
  if (!integrityFailure || integrityFailure.expectedProductCount !== audit.totalProducts) {
    throw new Error("逐页货品活动清单没有可核验的唯一商品数完整性错误，拒绝作废");
  }
  if (!audit.totalPages || audit.tasks.length !== audit.totalPages || audit.files.length !== audit.totalPages) {
    throw new Error("逐页货品任务或文件未完整绑定全部分页，拒绝按下载完整性错误作废");
  }
  for (let page = 1; page <= audit.totalPages; page += 1) {
    const task = audit.tasks[page - 1];
    const file = audit.files[page - 1];
    if (task?.page !== page || file?.page !== page
      || task.itemCount !== expectedTmallPageItemCount(audit.totalProducts, audit.totalPages, page)) {
      throw new Error("逐页货品任务与文件页码证据不连续，拒绝作废");
    }
  }

  const abandonedAt = (options.now ?? new Date()).toISOString();
  const archiveFileName = `abandoned-invalid-downloaded-${safeSegment(store.storeKey)}-${audit.snapshotDate}-${safeSegment(audit.runId)}.json`;
  const archivePath = path.join(auditRoot, archiveFileName);
  if (await stat(archivePath).then(() => true).catch(() => false)) {
    throw new Error("逐页货品无效下载作废归档已存在，拒绝覆盖");
  }

  // The atomic rename releases only this store's active slot while preserving
  // every submitted task and downloaded-file evidence. This entry point is
  // intentionally operator-only and is never called by n8n recovery itself.
  await rename(existing.filePath, archivePath);
  const archivedAudit: PagewiseAudit = {
    ...audit,
    updatedAt: abandonedAt,
    abandonment: {
      abandonedAt,
      previousStage: "downloaded",
      reason,
      integrityFailure,
    },
  };
  await writeJsonAtomic(archivePath, archivedAudit);
  return {
    ok: true as const,
    stage: "abandoned" as const,
    storeKey: store.storeKey,
    shopName: store.shopName,
    snapshotDate: audit.snapshotDate,
    previousStage: "downloaded" as const,
    archiveFileName,
    integrityFailure,
  };
}

export function normalizeTmallPagewiseAuditForWrite<T extends { stage: PagewiseAuditStage; lastError?: string }>(audit: T) {
  return audit.stage === "completed" ? { ...audit, lastError: undefined } : audit;
}

export function parseTmallOnSalePagination(text: string): PagewisePagination | null {
  const normalized = text.replace(/\s+/g, "");
  const totalMatch = normalized.match(/共(\d{1,7})件商品/);
  const pageMatches = [...normalized.matchAll(/(?:^|\D)(\d{1,4})\/(\d{1,4})(?:\D|$)/g)];
  if (!totalMatch || pageMatches.length !== 1) return null;
  const totalProducts = Number(totalMatch[1]);
  const currentPage = Number(pageMatches[0]![1]);
  const totalPages = Number(pageMatches[0]![2]);
  if (!Number.isInteger(totalProducts) || totalProducts <= 0
    || !Number.isInteger(currentPage) || currentPage <= 0
    || !Number.isInteger(totalPages) || totalPages <= 0 || totalPages > maximumPages
    || currentPage > totalPages || Math.ceil(totalProducts / 20) !== totalPages) return null;
  return { totalProducts, currentPage, totalPages };
}

export function chooseTmallOnSalePaginationRegions(regions: readonly string[]): PagewisePagination | null {
  const parsed = regions.map((region) => parseTmallOnSalePagination(region))
    .filter((value): value is PagewisePagination => value !== null);
  const unique = new Map(parsed.map((value) => [
    `${value.totalProducts}:${value.currentPage}:${value.totalPages}`,
    value,
  ]));
  return unique.size === 1 ? [...unique.values()][0]! : null;
}

export function chooseTmallOnSaleHeaderCheckbox(candidates: readonly { signature: string; score: number }[]) {
  const unique = new Map<string, { signature: string; score: number }>();
  for (const candidate of candidates) {
    const previous = unique.get(candidate.signature);
    if (!previous || candidate.score > previous.score) unique.set(candidate.signature, candidate);
  }
  const ranked = [...unique.values()].sort((left, right) => right.score - left.score);
  if (!ranked[0] || ranked[1]?.score === ranked[0].score) return null;
  return ranked[0];
}

export function chooseTmallOnSaleNextPageCandidate(candidates: readonly { signature: string; score: number }[]) {
  const unique = new Map<string, { signature: string; score: number }>();
  for (const candidate of candidates) {
    const previous = unique.get(candidate.signature);
    if (!previous || candidate.score > previous.score) unique.set(candidate.signature, candidate);
  }
  const ranked = [...unique.values()].sort((left, right) => right.score - left.score);
  if (!ranked[0] || ranked[1]?.score === ranked[0].score) return null;
  return ranked[0];
}

export function expectedTmallPageItemCount(totalProducts: number, totalPages: number, page: number) {
  if (!Number.isInteger(totalProducts) || totalProducts <= 0 || !Number.isInteger(totalPages) || totalPages <= 0
    || !Number.isInteger(page) || page <= 0 || page > totalPages || Math.ceil(totalProducts / 20) !== totalPages) {
    throw new Error("天猫出售中分页参数无效");
  }
  return page < totalPages ? 20 : totalProducts - (totalPages - 1) * 20;
}

export function decideTmallPagewiseAuditRecovery(
  requestedSnapshotDate: string,
  audit: Pick<PagewiseAudit, "snapshotDate" | "stage" | "tasks" | "files">,
) {
  if (audit.stage === "page_export_submitting") {
    return { action: "block", snapshotDate: audit.snapshotDate } as const;
  }
  const hasBusinessEvidence = audit.tasks.length > 0 || audit.files.length > 0
    || ["all_tasks_submitted", "downloading", "downloaded", "merged"].includes(audit.stage);
  if (audit.snapshotDate !== requestedSnapshotDate && hasBusinessEvidence) {
    return { action: "resume_previous", snapshotDate: audit.snapshotDate } as const;
  }
  if (audit.snapshotDate !== requestedSnapshotDate) {
    return { action: "discard", snapshotDate: requestedSnapshotDate } as const;
  }
  return { action: "continue", snapshotDate: requestedSnapshotDate } as const;
}

export function chooseTmallPagewiseExportRecords(
  candidates: readonly PagewiseRecordChoice[],
  tasks: readonly PagewiseTask[],
) {
  if (tasks.length === 0) throw new Error("逐页导出清单没有已提交任务");
  const firstSubmittedMs = Date.parse(tasks[0]!.submittedAt);
  const lastSubmittedMs = Date.parse(tasks.at(-1)!.submittedAt);
  if (!Number.isFinite(firstSubmittedMs) || !Number.isFinite(lastSubmittedMs) || lastSubmittedMs < firstSubmittedMs) {
    throw new Error("逐页导出任务提交时间无效");
  }
  const unique = new Map<string, PagewiseRecordChoice>();
  for (const candidate of candidates) {
    const parsed = parseTmallShanghaiTaskTime(candidate.taskCreatedAt);
    if (!parsed || parsed.epochMs < firstSubmittedMs - 5_000 || parsed.epochMs > lastSubmittedMs + 20 * 60_000) continue;
    const identity = candidate.recordIdentity || candidate.signature;
    const previous = unique.get(identity);
    if (!previous || (!previous.downloadReady && candidate.downloadReady)) unique.set(identity, candidate);
  }
  const matched = [...unique.values()].sort((left, right) => (
    parseTmallShanghaiTaskTime(left.taskCreatedAt)!.epochMs - parseTmallShanghaiTaskTime(right.taskCreatedAt)!.epochMs
  ));
  if (matched.length > tasks.length) {
    throw new Error(`导出记录在本轮时间窗出现 ${matched.length} 条任务，超过预期 ${tasks.length} 条，无法排除人工或并发任务`);
  }
  const failed = matched.find((candidate) => /任务失败|生成失败/.test(candidate.status));
  if (failed) throw new Error(`本轮逐页导出任务 ${failed.taskCreatedAt} 明确失败，拒绝重复创建或下载`);
  if (matched.length < tasks.length) return null;
  if (matched.some((candidate) => candidate.status.replace(/\s+/g, "") !== "已完成" || candidate.downloadReady === false)) {
    return null;
  }
  return matched;
}

async function combinedPageText(page: Page) {
  return (await Promise.all(page.frames().map((frame) => frame.locator("body").innerText({ timeout: 5_000 }).catch(() => ""))))
    .map((text) => text.slice(0, 50_000)).join("\n");
}

async function waitUntil(timeoutMs: number, predicate: () => Promise<boolean>, message: string, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      if (await predicate()) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  if (lastError instanceof Error) throw lastError;
  throw new Error(message);
}

async function positionedCandidate(frame: Frame, locator: Locator): Promise<PositionedCandidate | null> {
  const detail = await locator.evaluate((element) => {
    const view = element.ownerDocument.defaultView;
    if (!view) return null;
    const rect = element.getBoundingClientRect();
    const style = view.getComputedStyle(element);
    if (rect.width < 2 || rect.height < 2 || style.display === "none" || style.visibility === "hidden"
      || Number(style.opacity || "1") <= 0) return null;
    return {
      left: Math.round(rect.left), top: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height),
      tag: element.tagName.toLowerCase(), role: element.getAttribute("role") ?? "",
      attributes: [element.getAttribute("aria-label"), element.getAttribute("title"), element.getAttribute("class")]
        .filter(Boolean).join(" "),
    };
  }).catch(() => null);
  if (!detail) return null;
  return { frame, locator, signature: `${frame.url()}|${detail.left}|${detail.top}|${detail.width}|${detail.height}`, ...detail };
}

async function exactTextCandidates(page: Page, text: string) {
  const candidates: PositionedCandidate[] = [];
  for (const frame of page.frames()) {
    const matches = frame.getByText(text, { exact: true });
    const count = Math.min(await matches.count().catch(() => 0), 20);
    for (let index = 0; index < count; index += 1) {
      const detail = await positionedCandidate(frame, matches.nth(index));
      if (detail) candidates.push(detail);
    }
  }
  return [...new Map(candidates.map((candidate) => [candidate.signature, candidate])).values()];
}

async function clickUniqueExactText(page: Page, text: string) {
  const candidates = await exactTextCandidates(page, text);
  if (candidates.length === 0) throw new Error(`未找到唯一可见控件“${text}”`);
  const scored = candidates.map((candidate) => ({
    ...candidate,
    score: ["button", "a"].includes(candidate.tag) || ["button", "link", "menuitem"].includes(candidate.role) ? 10 : 1,
  })).sort((left, right) => right.score - left.score);
  if (scored[1] && scored[1].score === scored[0]!.score && scored[1].signature !== scored[0]!.signature) {
    throw new Error(`存在多个同等可见控件“${text}”，为防止误点已停止`);
  }
  await scored[0]!.locator.click({ timeout: 15_000 });
}

async function waitForUniqueExactTextCandidate(page: Page, text: string, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const candidates = await exactTextCandidates(page, text);
    if (candidates.length > 0) {
      const scored = candidates.map((candidate) => ({
        ...candidate,
        score: ["button", "a"].includes(candidate.tag) || ["button", "link", "menuitem"].includes(candidate.role) ? 10 : 1,
      })).sort((left, right) => right.score - left.score);
      if (scored[1] && scored[1].score === scored[0]!.score && scored[1].signature !== scored[0]!.signature) {
        throw new Error(`存在多个同等可见控件“${text}”，为防止误点已停止`);
      }
      return scored[0]!;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`等待唯一可见控件“${text}”超时`);
}

export async function submitTmallPagewiseExportAction(options: {
  resolveAction: () => Promise<() => Promise<void>>;
  markSubmitting: (submittedAt: string) => Promise<void>;
  now?: () => string;
}) {
  // Resolve the exact business control before recording an unresolved click. A
  // missing or ambiguous menu item is a retryable preflight failure, not proof
  // that an export task may already have been created.
  const click = await options.resolveAction();
  const submittedAt = (options.now ?? (() => new Date().toISOString()))();
  await options.markSubmitting(submittedAt);
  await click();
  return submittedAt;
}

async function inspectTmallOnSalePagination(page: Page) {
  const regions: string[] = [];
  let totalAnchorCount = 0;
  for (const frame of page.frames()) {
    const anchors = frame.getByText(/共\s*\d{1,7}\s*件商品/, { exact: true });
    const count = Math.min(await anchors.count().catch(() => 0), 20);
    totalAnchorCount += count;
    for (let index = 0; index < count; index += 1) {
      const region = await anchors.nth(index).evaluate((element) => {
        const view = element.ownerDocument.defaultView;
        if (!view) return null;
        let current: Element | null = element;
        for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
          const rect = current.getBoundingClientRect();
          const style = view.getComputedStyle(current);
          if (rect.width < 2 || rect.height < 2 || style.display === "none" || style.visibility === "hidden"
            || Number(style.opacity || "1") <= 0) continue;
          const text = (current as HTMLElement).innerText?.replace(/\s+/g, " ").trim() ?? "";
          if (text.length > 2_000 || !/共\s*\d{1,7}\s*件商品/.test(text)
            || !/(?:^|\D)\d{1,4}\s*\/\s*\d{1,4}(?:\D|$)/.test(text)) continue;
          return text;
        }
        return null;
      }).catch(() => null);
      if (region) regions.push(region);
    }
  }
  const observed = regions.map((region) => parseTmallOnSalePagination(region))
    .filter((value): value is PagewisePagination => value !== null);
  const uniqueObserved = [...new Map(observed.map((value) => [
    `${value.totalProducts}:${value.currentPage}:${value.totalPages}`,
    value,
  ])).values()];
  return {
    pagination: chooseTmallOnSalePaginationRegions(regions),
    diagnostic: {
      checkedAt: new Date().toISOString(),
      totalAnchorCount,
      scopedRegionCount: regions.length,
      parsedRegionCount: observed.length,
      uniquePaginationCount: uniqueObserved.length,
      observed: uniqueObserved.slice(0, 5),
    } satisfies PagewisePaginationDiagnostic,
  };
}

async function waitForStableTmallOnSalePagination(options: {
  page: Page;
  expectedPage: number;
  expectedTotalProducts?: number;
  expectedTotalPages?: number;
  timeoutMs?: number;
}) {
  const deadline = Date.now() + (options.timeoutMs ?? 30_000);
  let stableKey = "";
  let stableReads = 0;
  let lastDiagnostic: PagewisePaginationDiagnostic = {
    checkedAt: new Date().toISOString(), totalAnchorCount: 0, scopedRegionCount: 0,
    parsedRegionCount: 0, uniquePaginationCount: 0, observed: [],
  };
  while (Date.now() < deadline) {
    const inspected = await inspectTmallOnSalePagination(options.page);
    lastDiagnostic = inspected.diagnostic;
    const pagination = inspected.pagination;
    const matches = pagination?.currentPage === options.expectedPage
      && (options.expectedTotalProducts === undefined || pagination.totalProducts === options.expectedTotalProducts)
      && (options.expectedTotalPages === undefined || pagination.totalPages === options.expectedTotalPages);
    if (matches && pagination) {
      const key = `${pagination.totalProducts}:${pagination.currentPage}:${pagination.totalPages}`;
      stableReads = key === stableKey ? stableReads + 1 : 1;
      stableKey = key;
      if (stableReads >= 2) return { pagination, diagnostic: lastDiagnostic };
    } else {
      stableKey = "";
      stableReads = 0;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  const observed = lastDiagnostic.observed.map((item) => (
    `${item.totalProducts}:${item.currentPage}/${item.totalPages}`
  )).join(",") || "none";
  const error = new Error(
    `出售中列表未稳定读回第 ${options.expectedPage} 页`
    + `（总数锚点 ${lastDiagnostic.totalAnchorCount}，分页区域 ${lastDiagnostic.scopedRegionCount}，`
    + `有效区域 ${lastDiagnostic.parsedRegionCount}，唯一分页 ${lastDiagnostic.uniquePaginationCount}，观测 ${observed}）`,
  ) as Error & { paginationDiagnostic?: PagewisePaginationDiagnostic };
  error.paginationDiagnostic = lastDiagnostic;
  throw error;
}

async function selectCurrentPageProducts(page: Page, expectedCount: number) {
  const titleCandidates = await exactTextCandidates(page, "商品标题");
  if (titleCandidates.length === 0) throw new Error("未找到出售中列表的“商品标题”表头");
  const selector = 'input[type="checkbox"],[role="checkbox"],label[class*="checkbox" i],span[class*="checkbox" i]';
  const checkboxCandidates: Array<PositionedCandidate & { score: number }> = [];
  for (const title of titleCandidates) {
    const matches = title.frame.locator(selector);
    const count = Math.min(await matches.count().catch(() => 0), 200);
    for (let index = 0; index < count; index += 1) {
      const detail = await positionedCandidate(title.frame, matches.nth(index));
      if (!detail) continue;
      const centerY = detail.top + detail.height / 2;
      const titleCenterY = title.top + title.height / 2;
      if (detail.left > title.left || Math.abs(centerY - titleCenterY) > 48 || detail.top > 450) continue;
      const score = 100 - Math.abs(centerY - titleCenterY) - Math.min(60, Math.abs(title.left - detail.left) / 4);
      checkboxCandidates.push({ ...detail, score });
    }
  }
  const chosen = chooseTmallOnSaleHeaderCheckbox(checkboxCandidates);
  if (!chosen) {
    const uniqueCheckboxes = new Set(checkboxCandidates.map((candidate) => candidate.signature)).size;
    throw new Error(`无法唯一定位出售中列表当前页全选框（表头 ${titleCandidates.length}，候选 ${uniqueCheckboxes}）`);
  }
  const checkbox = checkboxCandidates.find((candidate) => candidate.signature === chosen.signature);
  if (!checkbox) throw new Error("出售中列表全选框候选在点击前失效");
  await checkbox.locator.click({ timeout: 15_000 });
  await waitUntil(15_000, async () => {
    const text = (await combinedPageText(page)).replace(/\s+/g, "");
    return text.includes(`已选${expectedCount}`);
  }, `当前页全选后未精确读回“已选 ${expectedCount}”`);
}

async function dismissExportSuccessDialog(page: Page) {
  await clickUniqueExactText(page, "取消");
  await waitUntil(10_000, async () => !(await combinedPageText(page)).includes(TMALL_PAGEWISE_EXPORT_SUCCESS), "关闭导出成功弹窗超时");
}

async function clickNextPage(page: Page, expectedPage: number) {
  const selectors = [
    'button[aria-label*="下一页"]', '[role="button"][aria-label*="下一页"]',
    '.next-pagination-item.next-next:not(.disabled)', '.next-pagination-item.next-next:not([aria-disabled="true"])',
    '[class*="pagination"] [class*="next"]:not([class*="disabled"])',
  ];
  const candidates: Array<PositionedCandidate & { score: number }> = [];
  for (const frame of page.frames()) {
    for (const selector of selectors) {
      const matches = frame.locator(selector);
      const count = Math.min(await matches.count().catch(() => 0), 10);
      for (let index = 0; index < count; index += 1) {
        const detail = await positionedCandidate(frame, matches.nth(index));
        if (!detail || detail.left <= 600 || detail.top >= 500) continue;
        const actionable = ["button", "a"].includes(detail.tag) || ["button", "link"].includes(detail.role) ? 1_000 : 0;
        const explicitlyNext = /下一页|next-next/i.test(detail.attributes) ? 500 : 0;
        const compactControl = Math.max(0, 100 - Math.min(100, detail.width * detail.height / 100));
        const score = actionable + explicitlyNext + compactControl + Math.min(200, detail.left / 10);
        candidates.push({ ...detail, score });
      }
    }
  }
  const chosen = chooseTmallOnSaleNextPageCandidate(candidates);
  const uniqueCount = new Set(candidates.map((candidate) => candidate.signature)).size;
  if (!chosen) throw new Error(`无法唯一定位第 ${expectedPage - 1} 页右上角“下一页”控件（候选 ${uniqueCount}）`);
  const next = candidates.find((candidate) => candidate.signature === chosen.signature);
  if (!next) throw new Error(`第 ${expectedPage - 1} 页“下一页”控件在点击前失效`);
  await next.locator.click({ timeout: 15_000 });
  await waitForStableTmallOnSalePagination({ page, expectedPage });
}

async function openRecordPageFromLastDialog(page: Page) {
  const context = page.context();
  const before = new Set(context.pages());
  await clickUniqueExactText(page, "前往下载");
  let recordPage: Page | undefined;
  await waitUntil(30_000, async () => {
    const pages = context.pages();
    for (const candidate of pages) {
      if (candidate.isClosed()) continue;
      const text = await combinedPageText(candidate);
      if (text.includes("导出记录") || (text.includes("任务创建时间") && text.includes("下载"))) {
        recordPage = candidate;
        return true;
      }
    }
    recordPage = pages.find((candidate) => !before.has(candidate) && !candidate.isClosed());
    return Boolean(recordPage && !recordPage.url().startsWith("about:blank"));
  }, "点击“前往下载”后未出现导出记录页");
  return recordPage!;
}

function safeRecordPageUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "https:" || !/(?:taobao|tmall)\.com$/i.test(url.hostname)) {
    throw new Error("导出记录页 URL 不属于受控天猫域名");
  }
  return url.toString();
}

async function waitForAllExportRecords(page: Page, tasks: readonly PagewiseTask[]) {
  const deadline = Date.now() + recordWaitTimeoutMs;
  let nextRefreshAt = Date.now() + recordRefreshIntervalMs;
  while (Date.now() < deadline) {
    const scan = await exportRecordDownloadCandidates(page);
    const matched = chooseTmallPagewiseExportRecords(scan.candidates, tasks);
    if (matched) {
      const records = matched.map((choice) => scan.candidates.find((candidate) => candidate.signature === choice.signature));
      if (records.every((record): record is ExportRecordDownloadCandidate => Boolean(record?.locator))) return records;
    }
    if (Date.now() >= nextRefreshAt) {
      for (const recordPage of scan.recordPages) {
        if (!recordPage.isClosed()) await recordPage.reload({ waitUntil: "domcontentloaded", timeout: 30_000 }).catch(() => undefined);
      }
      nextRefreshAt = Date.now() + recordRefreshIntervalMs;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`导出记录等待十分钟后仍未出现本轮全部 ${tasks.length} 条已完成任务`);
}

async function withDeadline<T>(promise: Promise<T>, timeoutMs: number, message: string) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    })]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function downloadRecordFile(options: {
  record: ExportRecordDownloadCandidate;
  downloadDirectory: string;
  targetPath: string;
}) {
  if (!options.record.locator) throw new Error(`导出记录 ${options.record.taskCreatedAt} 缺少唯一下载控件`);
  await mkdir(options.downloadDirectory, { recursive: true });
  const stagingDirectory = await mkdtemp(path.join(options.downloadDirectory, ".tmall-pagewise-master-"));
  if (!inside(options.downloadDirectory, stagingDirectory)) throw new Error("逐页下载暂存目录越过店铺独立目录");
  const session = await createTmallBrowserDownloadSession(options.record.recordPage);
  let activeGuid: string | undefined;
  let resolveStarted!: (value: { guid: string; suggestedFilename: string }) => void;
  let resolveCompleted!: (value: { ok: true; guid: string; filePath?: string } | { ok: false; guid: string; error: string }) => void;
  const started = new Promise<{ guid: string; suggestedFilename: string }>((resolve) => { resolveStarted = resolve; });
  const completed = new Promise<{ ok: true; guid: string; filePath?: string } | { ok: false; guid: string; error: string }>(
    (resolve) => { resolveCompleted = resolve; },
  );
  session.on("Browser.downloadWillBegin", (event) => {
    if (activeGuid) return;
    activeGuid = event.guid;
    resolveStarted({ guid: event.guid, suggestedFilename: event.suggestedFilename });
  });
  session.on("Browser.downloadProgress", (event) => {
    const outcome = tmallBrowserDownloadOutcome(event, activeGuid);
    if (outcome) resolveCompleted(outcome);
  });
  try {
    await session.send("Browser.setDownloadBehavior", { behavior: "allowAndName", downloadPath: stagingDirectory, eventsEnabled: true });
    await options.record.locator.click({ timeout: 15_000 });
    const start = await withDeadline(started, 60_000, `点击导出记录 ${options.record.taskCreatedAt} 后 Chrome 未开始下载`);
    if (!isTmallProductWorkbookFilename(start.suggestedFilename)) throw new Error("逐页导出返回的文件不是安全 .xlsx");
    const finish = await withDeadline(completed, 120_000, `导出记录 ${options.record.taskCreatedAt} 下载未在两分钟内完成`);
    if (!finish.ok) throw new Error(finish.error);
    const stagedPath = await resolveTmallStagedDownloadPath({
      stagingDirectory, guid: finish.guid, suggestedFilename: start.suggestedFilename, reportedFilePath: finish.filePath,
    });
    if (await stat(options.targetPath).then(() => true).catch(() => false)) throw new Error("逐页导出规范文件已存在，拒绝覆盖");
    await rename(stagedPath, options.targetPath);
  } finally {
    await session.send("Browser.setDownloadBehavior", { behavior: "default" }).catch(() => undefined);
    await session.detach().catch(() => undefined);
    if (inside(options.downloadDirectory, stagingDirectory)) {
      await rm(stagingDirectory, { recursive: true, force: true }).catch(() => undefined);
    }
  }
}

export async function mergeTmallPagewiseProductWorkbooks(options: {
  sourceFiles: readonly string[];
  sourceEvidence?: readonly PagewiseFileEvidence[];
  targetPath: string;
  store: Pick<TmallStore, "shopName" | "browser">;
  snapshotDate: string;
  expectedProductCount: number;
}) {
  if (options.sourceFiles.length < 1 || options.sourceFiles.length > maximumPages) throw new Error("逐页货品文件数量无效");
  const rows: Array<Record<string, unknown>> = [];
  const productIds = new Set<string>();
  if (options.sourceEvidence && options.sourceEvidence.length !== options.sourceFiles.length) {
    throw new Error("逐页货品文件与活动清单证据数量不一致");
  }
  for (const [index, filePath] of options.sourceFiles.entries()) {
    const resolved = path.resolve(filePath);
    if (!inside(options.store.browser.downloadDir, resolved)) throw new Error("逐页货品文件越过当前店铺独立下载目录");
    const bytes = new Uint8Array(await readFile(resolved));
    const recorded = options.sourceEvidence?.[index];
    if (recorded) {
      if (recorded.page !== index + 1 || path.resolve(recorded.filePath) !== resolved || path.basename(resolved) !== recorded.fileName
        || recorded.fileSizeBytes !== bytes.byteLength
        || recorded.sha256 !== createHash("sha256").update(bytes).digest("hex")) {
        throw new Error(`逐页货品文件 ${path.basename(resolved)} 与活动清单证据不一致`);
      }
    }
    const inspection = await inspectTmallImportBytes({
      source: "tmall_product_master", bytes, fileName: path.basename(resolved), fileSizeBytes: bytes.byteLength,
      platform: "天猫", shopName: options.store.shopName, snapshotDate: options.snapshotDate,
    });
    if (inspection.errors.length > 0 || inspection.totals.rowCount <= 0) {
      throw new Error(`逐页货品文件 ${path.basename(resolved)} 校验失败`);
    }
    if (recorded && (recorded.rowCount !== inspection.totals.rowCount
      || recorded.uniqueProductCount !== inspection.totals.uniqueProductCount
      || recorded.uniqueSkuCount !== inspection.totals.uniqueSkuCount)) {
      throw new Error(`逐页货品文件 ${path.basename(resolved)} 回读结果与活动清单证据不一致`);
    }
    for (const row of inspection.rows) {
      const raw = row.raw as Record<string, unknown>;
      const productId = String(raw["商品ID"] ?? "").trim();
      if (!productId) throw new Error("逐页货品文件存在缺失商品ID的业务行");
      productIds.add(productId);
      rows.push(raw);
    }
  }
  if (productIds.size !== options.expectedProductCount) {
    throw new Error(`逐页货品文件合计 ${productIds.size} 个唯一商品，与出售中总数 ${options.expectedProductCount} 不一致`);
  }
  const matrix = [
    [...exportedMasterHeaders],
    ...rows.map((row) => canonicalMasterHeaders.map((header) => row[header] ?? null)),
  ];
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(matrix), "发布模板");
  const bytes = new Uint8Array(XLSX.write(workbook, { type: "array", bookType: "xlsx", compression: true }));
  const targetPath = path.resolve(options.targetPath);
  if (!inside(options.store.browser.downloadDir, targetPath) || !/\.xlsx$/i.test(targetPath)) {
    throw new Error("合并货品文件必须位于当前店铺独立下载目录");
  }
  const expectedSha256 = createHash("sha256").update(bytes).digest("hex");
  const targetExists = await stat(targetPath).then(() => true).catch(() => false);
  if (!targetExists) await writeFile(targetPath, bytes);
  const evidence = await inspectTmallMasterFile(targetPath, options.store, options.snapshotDate);
  if (evidence.uniqueProductCount !== options.expectedProductCount || evidence.rowCount !== rows.length) {
    throw new Error("合并货品文件回读后的商品数或业务行数不一致");
  }
  if (evidence.sha256 !== expectedSha256) {
    throw new Error("已有合并货品文件与当前分页内容不一致，拒绝续接或覆盖");
  }
  return evidence;
}

async function submitRemainingPages(options: {
  page: Page;
  audit: PagewiseAudit;
  auditRoot: string;
  onAudit: (audit: PagewiseAudit) => void;
}) {
  let activeAudit = options.audit;
  const persist = async (patch: Partial<PagewiseAudit>) => {
    activeAudit = await writeActiveAudit({ ...activeAudit, ...patch }, options.auditRoot);
    options.onAudit(activeAudit);
  };
  const startPage = activeAudit.tasks.length + 1;
  const target = new URL(TMALL_SELLER_ON_SALE_URL);
  target.searchParams.set("current", String(startPage));
  await options.page.goto(target.toString(), { waitUntil: "domcontentloaded", timeout: 60_000 });
  await ensureTmallSellerSession(options.page, await getTmallStore(activeAudit.storeKey));
  await dismissImportantNotice(options.page);
  let initial;
  try {
    initial = await waitForStableTmallOnSalePagination({
      page: options.page,
      expectedPage: startPage,
      expectedTotalProducts: activeAudit.totalProducts,
      expectedTotalPages: activeAudit.totalPages,
    });
  } catch (error) {
    const paginationDiagnostic = (error as Error & { paginationDiagnostic?: PagewisePaginationDiagnostic }).paginationDiagnostic;
    await persist({ stage: "browser_ready", paginationDiagnostic });
    throw error;
  }
  const initialPagination = initial.pagination;
  if (activeAudit.totalProducts !== undefined && activeAudit.totalProducts !== initialPagination.totalProducts
    || activeAudit.totalPages !== undefined && activeAudit.totalPages !== initialPagination.totalPages) {
    throw new Error("恢复时出售中商品总数或页数变化，拒绝把不同快照混入同一任务清单");
  }
  await persist({
    stage: "browser_ready",
    totalProducts: initialPagination.totalProducts,
    totalPages: initialPagination.totalPages,
    currentPage: startPage,
    paginationDiagnostic: initial.diagnostic,
  });
  const totalProducts = activeAudit.totalProducts ?? initialPagination.totalProducts;
  const totalPages = activeAudit.totalPages ?? initialPagination.totalPages;
  for (let pageNumber = startPage; pageNumber <= totalPages; pageNumber += 1) {
    const current = await waitForStableTmallOnSalePagination({
      page: options.page, expectedPage: pageNumber, expectedTotalProducts: totalProducts, expectedTotalPages: totalPages,
    });
    await persist({ currentPage: pageNumber, paginationDiagnostic: current.diagnostic });
    const itemCount = expectedTmallPageItemCount(totalProducts, totalPages, pageNumber);
    await selectCurrentPageProducts(options.page, itemCount);
    await clickUniqueExactText(options.page, "更多批量操作");
    const submittedAt = await submitTmallPagewiseExportAction({
      resolveAction: async () => {
        const exportMenu = await waitForUniqueExactTextCandidate(options.page, TMALL_PAGEWISE_EXPORT_MENU);
        return () => exportMenu.locator.click({ timeout: 15_000 });
      },
      markSubmitting: async () => persist({ stage: "page_export_submitting", currentPage: pageNumber }),
    });
    await waitUntil(30_000, async () => (await combinedPageText(options.page)).includes(TMALL_PAGEWISE_EXPORT_SUCCESS),
      `第 ${pageNumber} 页未出现导出任务创建成功确认`);
    const task: PagewiseTask = { page: pageNumber, itemCount, submittedAt };
    await persist({ stage: "page_export_submitted", currentPage: pageNumber, tasks: [...activeAudit.tasks, task] });
    if (pageNumber < totalPages) {
      await dismissExportSuccessDialog(options.page);
      await clickNextPage(options.page, pageNumber + 1);
    } else {
      await persist({ stage: "all_tasks_submitted", currentPage: pageNumber });
      const recordPage = await openRecordPageFromLastDialog(options.page);
      await persist({ recordPageUrl: safeRecordPageUrl(recordPage.url()) });
    }
  }
  return activeAudit;
}

async function browserPagewiseExport(options: {
  store: TmallStore;
  audit: PagewiseAudit;
  auditRoot: string;
  onAudit: (audit: PagewiseAudit) => void;
}) {
  await launchStoreChrome(options.store);
  const { connectPlaywrightBrowser } = await import("../lib/jackyun/playwright-client");
  const browser = await connectPlaywrightBrowser(options.store.browser.debugPort);
  const context = browser.contexts()[0];
  if (!context) throw new Error(`${options.store.shopName} 独立 Chromium 没有可用上下文`);
  let page = context.pages().find((candidate) => candidate.url().includes("myseller.taobao.com")) ?? await context.newPage();
  page.setDefaultTimeout(15_000);
  try {
    let activeAudit = options.audit;
    if (activeAudit.tasks.length < (activeAudit.totalPages ?? Number.POSITIVE_INFINITY)) {
      activeAudit = await submitRemainingPages({ page, audit: activeAudit, auditRoot: options.auditRoot, onAudit: options.onAudit });
    }
    if (activeAudit.stage === "all_tasks_submitted" || activeAudit.stage === "page_export_submitted" || activeAudit.stage === "downloading") {
      const candidatePage = context.pages().find((candidate) => !candidate.isClosed() && candidate.url() === activeAudit.recordPageUrl);
      if (candidatePage) page = candidatePage;
      else if (activeAudit.recordPageUrl) {
        page = await context.newPage();
        await page.goto(safeRecordPageUrl(activeAudit.recordPageUrl), { waitUntil: "domcontentloaded", timeout: 60_000 });
      } else {
        throw new Error("逐页任务已全部提交但活动清单缺少导出记录页，需人工确认原任务后恢复");
      }
      const records = await waitForAllExportRecords(page, activeAudit.tasks);
      activeAudit = await writeActiveAudit({ ...activeAudit, stage: "downloading" }, options.auditRoot);
      options.onAudit(activeAudit);
      for (let index = activeAudit.files.length; index < records.length; index += 1) {
        const record = records[index]!;
        const task = activeAudit.tasks[index]!;
        const canonicalName = `${safeSegment(options.store.shopName)}-出售中第${task.page}页-${activeAudit.snapshotDate}-${activeAudit.runId}.xlsx`;
        const targetPath = path.resolve(options.store.browser.downloadDir, canonicalName);
        if (!inside(options.store.browser.downloadDir, targetPath)) throw new Error("逐页下载目标越过店铺独立目录");
        const exists = await stat(targetPath).then(() => true).catch(() => false);
        if (!exists) await downloadRecordFile({ record, downloadDirectory: options.store.browser.downloadDir, targetPath });
        const evidence = await inspectTmallMasterFile(targetPath, options.store, activeAudit.snapshotDate);
        if (evidence.uniqueProductCount !== task.itemCount) {
          throw new Error(`第 ${task.page} 页导出包含 ${evidence.uniqueProductCount} 个唯一商品，与选中 ${task.itemCount} 个不一致`);
        }
        const file: PagewiseFileEvidence = { ...evidence, page: task.page, taskCreatedAt: record.taskCreatedAt };
        activeAudit = await writeActiveAudit({ ...activeAudit, files: [...activeAudit.files, file] }, options.auditRoot);
        options.onAudit(activeAudit);
      }
      activeAudit = await writeActiveAudit({ ...activeAudit, stage: "downloaded" }, options.auditRoot);
      options.onAudit(activeAudit);
    }
    return activeAudit;
  } finally {
    await browser.close().catch(() => undefined);
  }
}

export async function runTmallPagewiseProductMasterStage(options: {
  storeKey: string;
  baseUrl?: string;
  request?: typeof fetch;
  snapshotDate?: string;
  auditDirectory?: string;
}): Promise<TmallProductMasterStageResult> {
  const store = await getTmallStore(options.storeKey);
  if (store.productMasterExportMode !== TMALL_PAGEWISE_EXPORT_MODE) {
    throw new Error(`${store.shopName} 未启用出售中逐页 Excel 导出模式`);
  }
  const requestedSnapshotDate = options.snapshotDate ?? shanghaiToday();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(requestedSnapshotDate)) throw new Error("天猫货品快照日期必须是 YYYY-MM-DD");
  const baseUrl = normalizeLocalBaseUrl(options.baseUrl ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000");
  const auditRoot = path.resolve(options.auditDirectory ?? artifactDirectory);
  await mkdir(auditRoot, { recursive: true });
  const legacyActivePath = path.join(legacyArtifactDirectory, `active-${safeSegment(store.storeKey)}.json`);
  if (await stat(legacyActivePath).then(() => true).catch(() => false)) {
    throw new Error("检测到原商品管家 M 节点仍有活动清单；必须先人工核对原任务，不能直接切换为逐页导出");
  }
  const directMtopActivePath = path.join(directMtopArtifactDirectory, `active-${safeSegment(store.storeKey)}.json`);
  if (await stat(directMtopActivePath).then(() => true).catch(() => false)) {
    throw new Error("检测到 MTOP 直连 M 节点仍有活动清单；必须先人工核对原任务，不能切换为逐页导出");
  }
  const existing = await readActiveAudit(store.storeKey, auditRoot);
  let snapshotDate = requestedSnapshotDate;
  let audit = existing?.audit;
  if (audit) {
    if (audit.shopName !== store.shopName) throw new Error("逐页导出活动清单店铺身份不一致");
    const recovery = decideTmallPagewiseAuditRecovery(requestedSnapshotDate, audit);
    if (recovery.action === "block") throw new Error("逐页导出停在任务创建点击未决阶段，必须人工核对导出记录后再处理");
    if (recovery.action === "discard") {
      await rm(existing!.filePath, { force: true });
      audit = undefined;
    } else snapshotDate = recovery.snapshotDate;
  }
  if (!audit) {
    const now = new Date().toISOString();
    audit = await writeActiveAudit({
      version: 1, strategy: TMALL_PAGEWISE_EXPORT_MODE, runId: randomUUID(), storeKey: store.storeKey,
      shopName: store.shopName, snapshotDate, startedAt: now, updatedAt: now, stage: "planned", tasks: [], files: [],
    }, auditRoot);
  }
  let activeAudit = audit;
  try {
    if (activeAudit.stage !== "merged") {
      activeAudit = await browserPagewiseExport({
        store, audit: activeAudit, auditRoot, onAudit: (updated) => { activeAudit = updated; },
      });
    }
    let mergedFile = activeAudit.mergedFile;
    if (!mergedFile) {
      if (!activeAudit.totalProducts || activeAudit.files.length !== activeAudit.totalPages) {
        throw new Error("逐页文件未覆盖全部出售中分页，拒绝合并导入");
      }
      const mergedPath = path.resolve(store.browser.downloadDir,
        `${safeSegment(store.shopName)}-出售中全部商品-${snapshotDate}-${activeAudit.runId}.xlsx`);
      const orderedFiles = [...activeAudit.files].sort((left, right) => left.page - right.page);
      mergedFile = await mergeTmallPagewiseProductWorkbooks({
        sourceFiles: orderedFiles.map((file) => file.filePath), sourceEvidence: orderedFiles,
        targetPath: mergedPath, store, snapshotDate, expectedProductCount: activeAudit.totalProducts,
      });
      activeAudit = await writeActiveAudit({ ...activeAudit, stage: "merged", mergedFile, lastError: undefined }, auditRoot);
    } else {
      const rechecked = await inspectTmallMasterFile(mergedFile.filePath, store, snapshotDate);
      if (rechecked.sha256 !== mergedFile.sha256 || rechecked.rowCount !== mergedFile.rowCount
        || rechecked.uniqueProductCount !== activeAudit.totalProducts) {
        throw new Error("恢复时合并货品文件与活动清单不一致");
      }
      mergedFile = rechecked;
    }
    const imported = await importTmallProductMasterFile({
      baseUrl, store, snapshotDate, evidence: mergedFile, request: options.request,
    });
    activeAudit = await writeActiveAudit({
      ...activeAudit, stage: "completed", lastError: undefined, importResult: {
        status: imported.status, batchId: imported.batchId, rowCount: imported.rowCount, warningCount: imported.warningCount,
      },
    }, auditRoot);
    const finalAuditPath = path.join(auditRoot, `run-${activeAudit.runId}.json`);
    await writeJsonAtomic(finalAuditPath, activeAudit);
    await rm(activeAuditPath(store.storeKey, auditRoot), { force: true });
    return {
      ok: true, stage: "product_master", status: imported.status, storeKey: store.storeKey, shopName: store.shopName,
      snapshotDate, batchId: imported.batchId, rowCount: imported.rowCount, warningCount: imported.warningCount,
      auditPath: finalAuditPath, filePath: mergedFile.filePath,
    };
  } catch (error) {
    const lastError = safeError(error);
    const failedAudit = await writeActiveAudit({ ...activeAudit, lastError }, auditRoot).catch(() => undefined);
    if (["planned", "browser_ready"].includes(activeAudit.stage)) {
      if (failedAudit) {
        const preflightFailurePath = path.join(auditRoot,
          `preflight-failure-${safeSegment(store.storeKey)}-${snapshotDate}-${safeSegment(activeAudit.runId)}.json`);
        await writeJsonAtomic(preflightFailurePath, failedAudit).catch(() => undefined);
      }
      await rm(activeAuditPath(store.storeKey, auditRoot), { force: true }).catch(() => undefined);
    }
    throw error;
  }
}
