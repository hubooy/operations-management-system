import { createReadStream } from "node:fs";
import { createHash } from "node:crypto";
import { lstat, mkdtemp, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assertNoReparsePoint, hashTree, runProcess, workerRuntimeRoot } from "./worker-local-release.mjs";
import { planDependencyRetention } from "./storage-retention-plan.mjs";
import { assertArchiveProof, assertPrivateRepository, canonical } from "./storage-retention-policy.mjs";

const helper = fileURLToPath(new URL("./storage-archive-bundle.py", import.meta.url));
const tag = "storage-dependencies-v1";
const maxArchiveBytes = 2 ** 31 - 1;

export async function fileSha256(file) {
  await assertNoReparsePoint(file, { label: "archive" });
  const before = await lstat(file);
  if (!before.isFile() || before.nlink !== 1 || before.size < 1 || before.size > maxArchiveBytes) {
    throw new Error("Archive must be one ordinary file smaller than 2 GiB");
  }
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  const after = await lstat(file);
  if (before.ino !== after.ino || before.size !== after.size || before.mtimeMs !== after.mtimeMs) {
    throw new Error("Archive changed while hashing");
  }
  return { sha256: hash.digest("hex"), bytes: after.size };
}

export async function verifyBundle(archive, tree, scratch, { python = "python", run = runProcess } = {}) {
  await assertNoReparsePoint(scratch, { label: "archive scratch" });
  const identity = await fileSha256(archive);
  const destination = path.join(await mkdtemp(path.join(scratch, "verify-")), "node_modules");
  await run(python, [helper, "unpack", archive, destination], { label: "isolated dependency extraction" });
  if (canonical(await hashTree(destination)) !== canonical(tree)) throw new Error("Extracted dependency tree differs from release manifest");
  const after = await fileSha256(archive);
  if (canonical(after) !== canonical(identity)) throw new Error("Archive changed during extraction");
  return { ...identity, destination };
}

export async function prepareBundle(source, tree, scratch, { python = "python", run = runProcess } = {}) {
  await assertNoReparsePoint(source, { label: "dependency source" });
  await assertNoReparsePoint(scratch, { label: "archive scratch" });
  if (canonical(await hashTree(source)) !== canonical(tree)) throw new Error("Source tree differs from release manifest");
  const staging = await mkdtemp(path.join(scratch, "prepare-"));
  const archive = path.join(staging, `dependencies-${tree.sha256}.tar.gz`);
  await run(python, [helper, "pack", source, archive], { label: "isolated dependency packing" });
  const result = await verifyBundle(archive, tree, scratch, { python, run });
  return { archive, tree, archiveSha256: result.sha256, archiveBytes: result.bytes };
}

export function githubArchiveClient(repository, { run = runProcess } = {}) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(repository)) throw new Error("Invalid repository");
  const api = async (suffix, args = []) => {
    const result = await run("gh", ["api", "--hostname", "github.com", `repos/${repository}${suffix}`, ...args], {
      label: "private GitHub archive metadata", maxOutputBytes: 2 * 1024 * 1024,
    });
    return JSON.parse(result.stdout.toString());
  };
  const assertDestination = async (id) => assertPrivateRepository(await api(""), repository, id);
  return {
    assertDestination,
    async release() {
      const release = await api(`/releases/tags/${tag}`);
      if (!Number.isSafeInteger(release.id) || release.id < 1 || release.tag_name !== tag
          || !Array.isArray(release.assets) || release.assets.length > 16) throw new Error("Invalid dependency archive release");
      return release;
    },
    async upload(archive) {
      await run("gh", ["release", "upload", tag, archive, "--repo", `https://github.com/${repository}`], {
        label: "private dependency archive upload", timeoutMs: 30 * 60 * 1000,
      });
    },
    async download(name, output) {
      if (!/^dependencies-[0-9a-f]{64}\.tar\.gz$/.test(name)) throw new Error("Invalid archive asset name");
      await run("gh", ["release", "download", tag, "--repo", `https://github.com/${repository}`,
        "--pattern", name, "--output", output], { label: "private dependency archive readback", timeoutMs: 30 * 60 * 1000 });
    },
  };
}

export async function publishAndVerifyBundle(prepared, scratch, client, options = {}) {
  const repositoryId = await client.assertDestination();
  // Re-extract before uploading: a caller-supplied digest alone is insufficient.
  const local = await verifyBundle(prepared.archive, prepared.tree, scratch, options);
  if (local.sha256 !== prepared.archiveSha256 || local.bytes !== prepared.archiveBytes) throw new Error("Prepared archive changed");
  const name = path.basename(prepared.archive);
  let release = await client.release();
  let matches = release.assets.filter((asset) => asset.name === name);
  if (matches.length > 1) throw new Error("Ambiguous archive asset");
  if (matches.length === 0) {
    // Unknown upload results are not replayed. A subsequent run must inspect
    // the existing exact asset, download it, and verify its tree before reuse.
    await client.assertDestination(repositoryId);
    await client.upload(prepared.archive);
    release = await client.release();
    matches = release.assets.filter((asset) => asset.name === name);
  }
  if (matches.length !== 1) throw new Error("Archive asset missing after upload");
  const asset = matches[0];
  if (!Number.isSafeInteger(asset.id) || asset.id < 1 || asset.state !== "uploaded"
      || !Number.isSafeInteger(asset.size) || asset.size < 1 || asset.size > maxArchiveBytes) {
    throw new Error("Incomplete archive asset");
  }
  const downloaded = path.join(await mkdtemp(path.join(scratch, "download-")), name);
  await client.assertDestination(repositoryId);
  await client.download(name, downloaded);
  const restored = await verifyBundle(downloaded, prepared.tree, scratch, options);
  if (restored.bytes !== asset.size) throw new Error("Cloud archive size mismatch");
  if (asset.digest != null && asset.digest !== `sha256:${restored.sha256}`) throw new Error("Cloud archive digest mismatch");
  const after = await client.release();
  const current = after.assets.filter((candidate) => candidate.name === name);
  if (after.id !== release.id || current.length !== 1 || current[0].id !== asset.id
      || current[0].size !== asset.size || current[0].state !== "uploaded") throw new Error("Cloud asset changed during verification");
  await client.assertDestination(repositoryId);
  const proof = {
    version: "teruisi-dependency-archive-proof-v1", repositoryId, releaseId: release.id, assetId: asset.id,
    assetName: name, tree: prepared.tree, archiveSha256: restored.sha256, archiveBytes: restored.bytes,
    downloadVerified: true, extractionVerified: true, verifiedAt: new Date().toISOString(),
  };
  assertArchiveProof(proof, prepared.tree, repositoryId);
  return proof;
}

// This command archives dependencies only. It never deletes local releases,
// changes the production startup chain or uploads database/configuration files.
export async function archiveDependencies({ repository, scratch, approvedPlanSha256 }) {
  const plan = await planDependencyRetention();
  if (plan.planSha256 !== approvedPlanSha256) throw new Error("Storage plan changed");
  scratch = path.resolve(scratch);
  const runtimeParent = path.dirname(path.resolve(workerRuntimeRoot)).toLowerCase();
  if (scratch.toLowerCase() === runtimeParent || scratch.toLowerCase().startsWith(runtimeParent + path.sep)) {
    throw new Error("Scratch cannot be inside the production runtime");
  }
  await assertNoReparsePoint(scratch, { label: "archive scratch" });
  const client = githubArchiveClient(repository);
  await client.assertDestination();
  const proofs = [];
  for (const group of plan.archiveGroups) {
    const source = path.join(workerRuntimeRoot, "releases", group.releaseIds.at(-1), "node_modules");
    const prepared = await prepareBundle(source, group.tree, scratch);
    const proof = await publishAndVerifyBundle(prepared, scratch, client);
    await writeFile(path.join(scratch, `proof-${proof.tree.sha256}.json`), `${canonical(proof)}\n`, { flag: "wx" });
    proofs.push(proof);
    process.stderr.write(`Verified dependency archive ${proof.tree.sha256}\n`);
  }
  if ((await planDependencyRetention()).planSha256 !== approvedPlanSha256) throw new Error("Storage plan changed while archiving");
  return { planSha256: plan.planSha256, proofs, productionFilesDeleted: false };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [command, repository, scratch, approvedPlanSha256, ...extra] = process.argv.slice(2);
  if (command !== "archive-dependencies" || !repository || !scratch || !approvedPlanSha256 || extra.length) {
    throw new Error("Usage: archive-dependencies owner/private-repo existing-scratch approved-plan-sha256");
  }
  archiveDependencies({ repository, scratch, approvedPlanSha256 }).then((result) => process.stdout.write(`${JSON.stringify(result)}\n`)).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
