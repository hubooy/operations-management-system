import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

test("sales chunk completion claims the session before assembly and fences later chunk writes", async () => {
  const [route, service] = await Promise.all([
    readFile(path.resolve("app/api/imports/sales/chunks/route.ts"), "utf8"),
    readFile(path.resolve("lib/sales/chunked-upload.ts"), "utf8"),
  ]);
  const claimIndex = route.indexOf("await claimSalesUpload(principal, uploadId)");
  const assembleIndex = route.indexOf("await assembleSalesUpload(principal, claim.session)");
  const readStart = service.indexOf("async function readSalesUpload");
  const readEnd = service.indexOf("export async function beginSalesUpload", readStart);
  const readSalesUpload = service.slice(readStart, readEnd);
  assert.ok(claimIndex >= 0 && assembleIndex > claimIndex);
  assert.ok(readStart >= 0 && readEnd > readStart);
  assert.match(readSalesUpload, /service:\s*"writer"/);
  assert.doesNotMatch(readSalesUpload, /service:\s*"reader"/);
  assert.match(service, /reconciled\.chunks/);
  assert.match(service, /adopted\?\.sha256 === checksum/);
  assert.match(service, /contentBase64: bytesToBase64\(input\.bytes\)/);
  assert.match(service, /upload\.status === "processing"/);
  assert.match(service, /crypto\.randomUUID\(\)/);
  assert.match(service, /path: SALES_RAW_UPLOAD_CHUNK_PATH/);
  assert.match(service, /ownerToken: claimed\.ownerToken/);
  assert.match(service, /const part = base64ToBytes\(stored\.contentBase64\)/);
  assert.match(service, /toHex\(await sha256\(part\)\) !== chunk\.sha256/);
  assert.match(service, /reconciled\.resultBatchId === resultBatchId/);
  assert.match(service, /cleanupToken: item\.cleanupToken/);
  assert.doesNotMatch(service, /SALES_IMPORT_FILES|cloudflare:workers|\bbucket\(\)|\bR2\b/);
  assert.match(route, /claim\.kind === "completed"/);
  assert.match(route, /cleanupCompletedSalesUpload/);
});
