import { lstat, mkdtemp, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assertNoReparsePoint, hashTree, sha256Bytes, verifyWorkerRelease, workerRuntimeRoot } from "./worker-local-release.mjs";
import { resolveEffectiveReleaseChain } from "./worker-local-release-rotation.mjs";
import { planDependencyRetention } from "./storage-retention-plan.mjs";
import { assertArchiveProof, canonical } from "./storage-retention-policy.mjs";
import { githubArchiveClient, verifyBundle } from "./github-dependency-archive.mjs";

// The PowerShell operator owns the service mutex, process checks, durable
// reservations and native deletion. This module never deletes any file.
export function candidatePath(plan, releaseId, runtimeRoot = workerRuntimeRoot) {
  if (plan.scope !== "worker-node-modules-only" || !/^\d{8}T\d{6}Z-[0-9a-f]{16}$/.test(releaseId)
      || plan.retainedReleaseIds.includes(releaseId)) throw new Error("Protected or invalid release");
  const matches = plan.candidates.filter((candidate) => candidate.releaseId === releaseId);
  if (matches.length !== 1) throw new Error("Release outside approved candidate set");
  const releasesRoot = path.resolve(runtimeRoot, "releases");
  const target = path.resolve(releasesRoot, releaseId, "node_modules");
  if (path.dirname(path.dirname(target)) !== releasesRoot || path.basename(target) !== "node_modules") {
    throw new Error("Dependency path escaped release root");
  }
  return { candidate: matches[0], target };
}

async function exactPlan(approvedPlanSha256) {
  const plan = await planDependencyRetention();
  if (plan.planSha256 !== approvedPlanSha256) throw new Error("Storage plan changed");
  return plan;
}

async function verifyHead(plan) {
  const chain = await resolveEffectiveReleaseChain({ verifyInstalledHead: true });
  if (chain.chainStateSha256 !== plan.chainStateSha256 || chain.head.releaseId !== plan.headReleaseId) {
    throw new Error("Release chain changed");
  }
  const manifestPath = path.join(workerRuntimeRoot, "releases", chain.head.releaseId, "deployment-manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  await verifyWorkerRelease({ manifestPath, approvedManifestSha256: chain.head.manifestSha256,
    expectedSourceD1PathSha256: manifest.runtime.sourceD1PathSha256,
    expectedPersistRootPathSha256: manifest.runtime.persistRootPathSha256,
    processPolicy: "stopped-or-exact-release" });
  return { headReleaseId: chain.head.releaseId, chainStateSha256: chain.chainStateSha256, fullHeadVerified: true };
}

export async function prepareCleanup(repository, scratch, approvedPlanSha256, output) {
  const plan = await exactPlan(approvedPlanSha256);
  // Reject an unclean lifecycle state before creating more download/extraction
  // files. The same full verification is repeated after cloud recovery checks.
  await verifyHead(plan);
  scratch = path.resolve(scratch);
  const runtimeParent = path.dirname(path.resolve(workerRuntimeRoot)).toLowerCase();
  if (scratch.toLowerCase() === runtimeParent || scratch.toLowerCase().startsWith(runtimeParent + path.sep)
      || path.dirname(path.resolve(output)) !== scratch) throw new Error("Invalid isolated cleanup scratch/output");
  await assertNoReparsePoint(scratch, { label: "cleanup scratch" });
  const client = githubArchiveClient(repository);
  const repositoryId = await client.assertDestination();
  const proofs = [];
  for (const group of plan.archiveGroups) {
    const proofPath = path.join(scratch, `proof-${group.tree.sha256}.json`);
    await assertNoReparsePoint(proofPath, { label: "archive proof" });
    const proof = JSON.parse(await readFile(proofPath, "utf8"));
    assertArchiveProof(proof, group.tree, repositoryId);
    const name = `dependencies-${group.tree.sha256}.tar.gz`;
    const release = await client.release();
    const assets = release.assets.filter((asset) => asset.name === name);
    if (proof.assetName !== name || release.id !== proof.releaseId || assets.length !== 1
        || assets[0].id !== proof.assetId || assets[0].size !== proof.archiveBytes
        || assets[0].state !== "uploaded") throw new Error("Cloud archive binding changed");
    const archive = path.join(await mkdtemp(path.join(scratch, "cleanup-download-")), name);
    await client.assertDestination(repositoryId);
    await client.download(name, archive);
    const restored = await verifyBundle(archive, group.tree, scratch);
    if (restored.sha256 !== proof.archiveSha256 || restored.bytes !== proof.archiveBytes) throw new Error("Cloud archive bytes changed");
    await client.assertDestination(repositoryId);
    proofs.push({ ...proof, verifiedAt: new Date().toISOString() });
  }
  const head = await verifyHead(plan);
  await exactPlan(approvedPlanSha256);
  const result = { version: "teruisi-dependency-cleanup-preflight-v1", repository, repositoryId,
    preparedAt: new Date().toISOString(), plan, proofs, head };
  await writeFile(output, `${canonical(result)}\n`, { flag: "wx" });
  return { preflightSha256: sha256Bytes(await readFile(output)), candidates: plan.candidates.length, ...head };
}

export async function verifyCandidate(preflightPath, preflightSha256, approvedPlanSha256, releaseId) {
  // The same PowerShell parent still owns the lifecycle mutex. Bind to its
  // freshly downloaded preflight instead of rescanning 140 releases per item.
  await assertNoReparsePoint(preflightPath, { label: "cleanup preflight" });
  const preflightInfo = await lstat(preflightPath);
  if (!preflightInfo.isFile() || preflightInfo.size > 2 * 1024 * 1024) throw new Error("Invalid preflight file");
  const preflightBytes = await readFile(preflightPath);
  if (preflightBytes.length > 2 * 1024 * 1024 || sha256Bytes(preflightBytes) !== preflightSha256) throw new Error("Preflight changed");
  const snapshot = JSON.parse(preflightBytes);
  const plan = snapshot.plan;
  if (snapshot.version !== "teruisi-dependency-cleanup-preflight-v1" || plan.planSha256 !== approvedPlanSha256
      || snapshot.head.fullHeadVerified !== true || snapshot.head.chainStateSha256 !== plan.chainStateSha256) throw new Error("Invalid preflight binding");
  const { candidate, target } = candidatePath(plan, releaseId);
  await assertNoReparsePoint(target, { label: "old release dependency payload" });
  if (!(await lstat(target)).isDirectory()) throw new Error("Dependency payload is not a directory");
  const manifestPath = path.join(path.dirname(target), "deployment-manifest.json");
  const raw = await readFile(manifestPath);
  if (sha256Bytes(raw) !== candidate.manifestSha256) throw new Error("Candidate manifest changed");
  if (canonical(await hashTree(target)) !== canonical(candidate.tree)) throw new Error("Candidate dependency tree changed");
  return { releaseId, target, manifestSha256: candidate.manifestSha256, tree: candidate.tree, planSha256: approvedPlanSha256 };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [command, ...args] = process.argv.slice(2);
  let task;
  if (command === "preflight" && args.length === 4) task = prepareCleanup(...args);
  else if (command === "candidate" && args.length === 4) task = verifyCandidate(...args);
  else if (command === "postflight" && args.length === 1) task = exactPlan(args[0]).then(verifyHead);
  else throw new Error("Expected preflight repository scratch plan-sha output, candidate preflight preflight-sha plan-sha release-id, or postflight plan-sha");
  task.then((result) => process.stdout.write(`${JSON.stringify(result)}\n`)).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
