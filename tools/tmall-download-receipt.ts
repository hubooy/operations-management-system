import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { writeJsonAtomic } from "../lib/jackyun/json-file";
import { inspectTmallImportBytes } from "../lib/netshop/normalized-import";
import { getTmallStore } from "../lib/netshop/tmall-store-registry";

export type TmallDownloadReceipt = {
  version: 1;
  storeKey: string;
  shopName: string;
  businessDate: string;
  fileName: string;
  sha256: string;
  size: number;
  downloadedAt: string;
};

function value(argv: string[], name: string) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : undefined;
}

function validDate(date: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(date) && !Number.isNaN(Date.parse(`${date}T00:00:00Z`));
}

function withinDirectory(directory: string, filePath: string) {
  const relative = path.relative(directory, filePath);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export function receiptPathForWorkbook(filePath: string) {
  return `${filePath}.tmall-receipt.json`;
}

export async function createTmallDownloadReceipt(input: { storeKey: string; businessDate: string; filePath: string }) {
  if (!validDate(input.businessDate)) throw new Error("--date 必须是有效的 YYYY-MM-DD 日期");
  const store = await getTmallStore(input.storeKey);
  const filePath = path.resolve(input.filePath);
  if (!withinDirectory(store.browser.downloadDir, filePath)) throw new Error("下载文件必须位于该店铺的独立 downloadDir 内");
  if (!/\.xls$/i.test(filePath)) throw new Error("生意参谋商品日数据必须是 .xls 文件");
  const fileInfo = await stat(filePath);
  if (!fileInfo.isFile() || fileInfo.size <= 0) throw new Error("下载文件不存在或为空");
  if (fileInfo.size > 25 * 1024 * 1024) throw new Error("下载文件超过天猫导入 25MB 上限");
  const bytes = new Uint8Array(await readFile(filePath));
  const inspected = await inspectTmallImportBytes({
    source: "tmall_product_daily",
    bytes,
    fileName: path.basename(filePath),
    fileSizeBytes: bytes.byteLength,
    shopName: store.shopName,
    expectedStartDate: input.businessDate,
    expectedEndDate: input.businessDate,
  });
  if (inspected.errors.length) throw new Error(`下载文件日期或格式校验失败: ${inspected.errors.map((issue) => issue.message).join("；")}`);
  const receipt: TmallDownloadReceipt = {
    version: 1,
    storeKey: store.storeKey,
    shopName: store.shopName,
    businessDate: input.businessDate,
    fileName: path.basename(filePath),
    sha256: createHash("sha256").update(bytes).digest("hex"),
    size: bytes.byteLength,
    downloadedAt: new Date().toISOString(),
  };
  const receiptPath = receiptPathForWorkbook(filePath);
  await writeJsonAtomic(receiptPath, receipt);
  return { receiptPath, receipt };
}

async function main() {
  const argv = process.argv.slice(2);
  const storeKey = value(argv, "--store-key");
  const businessDate = value(argv, "--date");
  const filePath = value(argv, "--file");
  if (!storeKey || !businessDate || !filePath) throw new Error("用法: npm run tmall:receipt -- --store-key <key> --date YYYY-MM-DD --file <path.xls>");
  const result = await createTmallDownloadReceipt({ storeKey, businessDate, filePath });
  console.log(JSON.stringify({ ok: true, receiptPath: result.receiptPath, storeKey, businessDate }, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
