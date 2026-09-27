import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { businessVolumeManifest, downloadBudgetV10Volume, downloadBusinessVolume, type BusinessFileRun } from "../lib/ai/business-file-download";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const actor = "c".repeat(64);

function fixture(size = 524301) {
  const bytes = Buffer.alloc(size, 89), binding = "a".repeat(64), requests: string[] = [];
  const descriptor = (volumeIndex: number, format: "html" | "xlsx" | "json") => ({ volumeIndex, format, bytes: size, sha256: hash(bytes), chunkCount: Math.ceil(size/524288) });
  const progress = { stage: "ready", attempt: 2, bindingDigest: binding,
    attestationId: "b".repeat(64), attestationSha256: "c".repeat(64),
    fullManifestDigest: "d".repeat(64), manifestFileSha256: hash(bytes),
    owningVerificationDigest: "e".repeat(64), publicationFenceDigest: "f".repeat(64),
    publishRequestDigest: "1".repeat(64) };
  const item: BusinessFileRun = { id: "file_1", reportId: "report_1", rendererVersion: 10, status: "ready", version: 8, attempt: 2, draft: false, storedBytes: size*5, errorCode: "", progress, bindingDigest: binding,
    manifest: { schemaVersion: "business-file-delivery-v2", rendererVersion: 10, attempt: 2, draft: false, bindingDigest: binding, volumeCount: 2,
      files: [descriptor(1, "html"), descriptor(1, "xlsx"), descriptor(2, "html"), descriptor(2, "xlsx")], manifestFile: descriptor(0, "json") } };
  const hooks: { root: (value: BusinessFileRun, read: number) => void; part: (value: Record<string, unknown>, sequence: number) => void; identity: (read: number) => string } = { root: () => {}, part: () => {}, identity: () => actor };
  let roots = 0, identities = 0;
  const fetcher: typeof fetch = async (input, init) => {
    assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "manual");
    const url = new URL(String(input), "https://example.invalid"); requests.push(url.pathname + url.search);
    if (url.pathname === "/api/ai/business-evidence") return Response.json({ principalKey: hooks.identity(++identities) });
    const match = /\/volumes\/(\d+)\/chunks\/(html|xlsx|json)$/.exec(url.pathname);
    if (!match) { const result = structuredClone(item); hooks.root(result, ++roots); return Response.json({ item: result }); }
    const sequence = Number(url.searchParams.get("sequence")), data = bytes.subarray((sequence-1)*524288, sequence*524288);
    const part: Record<string, unknown> = { schemaVersion: "business-volume-chunk-v10-v1", runId: item.id, volumeIndex: Number(match[1]), format: match[2], attempt: item.attempt, readyVersion: item.version, sequence,
      bytes: data.length, sha256: hash(data), fileSha256: hash(bytes), bindingDigest: binding,
      manifestFileSha256: hash(bytes), receiptDigest: "2".repeat(64), base64: data.toString("base64") };
    hooks.part(part, sequence); return Response.json(part);
  };
  return { bytes, item, fetcher, hooks, requests };
}

async function rejectsWithoutBlob(operation: () => Promise<unknown>) {
  const OriginalBlob = globalThis.Blob;
  let constructed = 0;
  globalThis.Blob = class extends OriginalBlob {
    constructor(...args: ConstructorParameters<typeof Blob>) {
      super(...args);
      constructed++;
    }
  };
  try {
    await assert.rejects(operation);
    assert.equal(constructed, 0, "rejected download must not construct a Blob");
  } finally {
    globalThis.Blob = OriginalBlob;
  }
}

for (const [volume, format] of [[2, "xlsx"], [1, "html"], [0, "json"]] as const) {
  test(`renderer10 ${volume}/${format} reassembles only one complete verified file`, async () => {
    const f = fixture(), result = await downloadBudgetV10Volume("file_1", volume, format,
      { fetcher: f.fetcher, expectedPrincipalKey: actor });
    assert.deepEqual(Buffer.from(await result.blob.arrayBuffer()), f.bytes);
    assert.equal(result.sha256, hash(f.bytes));
    assert.equal(f.requests.filter(path => path.includes("/chunks/")).length, 2);
    assert.ok(f.requests.filter(path => path.includes("/chunks/")).every(path => path.includes(`/volumes/${volume}/chunks/${format}?`)));
    await assert.rejects(downloadBusinessVolume("file_1", volume, format,
      { fetcher: f.fetcher, expectedPrincipalKey: actor }));
  });
}

for (const field of ["runId", "volumeIndex", "format", "attempt", "readyVersion", "sequence", "bindingDigest", "fileSha256", "manifestFileSha256", "receiptDigest", "sha256", "bytes", "base64"] as const) {
  test(`renderer10 rejects mismatched chunk ${field}`, async () => {
    const f = fixture();
    f.hooks.part = value => { value[field] = typeof value[field] === "number" ? Number(value[field])+1 : "wrong"; };
    await assert.rejects(downloadBudgetV10Volume("file_1", 2, "xlsx",
      { fetcher: f.fetcher, expectedPrincipalKey: actor }));
  });
}

test("renderer10 receipt must stay identical between chunks", async () => {
  const f = fixture();
  f.hooks.part = (value, sequence) => { if (sequence === 2) value.receiptDigest = "3".repeat(64); };
  await assert.rejects(downloadBudgetV10Volume("file_1", 2, "xlsx",
    { fetcher: f.fetcher, expectedPrincipalKey: actor }));
});

for (const field of ["status", "version", "attempt", "bindingDigest", "attestationId", "publicationFenceDigest", "publishRequestDigest", "owningVerificationDigest", "manifestSha"] as const) {
  test(`renderer10 final root rejects changed ${field}`, async () => {
    const f = fixture();
    f.hooks.root = (value, read) => {
      if (read !== 2 || value.manifest?.schemaVersion !== "business-file-delivery-v2") return;
      if (field === "status") value.status = "paused";
      else if (field === "version") value.version++;
      else if (field === "attempt") { value.attempt++; value.manifest.attempt++; value.progress.attempt = value.attempt; }
      else if (field === "bindingDigest") { value.bindingDigest = "4".repeat(64); value.manifest.bindingDigest = value.progress.bindingDigest = value.bindingDigest; }
      else if (field === "attestationId") value.progress.attestationId = "4".repeat(64);
      else if (field === "publicationFenceDigest") value.progress.publicationFenceDigest = "4".repeat(64);
      else if (field === "publishRequestDigest") value.progress.publishRequestDigest = "4".repeat(64);
      else if (field === "owningVerificationDigest") value.progress.owningVerificationDigest = "4".repeat(64);
      else value.manifest.files[3].sha256 = "4".repeat(64);
    };
    await rejectsWithoutBlob(() => downloadBudgetV10Volume("file_1", 2, "xlsx",
      { fetcher: f.fetcher, expectedPrincipalKey: actor }));
  });
}

test("renderer10 final account change or corrupted full-file SHA releases no Blob", async () => {
  const f = fixture();
  f.hooks.identity = read => read === 4 ? "4".repeat(64) : actor;
  await rejectsWithoutBlob(() => downloadBudgetV10Volume("file_1", 2, "xlsx",
    { fetcher: f.fetcher, expectedPrincipalKey: actor }));
  const changed = fixture();
  if (changed.item.manifest?.schemaVersion !== "business-file-delivery-v2") throw new Error("fixture");
  changed.item.manifest.files[3].sha256 = "4".repeat(64);
  changed.hooks.part = value => { value.fileSha256 = "4".repeat(64); };
  await assert.rejects(downloadBudgetV10Volume("file_1", 2, "xlsx",
    { fetcher: changed.fetcher, expectedPrincipalKey: actor }));
});

test("renderer10 account change between chunks stops before another part or Blob", async () => {
  const f = fixture();
  f.hooks.identity = read => read === 3 ? "4".repeat(64) : actor;
  await rejectsWithoutBlob(() => downloadBudgetV10Volume("file_1", 2, "xlsx",
    { fetcher: f.fetcher, expectedPrincipalKey: actor }));
  assert.equal(f.requests.filter(path => path.includes("/chunks/")).length, 1);
});

test("renderer10 manifest requires exact ready publication receipt", () => {
  for (const change of [
    (item: BusinessFileRun) => { item.progress.attestationId = "wrong"; },
    (item: BusinessFileRun) => { item.progress.manifestFileSha256 = "0".repeat(64); },
    (item: BusinessFileRun) => { item.progress.stage = "staged_unpublished"; },
    (item: BusinessFileRun) => { Object.assign(item.progress, { extra: "forged" }); },
  ]) { const f = fixture(); change(f.item); assert.throws(() => businessVolumeManifest(f.item)); }
});
