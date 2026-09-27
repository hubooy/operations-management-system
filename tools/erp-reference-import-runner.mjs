import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";

const [source, filePath, snapshotDate] = process.argv.slice(2);
const sources = new Set(["products", "inventory_age", "combos"]);
if (!sources.has(source) || !filePath) {
  throw new Error("Usage: node tools/erp-reference-import-runner.mjs <products|inventory_age|combos> <xlsx-path> [YYYY-MM-DD]");
}
if (source === "inventory_age" && !/^\d{4}-\d{2}-\d{2}$/.test(snapshotDate ?? "")) {
  throw new Error("inventory_age requires a snapshot date in YYYY-MM-DD format.");
}

const baseUrl = process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000";
const bytes = await readFile(filePath);
const chunkSize = 1024 * 1024;
const chunkCount = Math.ceil(bytes.byteLength / chunkSize);
const fingerprint = createHash("sha256").update(bytes).digest("hex");
const endpoint = `${baseUrl}/api/imports/erp/chunks`;

const init = await fetch(endpoint, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    action: "init",
    source,
    ...(source === "inventory_age" ? { snapshotDate } : {}),
    fileName: path.basename(filePath),
    fileSizeBytes: bytes.byteLength,
    chunkCount,
    fingerprint,
  }),
});
const initBody = await init.json();
if (!init.ok || !initBody.ok || !initBody.upload?.id) throw new Error(initBody.message ?? "Unable to initialize ERP import.");

const received = new Set(initBody.upload.receivedChunkIndexes ?? []);
for (let index = 0; index < chunkCount; index += 1) {
  if (received.has(index)) continue;
  const start = index * chunkSize;
  const payload = bytes.subarray(start, Math.min(start + chunkSize, bytes.byteLength));
  const uploaded = await fetch(endpoint, {
    method: "PUT",
    headers: {
      "x-upload-id": initBody.upload.id,
      "x-chunk-index": String(index),
      "content-type": "application/octet-stream",
    },
    body: payload,
  });
  const uploadedBody = await uploaded.json();
  if (!uploaded.ok || !uploadedBody.ok) throw new Error(uploadedBody.message ?? `Failed at chunk ${index}.`);
}

const completed = await fetch(endpoint, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: "complete", source, uploadId: initBody.upload.id, snapshotDate }),
});
const result = await completed.json();
if (!completed.ok || !result.ok) throw new Error(result.message ?? "ERP import rejected.");

const verified = await fetch(`${baseUrl}/api/imports/erp?source=${encodeURIComponent(source)}&limit=1`);
const verification = await verified.json();
if (!verified.ok || !Array.isArray(verification.items)) throw new Error(verification.message ?? "Unable to verify ERP import.");
console.log(JSON.stringify({ fingerprint, result, verification }, null, 2));
