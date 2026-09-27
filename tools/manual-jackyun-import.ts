import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { prepareJackyunWorkbook } from "../lib/jackyun/post-download";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const baseUrl = "http://localhost:3000";

type ModuleConfig = {
  module: "products" | "inventory" | "inventory_age" | "combos";
  filePath: string;
  snapshotDate?: string;
};

const configs: ModuleConfig[] = [
  { module: "products", filePath: "D:/谷歌浏览器/货品导出 (1).xlsx" },
  { module: "inventory", filePath: "D:/谷歌浏览器/分仓库存查询 (2).xlsx", snapshotDate: "2026-08-26" },
  { module: "inventory_age", filePath: "D:/谷歌浏览器/库龄分析(正式勿删) (1).xlsx", snapshotDate: "2026-08-26" },
  // 2026-08-27 下载的"组合装导出.xlsx"缺子件关系表（sheet1 仅表头 0 行），
  // 复用 8/26 的"组合装及子件导出.xlsx"（72 小时复用窗口内）
  { module: "combos", filePath: "D:/谷歌浏览器/组合装及子件导出.xlsx" },
];

function sha256(bytes: Uint8Array) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function fetchJson(url: string, init: RequestInit = {}, timeoutMs = 60_000) {
  const response = await fetch(url, { ...init, signal: init.signal ?? AbortSignal.timeout(timeoutMs) });
  const body = await response.json().catch(() => null);
  if (!response.ok || !body || body.ok === false) {
    const msg = typeof body?.message === "string" ? body.message : `HTTP ${response.status}`;
    throw new Error(`${url} -> ${msg}`);
  }
  return body;
}

async function uploadAndImport(config: ModuleConfig) {
  const rawBytes = new Uint8Array(readFileSync(config.filePath));
  const prepared = prepareJackyunWorkbook(config.module, rawBytes, { snapshotDate: config.snapshotDate });
  const importBytes = prepared.importBytes;
  const importHash = sha256(importBytes);
  const isInventory = config.module === "inventory";
  const endpoint = `${baseUrl}/api/imports/${isInventory ? "inventory" : "erp"}/chunks`;
  const source = isInventory ? undefined : config.module;
  const fileName = prepared.importFileName;
  const chunkSize = 1024 * 1024;
  const chunkCount = Math.ceil(importBytes.byteLength / chunkSize);

  console.log(`[${config.module}] 开始导入: ${fileName}`);
  console.log(`  源文件: ${config.filePath} (${rawBytes.byteLength} bytes)`);
  console.log(`  预处理后: ${importBytes.byteLength} bytes, 期望行数: ${prepared.expectedBatchRowCount}`);
  console.log(`  预处理: ${JSON.stringify(prepared.preprocessing)}`);

  // 初始化上传
  const initBody = await fetchJson(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      action: "init",
      ...(source ? { source } : {}),
      fileName,
      fileSizeBytes: importBytes.byteLength,
      chunkCount,
      fingerprint: `${config.module}:${importHash}`,
      ...(config.snapshotDate ? { snapshotDate: config.snapshotDate } : {}),
    }),
  });
  const uploadId = initBody.upload?.id;
  if (!uploadId) throw new Error(`[${config.module}] 初始化上传失败: ${JSON.stringify(initBody)}`);
  console.log(`  上传会话: ${uploadId}, 共 ${chunkCount} 片`);

  // 并行上传分片
  const pending: number[] = [];
  for (let i = 0; i < chunkCount; i++) pending.push(i);
  const concurrency = 3;
  for (let batchStart = 0; batchStart < pending.length; batchStart += concurrency) {
    const batch = pending.slice(batchStart, batchStart + concurrency);
    await Promise.all(batch.map(async (index) => {
      const start = index * chunkSize;
      const end = Math.min(start + chunkSize, importBytes.byteLength);
      const chunk = new Uint8Array(end - start);
      chunk.set(importBytes.subarray(start, end));
      await fetchJson(endpoint, {
        method: "PUT",
        headers: {
          "content-type": "application/octet-stream",
          "x-upload-id": uploadId,
          "x-chunk-index": String(index),
        },
        body: chunk.buffer,
      });
    }));
    process.stdout.write(`\r  已上传 ${Math.min(batchStart + concurrency, pending.length)}/${pending.length} 片`);
  }
  console.log("");

  // 完成上传并触发导入
  console.log(`  正在合并并导入...`);
  const result = await fetchJson(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      action: "complete",
      ...(source ? { source } : {}),
      uploadId,
      ...(config.snapshotDate ? { snapshotDate: config.snapshotDate } : {}),
    }),
  }, 10 * 60_000);

  const batch = result.batch || {};
  console.log(`  导入结果: status=${batch.status || "?"}, rowCount=${batch.rowCount ?? "?"}, id=${(batch.id || "").slice(0, 16)}`);
  if (batch.warnings && batch.warnings.length > 0) {
    for (const w of batch.warnings.slice(0, 5)) {
      console.log(`  警告: [${w.code}] ${w.message}`);
    }
  }
  return { module: config.module, batch };
}

async function main() {
  const results: Array<{ module: string; batch: Record<string, unknown> }> = [];
  for (const config of configs) {
    try {
      const result = await uploadAndImport(config);
      results.push(result);
    } catch (error) {
      console.error(`\n[${config.module}] 导入失败: ${error instanceof Error ? error.message : String(error)}`);
      results.push({ module: config.module, batch: { status: "failed", error: error instanceof Error ? error.message : String(error) } });
    }
  }

  console.log("\n========== 导入汇总 ==========");
  for (const r of results) {
    const b = r.batch;
    console.log(`${r.module}: ${b.status || "?"} | rows=${b.rowCount ?? "?"} | id=${String(b.id ?? "").slice(0, 16)}`);
  }

  // 保存结果摘要
  const outDir = path.join(projectRoot, "outputs", "manual-import-20260807");
  mkdirSync(outDir, { recursive: true });
  writeFileSync(path.join(outDir, "summary.json"), JSON.stringify({ timestamp: new Date().toISOString(), results }, null, 2));
}

main().catch((error) => {
  console.error("致命错误:", error);
  process.exit(1);
});
