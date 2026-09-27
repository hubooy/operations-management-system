import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { businessVolumeManifest, downloadBusinessVolume, type BusinessFileRun } from "../lib/ai/business-file-download";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const actor = "c".repeat(64);
function fixture(size = 524301) {
  const bytes = Buffer.alloc(size, 89), binding = "a".repeat(64), requests: string[] = [];
  const file = (volumeIndex: number, format: "html" | "xlsx" | "json") => ({ volumeIndex, format, bytes: size, sha256: hash(bytes), chunkCount: Math.ceil(size/524288) });
  const item: BusinessFileRun = { id: "file_1", reportId: "report_1", rendererVersion: 4, status: "ready", version: 8, attempt: 2, draft: true, storedBytes: size*5, errorCode: "", progress: {}, bindingDigest: binding,
    manifest: { schemaVersion: "business-file-delivery-v2", rendererVersion: 4, attempt: 2, draft: true, bindingDigest: binding, volumeCount: 2,
      files: [file(1, "html"), file(1, "xlsx"), file(2, "html"), file(2, "xlsx")], manifestFile: file(0, "json") } };
  const hooks: { root: (item: BusinessFileRun, read: number) => void; part: (part: Record<string, unknown>) => void; identity: (read: number) => string } = { root: () => {}, part: () => {}, identity: () => actor };
  let roots = 0, identities = 0;
  const fetcher: typeof fetch = async (input, init) => {
    assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "manual");
    const url = new URL(String(input), "https://example.invalid"); requests.push(url.pathname+url.search);
    if (url.pathname === "/api/ai/business-evidence") return Response.json({ principalKey: hooks.identity(++identities) });
    const match = /\/volumes\/(\d+)\/chunks\/(html|xlsx|json)$/.exec(url.pathname);
    if (!match) { const result = structuredClone(item); hooks.root(result, ++roots); return Response.json({ item: result }); }
    const sequence = Number(url.searchParams.get("sequence")), data = bytes.subarray((sequence-1)*524288, sequence*524288);
    const part = { schemaVersion: "business-volume-chunk-v1", runId: item.id, volumeIndex: Number(match[1]), format: match[2], attempt: item.attempt, sequence,
      bytes: data.length, sha256: hash(data), fileSha256: hash(bytes), bindingDigest: binding, base64: data.toString("base64") };
    hooks.part(part); return Response.json(part);
  };
  return { bytes, item, fetcher, hooks, requests };
}

for (const [volume, format] of [[2, "xlsx"], [1, "html"], [0, "json"]] as const) {
  test(`selected volume ${volume}/${format} verifies bytes, never downloads other volumes`, async () => {
    const f = fixture(), result = await downloadBusinessVolume("file_1", volume, format, { fetcher: f.fetcher, expectedPrincipalKey: actor });
    assert.deepEqual(Buffer.from(await result.blob.arrayBuffer()), f.bytes);
    assert.equal(result.sha256, hash(f.bytes));
    assert.equal(f.requests.filter(path => path.includes("/chunks/")).length, 2);
    assert.ok(f.requests.filter(path => path.includes("/chunks/")).every(path => path.includes(`/volumes/${volume}/chunks/${format}?`)));
    assert.equal(result.fileName, volume === 0 ? "report_1-完整交付清单.json" : `report_1-volume-${String(volume).padStart(3, "0")}-of-002.${format}`);
  });
}

for (const field of ["runId", "volumeIndex", "format", "attempt", "sequence", "bindingDigest", "fileSha256", "sha256", "bytes", "base64"] as const) {
  test(`rejects mismatched chunk ${field}`, async () => {
    const f = fixture();
    f.hooks.part = value => { value[field] = typeof value[field] === "number" ? Number(value[field])+1 : "wrong"; };
    await assert.rejects(downloadBusinessVolume("file_1", 2, "xlsx", { fetcher: f.fetcher, expectedPrincipalKey: actor }));
  });
}

for (const field of ["status", "version", "reportId", "attempt", "bindingDigest", "draft", "rendererVersion", "otherFile", "count", "bytes", "manifestFile"] as const) {
  test(`final root recheck rejects changed ${field}`, async () => {
    const f = fixture();
    f.hooks.root = (item, read) => {
      if (read !== 2 || item.manifest?.schemaVersion !== "business-file-delivery-v2") return;
      if (field === "status") item.status = "paused";
      else if (field === "version") item.version++;
      else if (field === "reportId") item.reportId = "other_report";
      else if (field === "attempt") { item.attempt++; item.manifest.attempt++; }
      else if (field === "bindingDigest") { item.bindingDigest = "d".repeat(64); item.manifest.bindingDigest = item.bindingDigest; }
      else if (field === "draft") { item.draft = false; item.manifest.draft = false; }
      else if (field === "rendererVersion") item.rendererVersion = 3;
      else if (field === "otherFile") item.manifest.files[0].sha256 = "d".repeat(64);
      else if (field === "count") item.manifest.volumeCount++;
      else if (field === "bytes") item.manifest.files[3].bytes--;
      else item.manifest.manifestFile.sha256 = "d".repeat(64);
    };
    await assert.rejects(downloadBusinessVolume("file_1", 2, "xlsx", { fetcher: f.fetcher, expectedPrincipalKey: actor }));
  });
}

test("final account switch and permission failure prevent Blob release", async () => {
  for (const revoke of [false, true]) {
    const f = fixture();
    f.hooks.identity = read => { if (read === 2) { if (revoke) throw new Error("permission revoked"); return "d".repeat(64); } return actor; };
    await assert.rejects(downloadBusinessVolume("file_1", 2, "xlsx", { fetcher: f.fetcher, expectedPrincipalKey: actor }));
  }
});

test("whole file digest cannot be replaced by valid per-chunk hashes", async () => {
  const f = fixture();
  if (f.item.manifest?.schemaVersion !== "business-file-delivery-v2") throw new Error("fixture");
  f.item.manifest.files[3].sha256 = "b".repeat(64);
  f.hooks.part = part => { part.fileSha256 = "b".repeat(64); };
  await assert.rejects(downloadBusinessVolume("file_1", 2, "xlsx", { fetcher: f.fetcher, expectedPrincipalKey: actor }));
});

test("cancel after a verified chunk stops subsequent requests", async () => {
  const f = fixture(), controller = new AbortController();
  await assert.rejects(downloadBusinessVolume("file_1", 2, "xlsx", { fetcher: f.fetcher, expectedPrincipalKey: actor, signal: controller.signal,
    onProgress: received => { if (received) controller.abort(); } }));
  assert.equal(f.requests.filter(path => path.includes("/chunks/")).length, 1);
});

test("manifest rejects omissions, duplicate coordinates, oversized capacities and unexpected fields", () => {
  const changes: ((item: BusinessFileRun) => void)[] = [
    item => { if (item.manifest?.schemaVersion === "business-file-delivery-v2") item.manifest.files.pop(); },
    item => { if (item.manifest?.schemaVersion === "business-file-delivery-v2") item.manifest.files[1] = item.manifest.files[0]; },
    item => { if (item.manifest?.schemaVersion === "business-file-delivery-v2") item.manifest.files[0].bytes = 256*1024*1024+1; },
    item => { if (item.manifest?.schemaVersion === "business-file-delivery-v2") item.manifest.manifestFile.bytes = 16*1024*1024+1; },
    item => { if (item.manifest?.schemaVersion === "business-file-delivery-v2") Object.assign(item.manifest.files[0], { fileName: "forged" }); },
  ];
  for (const change of changes) { const f = fixture(); change(f.item); assert.throws(() => businessVolumeManifest(f.item)); }
});

test("manifest snapshot is detached from caller aliases", () => {
  const f = fixture(), value = businessVolumeManifest(f.item);
  if (f.item.manifest?.schemaVersion !== "business-file-delivery-v2") throw new Error("fixture");
  f.item.manifest.files[0].sha256 = "b".repeat(64);
  assert.notEqual(value.files[0].sha256, f.item.manifest.files[0].sha256);
});

for (const version of [4, 6] as const) {
  test(`renderer ${version} downloads only a matching-version manifest`, async () => {
    const f = fixture(9);
    f.item.rendererVersion = version;
    assert.equal(f.item.manifest?.schemaVersion, "business-file-delivery-v2");
    if (f.item.manifest?.schemaVersion !== "business-file-delivery-v2") throw new Error("fixture");
    f.item.manifest.rendererVersion = version;
    const result = await downloadBusinessVolume("file_1", 1, "xlsx", { fetcher: f.fetcher, expectedPrincipalKey: actor });
    assert.deepEqual(Buffer.from(await result.blob.arrayBuffer()), f.bytes);
    f.item.manifest.rendererVersion = version === 4 ? 6 : 4;
    assert.throws(() => businessVolumeManifest(f.item, "file_1"));
  });
}

for (const version of [7, 9] as const) test(`renderer ${version} verifies published approval fence and full selected file`, async () => {
  const f = fixture(15);
  if (f.item.manifest?.schemaVersion !== "business-file-delivery-v2") throw new Error("fixture");
  f.item.rendererVersion = f.item.manifest.rendererVersion = version;
  f.item.draft = f.item.manifest.draft = false;
  f.item.progress = { stage: "ready", publicationFenceDigest: "e".repeat(64),
    manifestFileSha256: f.item.manifest.manifestFile.sha256 };
  const result = await downloadBusinessVolume("file_1", 2, "xlsx", {
    fetcher: f.fetcher, expectedPrincipalKey: actor });
  assert.deepEqual(Buffer.from(await result.blob.arrayBuffer()), f.bytes);
  for (const mutate of [
    (item: BusinessFileRun) => { item.draft = true; },
    (item: BusinessFileRun) => { item.progress.publicationFenceDigest = "bad"; },
    (item: BusinessFileRun) => { item.progress.manifestFileSha256 = "0".repeat(64); },
    (item: BusinessFileRun) => { item.progress.stage = "staged_unpublished"; },
  ]) {
    const changed = fixture(15);
    if (changed.item.manifest?.schemaVersion !== "business-file-delivery-v2") throw new Error("fixture");
    changed.item.rendererVersion = changed.item.manifest.rendererVersion = version;
    changed.item.draft = changed.item.manifest.draft = false;
    changed.item.progress = { stage: "ready", publicationFenceDigest: "e".repeat(64),
      manifestFileSha256: changed.item.manifest.manifestFile.sha256 };
    mutate(changed.item);
    assert.throws(() => businessVolumeManifest(changed.item));
  }
});

for (const version of [7, 9] as const) test(`renderer ${version} final root recheck rejects changed publication fence`, async () => {
  const f = fixture(9);
  if (f.item.manifest?.schemaVersion !== "business-file-delivery-v2") throw new Error("fixture");
  f.item.rendererVersion = f.item.manifest.rendererVersion = version;
  f.item.draft = f.item.manifest.draft = false;
  f.item.progress = { stage: "ready", publicationFenceDigest: "e".repeat(64),
    manifestFileSha256: f.item.manifest.manifestFile.sha256 };
  f.hooks.root = (item, read) => {
    if (read === 2) item.progress.publicationFenceDigest = "f".repeat(64);
  };
  await assert.rejects(downloadBusinessVolume("file_1", 1, "html", {
    fetcher: f.fetcher, expectedPrincipalKey: actor }));
});
