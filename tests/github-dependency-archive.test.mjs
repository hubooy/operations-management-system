import assert from "node:assert/strict";
import { copyFile, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { hashTree } from "../tools/worker-local-release.mjs";
import { fileSha256, prepareBundle, publishAndVerifyBundle, githubArchiveClient } from "../tools/github-dependency-archive.mjs";

test("public repository is rejected before any archive write request", async () => {
  const calls = [];
  const client = githubArchiveClient("owner/backup", { run: async (command, args) => {
    calls.push([command, args]);
    return { stdout: JSON.stringify({ full_name: "owner/backup", id: 1, private: false, visibility: "public" }) };
  } });
  await assert.rejects(client.assertDestination(), /private/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].includes("POST"), false);
  assert.throws(() => githubArchiveClient("owner/../../bad"));
});

test("dependency archive proof requires actual cloud readback and exact extracted tree", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "teruisi-storage-test-"));
  try {
    const source = path.join(root, "source");
    await mkdir(source);
    await writeFile(path.join(source, "file.js"), "synthetic dependency\n");
    const tree = await hashTree(source);
    const prepared = await prepareBundle(source, tree, root);
    const file = await fileSha256(prepared.archive);
    let uploads = 0;
    let downloads = 0;
    let uploaded = false;
    const asset = { id: 42, name: path.basename(prepared.archive), size: file.bytes, state: "uploaded", digest: `sha256:${file.sha256}` };
    const client = {
      assertDestination: async (expected) => { if (expected !== undefined) assert.equal(expected, 7); return 7; },
      release: async () => ({ id: 8, assets: uploaded ? [asset] : [] }),
      upload: async () => { uploads++; uploaded = true; },
      download: async (_name, output) => { downloads++; await copyFile(prepared.archive, output); },
    };
    const proof = await publishAndVerifyBundle(prepared, root, client);
    assert.equal(proof.repositoryId, 7);
    assert.equal(proof.assetId, 42);
    assert.equal(proof.downloadVerified, true);
    assert.equal(proof.extractionVerified, true);
    assert.equal(uploads, 1);
    assert.equal(downloads, 1);
    // Reuse an existing immutable cloud asset instead of clobbering it.
    await publishAndVerifyBundle(prepared, root, client);
    assert.equal(uploads, 1);
    assert.equal(downloads, 2);
    const tamperedClient = { ...client, download: async (_name, output) => { await writeFile(output, "truncated"); } };
    await assert.rejects(publishAndVerifyBundle(prepared, root, tamperedClient));
    const duplicateClient = { ...client, release: async () => ({ id: 8, assets: [asset, asset] }) };
    await assert.rejects(publishAndVerifyBundle(prepared, root, duplicateClient), /Ambiguous/);
    const beforeUnknown = uploads;
    uploaded = false;
    const unknownUpload = { ...client, upload: async () => {
      uploads++;
      uploaded = true;
      throw new Error("upload response lost");
    } };
    await assert.rejects(publishAndVerifyBundle(prepared, root, unknownUpload), /response lost/);
    assert.equal(uploads, beforeUnknown + 1);
    await publishAndVerifyBundle(prepared, root, client);
    assert.equal(uploads, beforeUnknown + 1);
    const changedIdentity = { ...client, assertDestination: async (expected) => {
      if (expected !== undefined) throw new Error("repository recreated");
      return 7;
    } };
    await assert.rejects(publishAndVerifyBundle(prepared, root, changedIdentity), /recreated/);
    await writeFile(path.join(source, "file.js"), "changed\n");
    await assert.rejects(prepareBundle(source, tree, root), /Source tree differs/);
  } finally {
    assert.equal(path.dirname(root), path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith("teruisi-storage-test-"));
    await rm(root, { recursive: true, force: true });
  }
});
