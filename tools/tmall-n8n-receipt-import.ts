import { readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { inspectTmallImportBytes } from "../lib/netshop/normalized-import";
import { getTmallStore } from "../lib/netshop/tmall-store-registry";
import { createTmallDownloadReceipt } from "./tmall-download-receipt";
import { runTmallMultiStoreImport, shanghaiYesterday } from "./tmall-multi-store-import-runner";

type PreliminaryInspection = {
  errors: Array<{ code?: string; message: string }>;
  totals: { dateMin: string | null; dateMax: string | null };
};

function value(argv: string[], name: string) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : undefined;
}

function validDate(date: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(date) && !Number.isNaN(Date.parse(`${date}T00:00:00Z`));
}

function inside(directory: string, filePath: string) {
  const relative = path.relative(directory, filePath);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export function decodeWatchedFilePath(encoded: string) {
  const normalized = encoded.trim();
  if (!normalized || !/^[A-Za-z0-9+/]+={0,2}$/.test(normalized) || normalized.length % 4 !== 0) {
    throw new Error("--file-base64 不是有效的 Base64 路径");
  }
  const bytes = Buffer.from(normalized, "base64");
  if (bytes.length === 0 || bytes.toString("base64") !== normalized) {
    throw new Error("--file-base64 不是规范的 Base64 路径");
  }
  const filePath = bytes.toString("utf8");
  if (!filePath || Buffer.from(filePath, "utf8").compare(bytes) !== 0 || /[\u0000-\u001f\u007f]/.test(filePath)) {
    throw new Error("文件路径编码无效");
  }
  return filePath;
}

export function businessDateFromInspection(inspection: PreliminaryInspection) {
  const errors = inspection.errors.filter((issue) => issue.code !== "MISSING_EXPECTED_DATE_RANGE");
  if (errors.length > 0) throw new Error(errors.map((issue) => issue.message).join("；"));
  const { dateMin, dateMax } = inspection.totals;
  if (!dateMin || !dateMax || dateMin !== dateMax || !validDate(dateMin)) {
    throw new Error("生意参谋文件必须且只能覆盖一个有效业务日期");
  }
  return dateMin;
}

export function validateWatchedBusinessDate(businessDate: string, initialStartDate: string | null, latestDate = shanghaiYesterday()) {
  if (!validDate(businessDate) || (initialStartDate && businessDate < initialStartDate) || businessDate > latestDate) {
    throw new Error(`业务日期必须位于店铺注册起始日至昨天之间: ${businessDate}`);
  }
  return businessDate;
}

export async function receiptAndImportWatchedFile(input: {
  storeKey: string;
  encodedFilePath: string;
  baseUrl: string;
}) {
  const store = await getTmallStore(input.storeKey);
  if (!store.enabled) throw new Error(`店铺未启用: ${input.storeKey}`);

  const watchedPath = decodeWatchedFilePath(input.encodedFilePath);
  if (!path.isAbsolute(watchedPath)) throw new Error("n8n 文件事件必须提供绝对路径");
  const [downloadDirectory, filePath] = await Promise.all([
    realpath(store.browser.downloadDir),
    realpath(path.resolve(watchedPath)),
  ]);
  if (!inside(downloadDirectory, filePath)) throw new Error("文件不在该店铺独立下载目录内");
  if (!/\.xls$/i.test(filePath)) throw new Error("仅处理生意参谋商品日 .xls 文件");
  const info = await stat(filePath);
  if (!info.isFile() || info.size <= 0) throw new Error("下载文件不存在或为空");

  const bytes = new Uint8Array(await readFile(filePath));
  const preliminary = await inspectTmallImportBytes({
    source: "tmall_product_daily",
    bytes,
    fileName: path.basename(filePath),
    fileSizeBytes: bytes.byteLength,
    shopName: store.shopName,
  });
  const businessDate = validateWatchedBusinessDate(
    businessDateFromInspection(preliminary),
    store.initialStartDate,
  );
  const { receiptPath } = await createTmallDownloadReceipt({
    storeKey: store.storeKey,
    businessDate,
    filePath,
  });

  const result = await runTmallMultiStoreImport({
    baseUrl: input.baseUrl.replace(/\/$/, ""),
    storeKey: store.storeKey,
    endDate: businessDate,
    dates: [businessDate],
    receiptPaths: [receiptPath],
    dryRun: false,
  });
  const item = result.audit.items.find((candidate) =>
    candidate.storeKey === store.storeKey && candidate.businessDate === businessDate
  );
  if (!item || !["imported", "duplicate", "completed_with_warnings"].includes(item.status)) {
    throw new Error(item?.error ?? "签收文件未完成导入与覆盖回查");
  }
  return {
    ok: true,
    storeKey: store.storeKey,
    shopName: store.shopName,
    businessDate,
    fileName: path.basename(filePath),
    receiptPath,
    auditPath: result.auditPath,
    status: item.status,
    batchId: item.batchId ?? null,
    rowCount: item.rowCount ?? 0,
    warningCount: item.warningCount ?? 0,
  };
}

async function main() {
  const argv = process.argv.slice(2);
  const storeKey = value(argv, "--store-key");
  const encodedFilePath = value(argv, "--file-base64");
  if (!storeKey || !encodedFilePath) {
    throw new Error("用法: --store-key <key> --file-base64 <base64-path> [--base-url <url>]");
  }
  const baseUrl = value(argv, "--base-url") ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000";
  const result = await receiptAndImportWatchedFile({ storeKey, encodedFilePath, baseUrl });
  console.log(JSON.stringify(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
