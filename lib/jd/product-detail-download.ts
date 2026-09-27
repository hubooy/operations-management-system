import { readFile, readdir, rename, stat } from "node:fs/promises";
import path from "node:path";
import { parseXlsxFirstSheet } from "../imports/xlsx";
import { assertJdNaturalDateRange, isJdNaturalDate, jdNaturalDatesInRange } from "./date-range";

export const JD_PRODUCT_DETAIL_REUSE_WINDOW_MS = 60 * 60 * 1000;
export type JdProductDetailDimension = "SKU" | "SPU";
export type JdProductDetailDownloadResult = { filePath: string; reused: boolean; downloadClicks: number };
export type JdProductDetailWorkbookSummary = { rowCount: number; columnCount: number; dateMin: string; dateMax: string; dates: string[] };
type FileEvidence = { filePath: string; mtimeMs: number; size: number };
type DateRange = { startDate?: string; endDate?: string };
type WaitOptions = { downloadDirectory: string; expectedPrefix: string; minMtimeMs: number; timeoutMs: number; pollIntervalMs?: number; now?: () => number; sleep?: (ms: number) => Promise<void>; dimension?: JdProductDetailDimension } & DateRange;
type GuardOptions = { downloadDirectory: string; expectedPrefix: string; triggerDownload: () => Promise<void>; reuseWindowMs?: number; initialWaitMs?: number; partialGraceMs?: number; pollIntervalMs?: number; maxRetries?: number; now?: () => number; sleep?: (ms: number) => Promise<void>; dimension?: JdProductDetailDimension } & DateRange;
const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function matchesCompletedFile(name: string, prefix: string) { return name.startsWith(prefix) && name.toLowerCase().endsWith(".xlsx"); }
function matchesPartialFile(name: string, prefix: string) { return name.startsWith(prefix) && name.toLowerCase().endsWith(".crdownload"); }
function hasDimensionFileName(name: string, dimension: JdProductDetailDimension) { return dimension === "SPU" ? /(?:^|[_-])SPU(?:[_-]|\.|$)/i.test(name) : !/(?:^|[_-])SPU(?:[_-]|\.|$)/i.test(name); }

function asDate(value: unknown, date1904: boolean) {
  if (typeof value === "number" && Number.isFinite(value)) {
    const offset = date1904 ? 24_107 : 25_569;
    return new Date((value - offset) * 86_400_000).toISOString().slice(0, 10);
  }
  const text = String(value ?? "").trim();
  const matched = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/.exec(text);
  if (!matched) return null;
  const result = `${matched[1]}-${matched[2].padStart(2, "0")}-${matched[3].padStart(2, "0")}`;
  return isJdNaturalDate(result) ? result : null;
}

function assertDimensionHeaders(allHeaders: string[], dimension: JdProductDetailDimension) {
  const expectedHeader = ["时间", dimension, `${dimension}名称`];
  const header = allHeaders.slice(0, 3);
  if (header.length !== 3 || header.some((value, index) => value !== expectedHeader[index])) {
    throw new Error(`JD workbook header mismatch: expected ${expectedHeader.join("/")}, got ${header.join("/") || "empty"}.`);
  }
  const identifierHeaders = new Set(allHeaders.map((value) => value.replace(/[\s_-]+/g, "").toUpperCase()));
  const hasSkuIdentifier = identifierHeaders.has("SKU") || identifierHeaders.has("SKUID");
  const hasSpuIdentifier = identifierHeaders.has("SPU") || identifierHeaders.has("SPUID");
  if (hasSkuIdentifier && hasSpuIdentifier) {
    throw new Error("JD workbook contains both SKU and SPU identifier columns; refusing ambiguous dimension.");
  }
}

export function validateJdProductDetailWorkbook(bytes: Uint8Array, dimension: JdProductDetailDimension, range: Required<DateRange>): JdProductDetailWorkbookSummary {
  assertJdNaturalDateRange(range.startDate, range.endDate);
  const sheet = parseXlsxFirstSheet(bytes);
  assertDimensionHeaders(sheet.rows[0]?.cells.map((value) => String(value ?? "").trim()) ?? [], dimension);
  const dates = new Set<string>();
  for (const row of sheet.rows.slice(1)) {
    if (String(row.cells[1] ?? "").trim() === "合计") continue;
    const date = asDate(row.cells[0], sheet.date1904);
    if (!date) throw new Error(`JD workbook has invalid detail date at row ${row.rowNumber}.`);
    if (!String(row.cells[1] ?? "").trim()) throw new Error(`JD workbook has empty ${dimension} at row ${row.rowNumber}.`);
    if (date < range.startDate || date > range.endDate) throw new Error(`JD workbook date ${date} is outside ${range.startDate}..${range.endDate}.`);
    dates.add(date);
  }
  const wanted = jdNaturalDatesInRange(range.startDate, range.endDate);
  const missing = wanted.filter((date) => !dates.has(date));
  if (missing.length) throw new Error(`JD workbook is missing required daily dates: ${missing.join(", ")}.`);
  const sorted = [...dates].sort();
  return { rowCount: sheet.rows.length - 1, columnCount: sheet.maxColumns, dateMin: sorted[0]!, dateMax: sorted.at(-1)!, dates: sorted };
}

export async function assertJdProductDetailWorkbookDimension(filePath: string, dimension: JdProductDetailDimension, range?: Required<DateRange>) {
  const bytes = await readFile(filePath);
  if (range) return validateJdProductDetailWorkbook(bytes, dimension, range);
  const sheet = parseXlsxFirstSheet(bytes);
  assertDimensionHeaders(sheet.rows[0]?.cells.map((value) => String(value ?? "").trim()) ?? [], dimension);
}
async function fileMatchesDimension(filePath: string, dimension: JdProductDetailDimension, range?: Required<DateRange>) { try { await assertJdProductDetailWorkbookDimension(filePath, dimension, range); return true; } catch { return false; } }

async function completedFiles(directory: string, prefix: string, minMtimeMs: number): Promise<FileEvidence[]> {
  const names = await readdir(directory).catch(() => [] as string[]);
  const candidates: FileEvidence[] = [];
  for (const name of names) {
    if (!matchesCompletedFile(name, prefix)) continue;
    const filePath = path.join(directory, name);
    const info = await stat(filePath).catch(() => null);
    const partial = await stat(`${filePath}.crdownload`).catch(() => null);
    if (info?.isFile() && info.size > 0 && info.mtimeMs >= minMtimeMs && !partial) candidates.push({ filePath, mtimeMs: info.mtimeMs, size: info.size });
  }
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return candidates;
}

async function newestCompletedFile(directory: string, prefix: string, minMtimeMs: number, dimension?: JdProductDetailDimension, range?: Required<DateRange>): Promise<FileEvidence | null> {
  for (const candidate of await completedFiles(directory, prefix, minMtimeMs)) {
    if (!dimension || await fileMatchesDimension(candidate.filePath, dimension, range)) return candidate;
  }
  return null;
}

export async function findRecentJdProductDetailDownload(options: { downloadDirectory: string; expectedPrefix: string; maxAgeMs?: number; nowMs?: number; dimension?: JdProductDetailDimension } & DateRange) {
  const candidates = await completedFiles(options.downloadDirectory, options.expectedPrefix, (options.nowMs ?? Date.now()) - (options.maxAgeMs ?? JD_PRODUCT_DETAIL_REUSE_WINDOW_MS));
  for (const candidate of candidates) {
    if (!options.dimension || await fileMatchesDimension(candidate.filePath, options.dimension, options.startDate && options.endDate ? options as Required<DateRange> : undefined)) {
      return candidate.filePath;
    }
  }
  return null;
}

export async function hasActiveJdProductDetailPartial(directory: string, prefix: string, minMtimeMs: number) {
  for (const name of await readdir(directory).catch(() => [] as string[])) {
    if (!matchesPartialFile(name, prefix)) continue;
    const info = await stat(path.join(directory, name)).catch(() => null);
    if (info?.isFile() && info.mtimeMs >= minMtimeMs) return true;
  }
  return false;
}

export async function waitForStableJdProductDetailDownload(options: WaitOptions) {
  const now = options.now ?? Date.now, sleep = options.sleep ?? defaultSleep, interval = options.pollIntervalMs ?? 1_000, deadline = now() + options.timeoutMs;
  let previous: FileEvidence | null = null;
  for (;;) {
    const current = await newestCompletedFile(options.downloadDirectory, options.expectedPrefix, options.minMtimeMs, options.dimension, options.startDate && options.endDate ? options as Required<DateRange> : undefined);
    if (current && previous?.filePath === current.filePath && previous.size === current.size && previous.mtimeMs === current.mtimeMs) return current.filePath;
    previous = current;
    if (now() >= deadline) return null;
    await sleep(Math.min(interval, Math.max(0, deadline - now())));
  }
}

export async function finalizeJdProductDetailDownload(filePath: string, dimension: JdProductDetailDimension | undefined, range?: Required<DateRange>) {
  if (!dimension) return filePath;
  await assertJdProductDetailWorkbookDimension(filePath, dimension, range);
  if (dimension !== "SPU" || hasDimensionFileName(path.basename(filePath), "SPU")) return filePath;
  const parsed = path.parse(filePath), target = path.join(parsed.dir, `${parsed.name}_SPU${parsed.ext}`);
  await rename(filePath, target); // same-directory rename is atomic
  return target;
}

export async function acquireJdProductDetailDownload(options: GuardOptions): Promise<JdProductDetailDownloadResult> {
  const now = options.now ?? Date.now, sleep = options.sleep ?? defaultSleep, interval = options.pollIntervalMs ?? 1_000;
  const range = options.startDate && options.endDate ? { startDate: options.startDate, endDate: options.endDate } : undefined;
  const recent = await findRecentJdProductDetailDownload({ downloadDirectory: options.downloadDirectory, expectedPrefix: options.expectedPrefix, maxAgeMs: options.reuseWindowMs ?? JD_PRODUCT_DETAIL_REUSE_WINDOW_MS, nowMs: now(), dimension: options.dimension, ...range });
  if (recent) return { filePath: await finalizeJdProductDetailDownload(recent, options.dimension, range), reused: true, downloadClicks: 0 };
  // This must precede the first trigger: a restarted process must not duplicate
  // a Chrome download whose generic filename has not been renamed yet.
  const partialLookbackStart = now() - (options.reuseWindowMs ?? JD_PRODUCT_DETAIL_REUSE_WINDOW_MS);
  if (await hasActiveJdProductDetailPartial(options.downloadDirectory, options.expectedPrefix, partialLookbackStart)) {
    const completed = await waitForStableJdProductDetailDownload({ downloadDirectory: options.downloadDirectory, expectedPrefix: options.expectedPrefix, minMtimeMs: partialLookbackStart, timeoutMs: options.partialGraceMs ?? 300_000, pollIntervalMs: interval, now, sleep, dimension: options.dimension, ...range });
    if (completed) return { filePath: await finalizeJdProductDetailDownload(completed, options.dimension, range), reused: false, downloadClicks: 0 };
    throw new Error("Existing JD product-detail .crdownload did not finish; refusing to trigger another download.");
  }
  const startedAt = now() - 2_000, attempts = 1 + (options.maxRetries ?? 1);
  let triggerError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try { await options.triggerDownload(); triggerError = undefined; } catch (error) { triggerError = error; }
    const completed = await waitForStableJdProductDetailDownload({ downloadDirectory: options.downloadDirectory, expectedPrefix: options.expectedPrefix, minMtimeMs: startedAt, timeoutMs: options.initialWaitMs ?? 120_000, pollIntervalMs: interval, now, sleep, dimension: options.dimension, ...range });
    if (completed) return { filePath: await finalizeJdProductDetailDownload(completed, options.dimension, range), reused: false, downloadClicks: attempt + 1 };
    if (await hasActiveJdProductDetailPartial(options.downloadDirectory, options.expectedPrefix, startedAt)) {
      const afterPartial = await waitForStableJdProductDetailDownload({ downloadDirectory: options.downloadDirectory, expectedPrefix: options.expectedPrefix, minMtimeMs: startedAt, timeoutMs: options.partialGraceMs ?? 300_000, pollIntervalMs: interval, now, sleep, dimension: options.dimension, ...range });
      if (afterPartial) return { filePath: await finalizeJdProductDetailDownload(afterPartial, options.dimension, range), reused: false, downloadClicks: attempt + 1 };
      throw new Error("JD product-detail .crdownload remains active; refusing retry.");
    }
  }
  const reason = triggerError instanceof Error ? ` Last click error: ${triggerError.message}` : "";
  throw new Error(`JD product-detail workbook did not arrive within the allowed wait; retry limit reached.${reason}`);
}
