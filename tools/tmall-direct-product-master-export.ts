import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import path from "node:path";

import type { APIRequestContext, APIResponse, BrowserContext, Page } from "playwright-core";

import { writeJsonAtomic } from "../lib/jackyun/json-file";
import { connectPlaywrightBrowser } from "../lib/jackyun/playwright-client";
import { inspectTmallImportBytes } from "../lib/netshop/normalized-import";
import { getTmallStore, type TmallStore } from "../lib/netshop/tmall-store-registry";
import { mergeTmallPagewiseProductWorkbooks } from "./tmall-pagewise-product-master-export";
import {
  TMALL_SELLER_ON_SALE_URL,
  ensureTmallSellerSession,
  ensureTmallStoreAuthenticatedSession,
  importTmallProductMasterFile,
  inspectTmallMasterFile,
  tmallAutomationProjectRoot,
  type TmallProductMasterStageResult,
} from "./tmall-product-master-export";
import { assertTmallDirectMasterStore } from "./tmall-yijiu-direct-pm-contract";
import { reportIsolatedHelperPhase } from "./tmall-isolated-helper";

export const TMALL_MTOP_URL = "https://h5api.m.taobao.com/h5/mtop.tmall.sell.pc.manage.async/1.0/";
export const TMALL_MTOP_API = "mtop.tmall.sell.pc.manage.async";
export const TMALL_MTOP_APP_KEY = "12574478";
export const TMALL_MTOP_LIST_PATH = "/tmall/manager/table.htm";
// This is intentionally the only write-like MTOP path in this module. The
// sibling operations under batchFastEdit can publish, unpublish or delete
// products, so this value must never become configuration or an argument.
export const TMALL_MTOP_EXPORT_PATH = "/tmall/manager/batchFastEdit.htm?optType=batchExportItem&action=submit";
export const TMALL_EXPORT_RECORDS_URL = "https://sell.publish.tmall.com/excel/manager/table.htm?tab=export";
export const TMALL_DIRECT_PRODUCT_MASTER_PROTOCOL = "tmall_mtop_batch_export_v1" as const;
export const TMALL_MTOP_PAGE_SIZE = 20;
export const TMALL_MTOP_MAX_PAGES = 100;

const defaultAuditDirectory = path.join(tmallAutomationProjectRoot, "outputs", "tmall-direct-product-master-export");
const pagewiseAuditDirectory = path.join(tmallAutomationProjectRoot, "outputs", "tmall-pagewise-product-master-export");
const productManagerAuditDirectory = path.join(tmallAutomationProjectRoot, "outputs", "tmall-product-master-export");
const maximumWorkbookBytes = 25 * 1024 * 1024;
const exportPollIntervalMs = 8_000;
const exportOverallTimeoutMs = 15 * 60_000;

type JsonRecord = Record<string, unknown>;
export type TmallMtopListTemplate = {
  tab: string;
  filtertab: string;
  filter: JsonRecord;
  table: JsonRecord;
};
export type TmallDirectProductItem = { itemId: string; catId: string | number };
export type TmallDirectProductBatch = {
  index: number;
  items: TmallDirectProductItem[];
  itemDigest: string;
};
export type TmallExportRecord = {
  id: string;
  rowCount: number;
  status: string;
  createdAt: string;
  reportUrl?: string;
};

type BatchFileEvidence = Awaited<ReturnType<typeof inspectTmallMasterFile>> & {
  productIds: string[];
};

type DirectBatchStage = "planned" | "submitting" | "submitted" | "downloading" | "downloaded";
type DirectBatchAudit = {
  index: number;
  itemIds: string[];
  itemDigest: string;
  expectedRows: number;
  stage: DirectBatchStage;
  baselineRecordIds?: string[];
  submittedAt?: string;
  traceId?: string;
  recordId?: string;
  file?: BatchFileEvidence;
};

type DirectMasterStage =
  | "planned"
  | "list_ready"
  | "batch_submitting"
  | "batch_submitted"
  | "downloading"
  | "downloaded"
  | "merged"
  | "importing"
  | "completed"
  | "failed";

type DirectMasterAudit = {
  version: 1;
  protocol: typeof TMALL_DIRECT_PRODUCT_MASTER_PROTOCOL;
  runId: string;
  storeKey: string;
  shopName: string;
  baseUrl: string;
  snapshotDate: string;
  startedAt: string;
  updatedAt: string;
  stage: DirectMasterStage;
  resumeStage?: Exclude<DirectMasterStage, "completed" | "failed">;
  listTemplateDigest?: string;
  itemDigest?: string;
  totalProducts?: number;
  batches: DirectBatchAudit[];
  mergedFile?: Awaited<ReturnType<typeof inspectTmallMasterFile>>;
  importResult?: { status: "imported" | "duplicate"; batchId: string; rowCount: number; warningCount: number };
  error?: string;
};

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}

function safeSegment(value: string) {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/\s+/g, "-").slice(0, 80);
}

function activeAuditPath(storeKey: string, directory: string) {
  return path.join(directory, `active-${safeSegment(storeKey)}.json`);
}

function digest(value: unknown) {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function normalizeLocalBaseUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname)) {
    throw new Error("天猫 MTOP 货品导入只允许连接本机运营系统");
  }
  return url.toString().replace(/\/$/, "");
}

function shanghaiToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((entry) => entry.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function isPlainJsonRecord(value: unknown) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function parseTmallMtopListRequest(input: { url: string; postData: string | null }): TmallMtopListTemplate | null {
  try {
    const url = new URL(input.url);
    if (url.protocol !== "https:" || url.hostname !== "h5api.m.taobao.com"
      || url.pathname !== "/h5/mtop.tmall.sell.pc.manage.async/1.0/"
      || url.searchParams.get("api") !== TMALL_MTOP_API || !input.postData) return null;
    const form = new URLSearchParams(input.postData);
    const rawOuter = form.get("data");
    if (!rawOuter || rawOuter.length > 100_000) return null;
    const outer = asRecord(JSON.parse(rawOuter));
    if (outer.url !== TMALL_MTOP_LIST_PATH || typeof outer.jsonBody !== "string") return null;
    const body = asRecord(JSON.parse(outer.jsonBody));
    const pagination = asRecord(body.pagination);
    if (Number(pagination.current) !== 1 || Number(pagination.pageSize) !== TMALL_MTOP_PAGE_SIZE
      || typeof body.tab !== "string" || body.tab.length > 64
      || typeof body.filtertab !== "string" || body.filtertab.length > 64
      || !isPlainJsonRecord(body.filter) || !isPlainJsonRecord(body.table)) return null;
    const filter = structuredClone(asRecord(body.filter));
    const table = structuredClone(asRecord(body.table));
    if (JSON.stringify({ filter, table }).length > 50_000) return null;
    return { tab: body.tab, filtertab: body.filtertab, filter, table };
  } catch {
    return null;
  }
}

export function buildTmallMtopListBody(template: TmallMtopListTemplate, current: number) {
  if (!Number.isInteger(current) || current < 1 || current > TMALL_MTOP_MAX_PAGES) {
    throw new Error("天猫 MTOP 商品页码无效");
  }
  return {
    tab: template.tab,
    pagination: { current, pageSize: TMALL_MTOP_PAGE_SIZE },
    filtertab: template.filtertab,
    filter: structuredClone(template.filter),
    table: structuredClone(template.table),
  };
}

export function buildTmallMtopSign(token: string, timestamp: string, data: string) {
  return createHash("md5").update(`${token}&${timestamp}&${TMALL_MTOP_APP_KEY}&${data}`).digest("hex");
}

function normalizeItem(value: unknown): TmallDirectProductItem {
  const row = asRecord(value);
  const itemId = String(row.itemId ?? "").trim();
  const catId = row.catId;
  if (!/^\d{1,32}$/.test(itemId) || !(typeof catId === "string" || typeof catId === "number")
    || !String(catId).trim() || String(catId).length > 64) {
    throw new Error("天猫 MTOP 商品列表存在无效 itemId/catId");
  }
  return { itemId, catId };
}

export function makeTmallDirectProductBatches(items: readonly TmallDirectProductItem[]) {
  if (items.length < 1 || items.length > TMALL_MTOP_PAGE_SIZE * TMALL_MTOP_MAX_PAGES) {
    throw new Error("天猫出售中商品数量无效");
  }
  const ordered = [...items].sort((left, right) => left.itemId.localeCompare(right.itemId, "en"));
  if (new Set(ordered.map((item) => item.itemId)).size !== ordered.length) {
    throw new Error("天猫 MTOP 商品列表存在重复 itemId");
  }
  const batches: TmallDirectProductBatch[] = [];
  for (let offset = 0; offset < ordered.length; offset += TMALL_MTOP_PAGE_SIZE) {
    const batchItems = ordered.slice(offset, offset + TMALL_MTOP_PAGE_SIZE);
    batches.push({ index: batches.length + 1, items: batchItems, itemDigest: digest(batchItems) });
  }
  return batches;
}

function recordFromValue(value: unknown): TmallExportRecord | null {
  const row = asRecord(value);
  const id = String(row.id ?? "").trim();
  const rowCount = Number(row.rowCount ?? row.totalRows ?? row.totalRow ?? row.totalCount ?? row.recordCount
    ?? row.rowNum ?? row.totalNum ?? row.successTotal);
  const status = String(row.taskStatus ?? row.status ?? row.statusDesc ?? row.state ?? row.statusName ?? "").trim();
  const createdAt = String(row.gmtCreate ?? row.createdAt ?? row.createTime ?? "").trim();
  const reportUrl = [row.reportUrl, row.downloadUrl, row.fileUrl]
    .find((candidate) => typeof candidate === "string" && candidate.trim()) as string | undefined;
  if (!id || !Number.isInteger(rowCount) || rowCount < 0 || !status || !createdAt) return null;
  return { id, rowCount, status, createdAt, ...(reportUrl ? { reportUrl } : {}) };
}

export function extractTmallExportRecords(value: unknown) {
  const candidates: TmallExportRecord[][] = [];
  const recordContainerKeys = new Set(["dataSource", "records", "recordList", "list", "rows"]);
  const walk = (current: unknown, parentKey?: string) => {
    if (Array.isArray(current)) {
      if (current.length === 0 && parentKey && recordContainerKeys.has(parentKey)) {
        candidates.push([]);
      } else if (current.length > 0) {
        const records = current.map(recordFromValue);
        if (records.every((record) => record !== null)) candidates.push(records as TmallExportRecord[]);
      }
      for (const item of current) walk(item);
      return;
    }
    if (current && typeof current === "object") {
      for (const [key, child] of Object.entries(current as JsonRecord)) walk(child, key);
    }
  };
  walk(value);
  const unique = new Map(candidates.map((records) => [JSON.stringify(records), records]));
  if (unique.size !== 1) throw new Error(unique.size === 0
    ? "天猫导出记录响应中没有唯一记录数组"
    : "天猫导出记录响应中出现多个不同记录数组");
  return [...unique.values()][0]!;
}

export function resolveTmallDirectProductMasterSnapshotDate(
  requestedDate: string | undefined,
  activeDate: string | undefined,
  today = shanghaiToday(),
) {
  for (const value of [requestedDate, activeDate, today]) {
    if (value !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      throw new Error("天猫货品快照日期必须是 YYYY-MM-DD");
    }
  }
  if (requestedDate && activeDate && requestedDate !== activeDate) {
    throw new Error(`存在未完成的 MTOP 货品清单 ${activeDate}，拒绝覆盖为 ${requestedDate}`);
  }
  return activeDate ?? requestedDate ?? today;
}

function parseShanghaiTimestamp(value: string) {
  const conventional = value.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)$/);
  const parsed = conventional
    ? Date.parse(`${conventional[1]}T${conventional[2]!.length === 5 ? `${conventional[2]}:00` : conventional[2]}+08:00`)
    : Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function recordFailed(status: string) {
  return /FAIL|ERROR|CANCEL|失败|取消/i.test(status);
}

function recordCompleted(status: string) {
  return /已完成|成功|SUCCESS|FINISH|DONE/i.test(status);
}

export function selectNewTmallExportRecord(input: {
  records: readonly TmallExportRecord[];
  baselineRecordIds: readonly string[];
  expectedRows: number;
  submittedAt: string;
  now?: Date;
}) {
  const baseline = new Set(input.baselineRecordIds);
  const fresh = input.records.filter((record) => !baseline.has(record.id));
  if (fresh.length > 1) throw new Error("提交后出现多个新天猫导出记录，无法唯一认领本批任务");
  const record = fresh[0];
  if (!record) return null;
  if (record.rowCount !== input.expectedRows) {
    throw new Error(`天猫导出记录 ${record.id} 行数 ${record.rowCount} 与本批 ${input.expectedRows} 不一致`);
  }
  const createdAt = parseShanghaiTimestamp(record.createdAt);
  const submittedAt = Date.parse(input.submittedAt);
  const now = (input.now ?? new Date()).getTime();
  if (createdAt === null || !Number.isFinite(submittedAt)
    || createdAt < submittedAt - 2 * 60_000 || createdAt > now + 5 * 60_000) {
    throw new Error(`天猫导出记录 ${record.id} 创建时间不在本批安全时间窗`);
  }
  if (recordFailed(record.status)) throw new Error(`天猫导出记录 ${record.id} 已失败（${record.status}）`);
  return record;
}

export function assertTmallProductDownloadUrl(value: unknown) {
  if (typeof value !== "string") throw new Error("天猫商品导出记录缺少下载链接");
  const url = new URL(value);
  if (url.protocol !== "https:" || !/^excel-tmall-item\.oss-[a-z0-9-]+\.aliyuncs\.com$/i.test(url.hostname)
    || url.username || url.password) {
    throw new Error("天猫商品导出链接不在受控 HTTPS OSS 域名");
  }
  return url.toString();
}

export function parseTmallExportSubmitResult(value: unknown) {
  const result = asRecord(value);
  if (result.success !== true) throw new Error("天猫 MTOP 商品批量导出任务未明确返回 success=true");
  const traceId = String(result.traceId ?? asRecord(result.data).traceId ?? "").trim();
  if (!/^[A-Za-z0-9._:-]{1,256}$/.test(traceId)) throw new Error("天猫 MTOP 商品批量导出未返回安全 traceId");
  return { traceId };
}

async function readApiJson(response: APIResponse, label: string) {
  const bytes = await response.body();
  if (!response.ok() || bytes.byteLength === 0) {
    throw new Error(`${label}失败（HTTP ${response.status()}，响应为空或状态异常）`);
  }
  try {
    return JSON.parse(bytes.toString("utf8")) as unknown;
  } catch {
    throw new Error(`${label}未返回 JSON`);
  }
}

class TmallMtopClient {
  constructor(private readonly context: BrowserContext, private readonly api: APIRequestContext = context.request) {}

  private async token() {
    const cookies = await this.context.cookies("https://h5api.m.taobao.com");
    const raw = cookies.find((cookie) => cookie.name === "_m_h5_tk")?.value ?? "";
    return raw.split("_")[0] ?? "";
  }

  private async rawCall(data: string, label: string) {
    const token = await this.token();
    const timestamp = String(Date.now());
    const url = new URL(TMALL_MTOP_URL);
    url.searchParams.set("jsv", "2.7.4");
    url.searchParams.set("appKey", TMALL_MTOP_APP_KEY);
    url.searchParams.set("t", timestamp);
    url.searchParams.set("sign", buildTmallMtopSign(token, timestamp, data));
    url.searchParams.set("api", TMALL_MTOP_API);
    url.searchParams.set("v", "1.0");
    url.searchParams.set("type", "originaljson");
    url.searchParams.set("dataType", "json");
    const response = await this.api.post(url.toString(), {
      form: { data },
      headers: {
        accept: "application/json, text/plain, */*",
        origin: "https://myseller.taobao.com",
        referer: "https://myseller.taobao.com/",
      },
      timeout: 60_000,
    });
    return readApiJson(response, label);
  }

  async call(dataObject: JsonRecord, label: string) {
    const data = JSON.stringify(dataObject);
    let response = await this.rawCall(data, label);
    let ret = Array.isArray(asRecord(response).ret) ? (asRecord(response).ret as unknown[]).map(String) : [];
    const initialTokenFailure = ret.some((entry) => /TOKEN_EXOIRED|TOKEN_EMPTY/i.test(entry));
    if (initialTokenFailure) {
      response = await this.rawCall(data, `${label}（换 token 后单次重试）`);
      ret = Array.isArray(asRecord(response).ret) ? (asRecord(response).ret as unknown[]).map(String) : [];
    }
    const head = ret[0] ?? "";
    if (!head.startsWith("SUCCESS")) {
      const message = ret.join(" ");
      if (/SESSION_EXPIRED|NEED_LOGIN|ILLEGAL_ACCESS|TOKEN_/i.test(message)) {
        throw new Error(`waiting_login：天猫 MTOP 登录态无效（${message.slice(0, 180)}）`);
      }
      if (/RGV587|FLOW_LIMIT|风控|限流/i.test(message)) {
        throw new Error(`天猫 MTOP 触发风控或限流（${message.slice(0, 180)}）`);
      }
      throw new Error(`${label}业务失败（${message.slice(0, 180) || "缺少 ret"}）`);
    }
    const result = asRecord(asRecord(response).data).result;
    if (typeof result === "string") {
      try {
        return asRecord(JSON.parse(result));
      } catch {
        throw new Error(`${label}的 data.result 不是 JSON`);
      }
    }
    return asRecord(result);
  }
}

async function captureListTemplate(page: Page, store: TmallStore) {
  reportIsolatedHelperPhase("master_template_capture");
  const requestPromise = page.waitForRequest((request) => parseTmallMtopListRequest({
    url: request.url(),
    postData: request.postData(),
  }) !== null, { timeout: 60_000 });
  reportIsolatedHelperPhase("master_template_navigation");
  await page.goto(TMALL_SELLER_ON_SALE_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
  reportIsolatedHelperPhase("master_template_identity");
  await ensureTmallSellerSession(page, store);
  reportIsolatedHelperPhase("master_template_wait");
  const request = await requestPromise;
  const template = parseTmallMtopListRequest({ url: request.url(), postData: request.postData() });
  if (!template) throw new Error("未捕获千牛出售中页面的 MTOP 列表请求模板");
  return template;
}

async function listAllItems(client: TmallMtopClient, template: TmallMtopListTemplate) {
  const items: TmallDirectProductItem[] = [];
  let expectedTotal: number | null = null;
  for (let current = 1; current <= TMALL_MTOP_MAX_PAGES; current += 1) {
    const result = await client.call({
      url: TMALL_MTOP_LIST_PATH,
      jsonBody: JSON.stringify(buildTmallMtopListBody(template, current)),
    }, `读取天猫出售中商品第 ${current} 页`);
    const data = asRecord(result.data);
    const pagination = asRecord(data.pagination);
    const total = Number(pagination.total);
    const pageSize = Number(pagination.pageSize);
    const responseCurrent = Number(pagination.current ?? current);
    const rows = asRecord(data.table).dataSource;
    if (!Number.isInteger(total) || total <= 0 || pageSize !== TMALL_MTOP_PAGE_SIZE
      || responseCurrent !== current || !Array.isArray(rows)) {
      throw new Error("天猫 MTOP 商品分页回显的 total/current/pageSize/table 无效");
    }
    if (expectedTotal === null) expectedTotal = total;
    if (total !== expectedTotal) throw new Error("天猫出售中商品总数在同一轮分页期间发生变化");
    const expectedRows = Math.min(TMALL_MTOP_PAGE_SIZE, total - items.length);
    if (rows.length !== expectedRows) {
      throw new Error(`天猫 MTOP 第 ${current} 页返回 ${rows.length} 行，期望 ${expectedRows} 行`);
    }
    items.push(...rows.map(normalizeItem));
    if (items.length === total) break;
    await new Promise((resolve) => setTimeout(resolve, 1_200));
  }
  if (expectedTotal === null || items.length !== expectedTotal) {
    throw new Error(`天猫 MTOP 分页只取得 ${items.length} 个商品，未覆盖回显总数 ${expectedTotal ?? "未知"}`);
  }
  if (new Set(items.map((item) => item.itemId)).size !== items.length) {
    throw new Error("天猫 MTOP 跨页商品存在重复 itemId");
  }
  return { items, total: expectedTotal };
}

async function fetchExportRecords(api: APIRequestContext) {
  const response = await api.get(TMALL_EXPORT_RECORDS_URL, {
    headers: {
      accept: "application/json, text/plain, */*",
      referer: "https://sell.publish.tmall.com/excel/manager/render.htm?tab=export",
    },
    timeout: 60_000,
  });
  return extractTmallExportRecords(await readApiJson(response, "读取天猫商品导出记录"));
}

async function submitBatch(client: TmallMtopClient, batch: TmallDirectProductBatch) {
  const itemIds = batch.items.map((item) => item.itemId);
  const payload = {
    itemId: itemIds,
    jsonBody: {
      auctionids: itemIds,
      rowRecord: batch.items.map((item) => ({ catId: item.catId, itemId: item.itemId })),
    },
  };
  const result = await client.call({
    url: TMALL_MTOP_EXPORT_PATH,
    jsonBody: JSON.stringify(payload),
  }, `提交天猫商品导出第 ${batch.index} 批`);
  return parseTmallExportSubmitResult(result);
}

async function waitForNewRecord(input: {
  api: APIRequestContext;
  audit: DirectBatchAudit;
  deadline: number;
}) {
  if (!input.audit.baselineRecordIds || !input.audit.submittedAt) {
    throw new Error("天猫商品导出批次缺少提交前记录基线或提交时间");
  }
  while (Date.now() < input.deadline) {
    const records = await fetchExportRecords(input.api);
    const record = selectNewTmallExportRecord({
      records,
      baselineRecordIds: input.audit.baselineRecordIds,
      expectedRows: input.audit.expectedRows,
      submittedAt: input.audit.submittedAt,
    });
    if (record) {
      if (recordCompleted(record.status)) {
        assertTmallProductDownloadUrl(record.reportUrl);
        return record;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, exportPollIntervalMs));
  }
  throw new Error(`天猫商品导出第 ${input.audit.index} 批在总计 15 分钟等待窗内未完成`);
}

async function reacquireRecord(api: APIRequestContext, batch: DirectBatchAudit) {
  if (!batch.recordId) throw new Error("天猫商品导出下载阶段缺少绑定的 recordId");
  const records = await fetchExportRecords(api);
  const matching = records.filter((record) => record.id === batch.recordId);
  if (matching.length !== 1) throw new Error(`无法从导出记录唯一恢复 recordId=${batch.recordId}`);
  const record = matching[0]!;
  if (record.rowCount !== batch.expectedRows || !recordCompleted(record.status) || recordFailed(record.status)) {
    throw new Error(`恢复的天猫导出记录 ${record.id} 状态或行数不一致`);
  }
  assertTmallProductDownloadUrl(record.reportUrl);
  return record;
}

async function boundedDownload(url: string, request: typeof fetch = fetch) {
  const response = await request(url, { redirect: "follow", signal: AbortSignal.timeout(180_000) });
  if (!response.ok || !response.body) throw new Error(`天猫商品 XLSX 下载失败（HTTP ${response.status}）`);
  const declared = Number(response.headers.get("content-length") ?? 0);
  if (Number.isFinite(declared) && declared > maximumWorkbookBytes) throw new Error("天猫商品 XLSX 超过 25MB 上限");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const part = await reader.read();
    if (part.done) break;
    total += part.value.byteLength;
    if (total > maximumWorkbookBytes) {
      await reader.cancel();
      throw new Error("天猫商品 XLSX 超过 25MB 上限");
    }
    chunks.push(part.value);
  }
  const bytes = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
  if (bytes.byteLength < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
    throw new Error("天猫商品导出文件缺少 XLSX ZIP 魔数");
  }
  return bytes;
}

async function inspectBatchFile(input: {
  filePath: string;
  store: TmallStore;
  snapshotDate: string;
  expectedItemIds: readonly string[];
}) {
  const evidence = await inspectTmallMasterFile(input.filePath, input.store, input.snapshotDate);
  const bytes = new Uint8Array(await readFile(evidence.filePath));
  const inspection = await inspectTmallImportBytes({
    source: "tmall_product_master",
    bytes,
    fileName: evidence.fileName,
    fileSizeBytes: bytes.byteLength,
    platform: "天猫",
    shopName: input.store.shopName,
    snapshotDate: input.snapshotDate,
  });
  if (inspection.errors.length > 0) throw new Error("天猫分批商品 XLSX 二次解析失败");
  const productIds = inspection.rows.map((row) => String((row.raw as JsonRecord)["商品ID"] ?? "").trim());
  const expected = [...input.expectedItemIds].sort((left, right) => left.localeCompare(right, "en"));
  const actual = [...new Set(productIds)].sort((left, right) => left.localeCompare(right, "en"));
  if (productIds.some((itemId) => !itemId) || evidence.uniqueProductCount !== expected.length
    || JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error("天猫分批商品 XLSX 的商品 ID 集合与提交批次不一致");
  }
  return { ...evidence, productIds: actual };
}

export async function assertNoLegacyMasterAction(
  store: Pick<TmallStore, "storeKey">,
  directories: readonly string[] = [pagewiseAuditDirectory, productManagerAuditDirectory],
) {
  for (const directory of directories) {
    const filePath = activeAuditPath(store.storeKey, directory);
    if (await stat(filePath).then(() => true).catch((error: NodeJS.ErrnoException) => {
      if (error.code === "ENOENT") return false;
      throw error;
    })) {
      throw new Error(`检测到原 M 节点活动清单 ${path.basename(directory)}，拒绝由 MTOP 直连候选接管`);
    }
  }
}

async function readAudit(store: TmallStore, directory: string) {
  const filePath = activeAuditPath(store.storeKey, directory);
  try {
    const audit = JSON.parse(await readFile(filePath, "utf8")) as DirectMasterAudit;
    if (audit.version !== 1 || audit.protocol !== TMALL_DIRECT_PRODUCT_MASTER_PROTOCOL
      || audit.storeKey !== store.storeKey || audit.shopName !== store.shopName || !audit.runId
      || !Array.isArray(audit.batches) || !audit.stage) {
      throw new Error("天猫 MTOP 货品活动清单结构或店铺身份无效");
    }
    return audit;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

async function persistAudit(audit: DirectMasterAudit, directory: string) {
  audit.updatedAt = new Date().toISOString();
  await mkdir(directory, { recursive: true });
  await writeJsonAtomic(activeAuditPath(audit.storeKey, directory), audit);
}

function batchesMatchAudit(batches: readonly TmallDirectProductBatch[], audit: DirectMasterAudit) {
  return audit.batches.length === batches.length && audit.batches.every((saved, index) => {
    const current = batches[index];
    return current?.index === saved.index && current.itemDigest === saved.itemDigest
      && JSON.stringify(current.items.map((item) => item.itemId)) === JSON.stringify(saved.itemIds);
  });
}

export async function runTmallDirectProductMasterStage(options: {
  storeKey: string;
  baseUrl?: string;
  request?: typeof fetch;
  downloadRequest?: typeof fetch;
  snapshotDate?: string;
  auditDirectory?: string;
}): Promise<TmallProductMasterStageResult> {
  reportIsolatedHelperPhase("master_start");
  const store = await getTmallStore(options.storeKey);
  assertTmallDirectMasterStore(store.storeKey);
  const baseUrl = normalizeLocalBaseUrl(options.baseUrl ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000");
  const auditDirectory = path.resolve(options.auditDirectory ?? defaultAuditDirectory);
  await assertNoLegacyMasterAction(store);
  await mkdir(auditDirectory, { recursive: true });
  let audit = await readAudit(store, auditDirectory);
  const snapshotDate = resolveTmallDirectProductMasterSnapshotDate(
    options.snapshotDate,
    audit?.snapshotDate,
  );
  const now = new Date().toISOString();
  audit ??= {
    version: 1,
    protocol: TMALL_DIRECT_PRODUCT_MASTER_PROTOCOL,
    runId: randomUUID(),
    storeKey: store.storeKey,
    shopName: store.shopName,
    baseUrl,
    snapshotDate,
    startedAt: now,
    updatedAt: now,
    stage: "planned",
    batches: [],
  };
  if (audit.baseUrl !== baseUrl) throw new Error("天猫 MTOP 货品活动清单的运营系统地址不一致");
  await persistAudit(audit, auditDirectory);
  reportIsolatedHelperPhase("master_audit_saved");
  reportIsolatedHelperPhase("master_authentication");
  await ensureTmallStoreAuthenticatedSession(store.storeKey);
  reportIsolatedHelperPhase("master_browser_connect");
  const browser = await connectPlaywrightBrowser(store.browser.debugPort);
  reportIsolatedHelperPhase("master_browser_connected");
  try {
    const context = browser.contexts()[0];
    if (!context) throw new Error(`${store.shopName} 独立 Chromium 没有可用上下文`);
    const pages = context.pages();
    const page = pages.find((candidate) => /myseller\.taobao\.com/i.test(candidate.url())) ?? await context.newPage();
    page.setDefaultTimeout(15_000);
    const template = await captureListTemplate(page, store);
    const client = new TmallMtopClient(context);
    reportIsolatedHelperPhase("master_list_read");
    const listed = await listAllItems(client, template);
    reportIsolatedHelperPhase("master_list_ready");
    const batches = makeTmallDirectProductBatches(listed.items);
    const templateDigest = digest(template);
    const itemDigest = digest(batches.map((batch) => batch.itemDigest));
    if (audit.listTemplateDigest && audit.listTemplateDigest !== templateDigest) {
      throw new Error("千牛出售中页面的 MTOP 查询模板在恢复期间发生变化");
    }
    if (audit.itemDigest && (audit.itemDigest !== itemDigest || audit.totalProducts !== listed.total
      || !batchesMatchAudit(batches, audit))) {
      throw new Error("天猫出售中商品集合在 MTOP 导出恢复期间发生变化，拒绝继续补批");
    }
    if (!audit.itemDigest) {
      audit.listTemplateDigest = templateDigest;
      audit.itemDigest = itemDigest;
      audit.totalProducts = listed.total;
      audit.batches = batches.map((batch) => ({
        index: batch.index,
        itemIds: batch.items.map((item) => item.itemId),
        itemDigest: batch.itemDigest,
        expectedRows: batch.items.length,
        stage: "planned",
      }));
      audit.stage = "list_ready";
      await persistAudit(audit, auditDirectory);
    }
    if (audit.batches.some((batch) => batch.stage === "submitting")) {
      throw new Error("MTOP 商品导出停在提交响应未决阶段，必须人工核对导出记录，禁止自动重提");
    }
    const pollDeadline = Date.now() + exportOverallTimeoutMs;
    for (const [index, batch] of batches.entries()) {
      const saved = audit.batches[index]!;
      if (saved.stage === "downloaded" && saved.file) {
        const checked = await inspectBatchFile({
          filePath: saved.file.filePath,
          store,
          snapshotDate,
          expectedItemIds: saved.itemIds,
        });
        if (checked.sha256 !== saved.file.sha256 || checked.rowCount !== saved.file.rowCount) {
          throw new Error(`恢复时第 ${saved.index} 批商品文件与活动清单证据不一致`);
        }
        continue;
      }
      if (saved.stage === "planned") {
        const baseline = await fetchExportRecords(context.request);
        if (new Set(baseline.map((record) => record.id)).size !== baseline.length) {
          throw new Error("天猫商品导出记录基线存在重复 id");
        }
        saved.baselineRecordIds = baseline.map((record) => record.id);
        saved.submittedAt = new Date().toISOString();
        saved.stage = "submitting";
        audit.stage = "batch_submitting";
        await persistAudit(audit, auditDirectory);
        const submitted = await submitBatch(client, batch);
        saved.traceId = submitted.traceId;
        saved.stage = "submitted";
        audit.stage = "batch_submitted";
        await persistAudit(audit, auditDirectory);
      }
      const targetPath = path.resolve(store.browser.downloadDir,
        `${safeSegment(store.shopName)}-出售中-MTOP-${snapshotDate}-${safeSegment(audit.runId)}-batch-${saved.index}.xlsx`);
      if (saved.stage === "downloading" && await stat(targetPath).then(() => true).catch(() => false)) {
        saved.file = await inspectBatchFile({
          filePath: targetPath,
          store,
          snapshotDate,
          expectedItemIds: saved.itemIds,
        });
        saved.stage = "downloaded";
        audit.stage = audit.batches.every((candidate) => candidate.stage === "downloaded") ? "downloaded" : "batch_submitted";
        await persistAudit(audit, auditDirectory);
        continue;
      }
      let record: TmallExportRecord;
      if (saved.stage === "submitted") {
        record = await waitForNewRecord({ api: context.request, audit: saved, deadline: pollDeadline });
        saved.recordId = record.id;
        saved.stage = "downloading";
        audit.stage = "downloading";
        await persistAudit(audit, auditDirectory);
      } else if (saved.stage === "downloading") {
        record = await reacquireRecord(context.request, saved);
      } else {
        throw new Error(`MTOP 商品导出第 ${saved.index} 批阶段 ${saved.stage} 无法安全恢复`);
      }
      const exists = await stat(targetPath).then(() => true).catch(() => false);
      if (!exists) {
        const bytes = await boundedDownload(assertTmallProductDownloadUrl(record.reportUrl), options.downloadRequest);
        await writeFile(targetPath, bytes, { flag: "wx" });
      }
      saved.file = await inspectBatchFile({
        filePath: targetPath,
        store,
        snapshotDate,
        expectedItemIds: saved.itemIds,
      });
      saved.stage = "downloaded";
      audit.stage = audit.batches.every((candidate) => candidate.stage === "downloaded") ? "downloaded" : "batch_submitted";
      await persistAudit(audit, auditDirectory);
      await new Promise((resolve) => setTimeout(resolve, 1_200));
    }
    if (!audit.totalProducts || audit.batches.some((batch) => batch.stage !== "downloaded" || !batch.file)) {
      throw new Error("MTOP 商品导出没有完整覆盖全部批次，拒绝合并导入");
    }
    let mergedFile = audit.mergedFile;
    if (!mergedFile) {
      const mergedPath = path.resolve(store.browser.downloadDir,
        `${safeSegment(store.shopName)}-出售中全部商品-MTOP-${snapshotDate}-${safeSegment(audit.runId)}.xlsx`);
      mergedFile = await mergeTmallPagewiseProductWorkbooks({
        sourceFiles: audit.batches.map((batch) => batch.file!.filePath),
        targetPath: mergedPath,
        store,
        snapshotDate,
        expectedProductCount: audit.totalProducts,
      });
      audit.mergedFile = mergedFile;
      audit.stage = "merged";
      await persistAudit(audit, auditDirectory);
    } else {
      const checked = await inspectTmallMasterFile(mergedFile.filePath, store, snapshotDate);
      if (checked.sha256 !== mergedFile.sha256 || checked.uniqueProductCount !== audit.totalProducts) {
        throw new Error("MTOP 恢复时合并货品文件与活动清单证据不一致");
      }
      mergedFile = checked;
    }
    audit.stage = "importing";
    await persistAudit(audit, auditDirectory);
    const imported = await importTmallProductMasterFile({
      baseUrl,
      store,
      snapshotDate,
      evidence: mergedFile,
      request: options.request,
    });
    audit.importResult = imported;
    audit.stage = "completed";
    delete audit.resumeStage;
    delete audit.error;
    await persistAudit(audit, auditDirectory);
    const finalAuditPath = path.join(auditDirectory, `run-${safeSegment(audit.runId)}.json`);
    await rename(activeAuditPath(store.storeKey, auditDirectory), finalAuditPath);
    return {
      ok: true,
      stage: "product_master",
      status: imported.status,
      storeKey: store.storeKey,
      shopName: store.shopName,
      snapshotDate,
      batchId: imported.batchId,
      rowCount: imported.rowCount,
      warningCount: imported.warningCount,
      auditPath: finalAuditPath,
      filePath: mergedFile.filePath,
    };
  } catch (error) {
    const current = audit.stage;
    audit.resumeStage = current === "failed" || current === "completed" ? audit.resumeStage : current;
    audit.stage = "failed";
    audit.error = (error instanceof Error ? error.message : String(error)).replace(/[\r\n]+/g, " ").slice(0, 500);
    await persistAudit(audit, auditDirectory).catch(() => undefined);
    throw error;
  } finally {
    await browser.close().catch(() => undefined);
  }
}
