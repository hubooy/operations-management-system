import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { downloadBusinessFile } from "../lib/ai/business-file-download";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
type MutableFixture = { item?: { id: string; manifest: { files: { xlsx: { bytes: number; sha256: string } } } }; base64?: string; attempt?: number; fileSha256?: string; bytes?: number };
function fixture(size = 524301, mutate?: (body: MutableFixture, call: number) => void) {
  const bytes = Buffer.alloc(size);
  for (let i = 0; i < size; i++) bytes[i] = i % 251;
  const digest = hash(bytes), binding = "a".repeat(64);
  let calls = 0;
  const fetcher: typeof fetch = async (input, init) => {
    assert.equal(init?.redirect, "manual");
    assert.equal(init?.cache, "no-store");
    const url = new URL(String(input), "https://example.invalid");
    const sequence = Number(url.searchParams.get("sequence"));
    const part = bytes.subarray((sequence-1)*524288, sequence*524288);
    const body = sequence ? { schemaVersion: "business-file-chunk-v1", runId: "file_1", attempt: 1, sequence, format: "xlsx", bytes: part.length, sha256: hash(part), fileSha256: digest, base64: part.toString("base64") } : { item: { id: "file_1", status: "ready", attempt: 1, bindingDigest: binding, manifest: { schemaVersion: "business-file-delivery-v1", attempt: 1, bindingDigest: binding, files: { xlsx: { bytes: size, chunkBytes: 524288, chunkCount: Math.ceil(size/524288), sha256: digest, fileName: "经营分析.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } } } } };
    calls++;
    mutate?.(body, calls);
    return Response.json(body);
  };
  return { bytes, fetcher, calls: () => calls };
}

test("download assembles more than the legacy 9 MiB limit with part and full hashes", async () => {
  const f = fixture(9*1024*1024+17), progress: number[] = [];
  const result = await downloadBusinessFile("file_1", "xlsx", { fetcher: f.fetcher, onProgress: n => progress.push(n) });
  assert.deepEqual(Buffer.from(await result.blob.arrayBuffer()), f.bytes);
  assert.equal(result.sha256, hash(f.bytes));
  assert.equal(f.calls(), 21);
  assert.equal(progress.at(-1), f.bytes.length);
});

for (const corruption of ["part", "mixedAttempt", "fileHash", "length", "finalIdentity", "finalPermission", "oversize"] as const) {
  test(`download refuses ${corruption} before exposing a Blob`, async () => {
    const f = fixture(524301, (body, call) => {
      if (call === 2) {
        if (corruption === "part") body.base64 = Buffer.alloc(524288).toString("base64");
        if (corruption === "mixedAttempt") body.attempt = 2;
        if (corruption === "fileHash") body.fileSha256 = "b".repeat(64);
        if (corruption === "length") body.bytes = body.bytes! - 1;
      }
      if (call === 4) {
        if (corruption === "finalIdentity") body.item!.id = "other";
        if (corruption === "finalPermission") throw new Error("permission revoked");
      }
      if (call === 1 && corruption === "oversize") body.item!.manifest.files.xlsx.bytes = 256*1024*1024+1;
    });
    await assert.rejects(downloadBusinessFile("file_1", "xlsx", { fetcher: f.fetcher }));
  });
}

test("abort between chunks makes no further request", async () => {
  const f = fixture(), controller = new AbortController();
  await assert.rejects(downloadBusinessFile("file_1", "xlsx", { fetcher: f.fetcher, signal: controller.signal, onProgress: n => { if (n) controller.abort(); } }));
  assert.equal(f.calls(), 2);
});

test("part hashes alone cannot substitute for the complete file hash", async () => {
  const f = fixture(524301, body => {
    if (body.item) body.item.manifest.files.xlsx.sha256 = "b".repeat(64);
    else body.fileSha256 = "b".repeat(64);
  });
  await assert.rejects(downloadBusinessFile("file_1", "xlsx", { fetcher: f.fetcher }));
  assert.equal(f.calls(), 3);
});
