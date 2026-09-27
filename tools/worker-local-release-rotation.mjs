import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { randomUUID } from "node:crypto";
import {
  lstat,
  link,
  mkdir,
  open,
  readFile,
  readdir,
  rename,
  rm,
  stat,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readD1RetirementReceipt } from "./d1-retirement-proof.mjs";

import {
  activationFenceRelativePath,
  assertExactKeys,
  buildWorkerReleaseCandidate,
  canonicalJson,
  canonicalWindowsPath,
  contractReceiptVersion,
  releaseVersion,
  runProcess,
  salesRetirementMigrationSha256,
  sha256Bytes,
  sha256Canonical,
  verifyWorkerRelease,
  windowsPathSha256,
  withPayloadSha256,
  workerReleaseActivationFence,
  workerDevVarsSource,
  workerHost,
  workerPersistRoot,
  workerPort,
  workerRuntimeRoot,
  workerSourceRoot,
} from "./worker-local-release.mjs";

export const rotationPlanVersion = "teruisi-local-worker-release-rotation-plan-v1";
export const successorVersion = "teruisi-local-worker-release-successor-v1";
export const rotationConsumptionVersion = "teruisi-local-worker-release-rotation-consumption-v1";
export const releaseBindingVersion = "teruisi-local-worker-release-binding-v1";
export const successorDirectoryName = "worker-release-successors";
export const rotationPlanDirectoryName = "worker-release-rotation-plans";
export const rotationConsumptionDirectoryName = "worker-release-rotation-consumptions";
export const publicationStagingDirectoryName = "worker-release-publication-staging";
export const djangoSalesRuntimeRoot = "D:\\teruisi-runtime\\django-sales";

const modulePath = fileURLToPath(import.meta.url);
const hex64 = /^[0-9a-f]{64}$/;
const releaseIdPattern = /^\d{8}T\d{6}Z-[0-9a-f]{16}$/;
const cutoverIdPattern = /^[A-Za-z0-9._:-]{8,128}$/;
// Reviewed bounded extension; retain and validate the complete original chain.
// See docs/WORKER_SUCCESSOR_CAPACITY.md. No environment/CLI override is allowed.
const maximumSuccessors = 256;
const rotationLockPipe = "\\\\.\\pipe\\TERUISI.Worker.ReleaseRotation.v1";

function fail(message) {
  throw new Error(message);
}

async function exists(target) {
  try {
    await lstat(target);
    return true;
  } catch (error) {
    if (error && typeof error === "object" && error.code === "ENOENT") return false;
    throw error;
  }
}

async function assertNoReparsePath(target, { allowMissingLeaf = false, label = "路径" } = {}) {
  const absolute = path.resolve(target);
  const parsed = path.parse(absolute);
  let current = parsed.root;
  const parts = absolute.slice(parsed.root.length).split(path.sep).filter(Boolean);
  for (let index = 0; index < parts.length; index += 1) {
    current = path.join(current, parts[index]);
    let info;
    try {
      info = await lstat(current);
    } catch (error) {
      if (allowMissingLeaf && error && typeof error === "object" && error.code === "ENOENT") return;
      throw error;
    }
    if (info.isSymbolicLink() || info.isDirectory() && (info.mode & 0o170000) !== 0o040000) {
      fail(`${label}不得包含 reparse/symlink：${current}`);
    }
  }
}

async function assertEntityDirectory(target, label) {
  await assertNoReparsePath(target, { label });
  const info = await lstat(target);
  if (!info.isDirectory() || info.isSymbolicLink()) fail(`${label}必须是实体目录`);
}

async function assertRegularFile(target, label) {
  await assertNoReparsePath(target, { label });
  const info = await lstat(target);
  if (!info.isFile() || info.isSymbolicLink()) fail(`${label}必须是普通文件`);
}

function validatePayload(value, fieldName, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !hex64.test(value[fieldName] ?? "")) {
    fail(`${label}自哈希字段无效`);
  }
  const core = { ...value };
  delete core[fieldName];
  if (sha256Canonical(core) !== value[fieldName]) fail(`${label}自哈希无效`);
}

async function readCanonical(target, label) {
  await assertRegularFile(target, label);
  const raw = await readFile(target);
  let value;
  try {
    value = JSON.parse(raw.toString("utf8"));
  } catch {
    fail(`${label}不是有效 JSON`);
  }
  if (!raw.equals(Buffer.from(`${canonicalJson(value)}\n`, "utf8"))) fail(`${label}不是 canonical JSON`);
  return { value, raw, sha256: sha256Bytes(raw) };
}

async function readStableJson(target, label) {
  await assertRegularFile(target, label);
  const before = await stat(target, { bigint: true });
  const raw = await readFile(target);
  const after = await stat(target, { bigint: true });
  if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size || before.mtimeNs !== after.mtimeNs) {
    fail(`${label}读取期间发生变化`);
  }
  let value;
  try {
    value = JSON.parse(raw.toString("utf8"));
  } catch {
    fail(`${label}不是有效 JSON`);
  }
  return { value, raw, sha256: sha256Bytes(raw) };
}

async function writeCreateOnly(target, raw) {
  const absoluteTarget = path.resolve(target);
  const finalDirectory = path.dirname(absoluteTarget);
  const stateRoot = path.dirname(finalDirectory);
  const allowedFinalDirectories = new Set([
    successorDirectoryName, rotationPlanDirectoryName, rotationConsumptionDirectoryName,
  ]);
  if (path.basename(stateRoot).toLowerCase() !== "state"
      || !allowedFinalDirectories.has(path.basename(finalDirectory))
      || path.dirname(stateRoot) === stateRoot) {
    fail("create-only publication target 不在固定 Worker state 目录");
  }
  await assertEntityDirectory(stateRoot, "Worker runtime state 根");
  const stagingRoot = path.join(stateRoot, publicationStagingDirectoryName);
  if (!(await exists(stagingRoot))) await mkdir(stagingRoot);
  await assertEntityDirectory(stagingRoot, "Worker publication staging 根");
  const temporary = path.join(stagingRoot, `publication-${randomUUID()}.tmp`);
  let handle;
  try {
    handle = await open(temporary, "wx");
    await handle.writeFile(raw);
    await handle.sync();
    await handle.close();
    handle = null;
    await link(temporary, absoluteTarget);
    await rm(temporary, { force: true });
  } catch (error) {
    if (handle) await handle.close().catch(() => {});
    await rm(temporary, { force: true }).catch(() => {});
    throw error;
  }
}

async function publishCanonicalWithSidecar(target, value, label) {
  const raw = Buffer.from(`${canonicalJson(value)}\n`, "utf8");
  const rawSha256 = sha256Bytes(raw);
  const sidecarPath = `${target}.sha256`;
  if (await exists(target)) {
    const existing = await readCanonical(target, label);
    if (!existing.raw.equals(raw)) fail(`${label}已存在但与批准元组不一致`);
  } else {
    if (await exists(sidecarPath)) fail(`${label} sidecar 孤立存在`);
    try {
      await writeCreateOnly(target, raw);
    } catch (error) {
      if (!(error && typeof error === "object" && error.code === "EEXIST")) throw error;
      const winner = await readCanonical(target, label);
      if (!winner.raw.equals(raw)) fail(`${label}由竞争者发布了不同元组`);
    }
  }
  const expectedSidecar = Buffer.from(`${rawSha256}\n`, "ascii");
  if (await exists(sidecarPath)) {
    await assertRegularFile(sidecarPath, `${label} sidecar`);
    if (!(await readFile(sidecarPath)).equals(expectedSidecar)) fail(`${label} sidecar 无效`);
  } else {
    try {
      await writeCreateOnly(sidecarPath, expectedSidecar);
    } catch (error) {
      if (!(error && typeof error === "object" && error.code === "EEXIST")) throw error;
      await assertRegularFile(sidecarPath, `${label} sidecar`);
      if (!(await readFile(sidecarPath)).equals(expectedSidecar)) fail(`${label} sidecar 竞争发布冲突`);
    }
  }
  await assertRegularFile(sidecarPath, `${label} sidecar`);
  const final = await readCanonical(target, label);
  if (final.sha256 !== rawSha256 || !(await readFile(sidecarPath)).equals(expectedSidecar)) {
    fail(`${label}发布后回读失败`);
  }
  return { path: target, sidecarPath, sha256: rawSha256 };
}

export function releaseBinding({ releaseId, manifestSha256, guardReceiptSha256 }) {
  if (!releaseIdPattern.test(releaseId ?? "")) fail("release binding releaseId 无效");
  if (![manifestSha256, guardReceiptSha256].every((value) => hex64.test(value ?? ""))) {
    fail("release binding SHA-256 无效");
  }
  return withPayloadSha256({
    version: releaseBindingVersion,
    releaseId,
    manifestSha256,
    guardReceiptSha256,
  }, "bindingSha256");
}

function validateBinding(value, label) {
  assertExactKeys(value, ["version", "releaseId", "manifestSha256", "guardReceiptSha256", "bindingSha256"], label);
  validatePayload(value, "bindingSha256", label);
  const normalized = releaseBinding(value);
  if (canonicalJson(normalized) !== canonicalJson(value)) fail(`${label}身份无效`);
  return value;
}

async function readManifest(runtimeRoot, binding, label) {
  validateBinding(binding, `${label} binding`);
  const manifestPath = path.join(runtimeRoot, "releases", binding.releaseId, "deployment-manifest.json");
  const manifestRead = await readCanonical(manifestPath, `${label} manifest`);
  if (manifestRead.sha256 !== binding.manifestSha256) fail(`${label} manifest SHA-256 无效`);
  const manifest = manifestRead.value;
  assertExactKeys(manifest, [
    "version", "releaseId", "createdAt", "source", "build", "runtime", "artifacts", "processIdentity", "manifestPayloadSha256",
  ], `${label} manifest`);
  validatePayload(manifest, "manifestPayloadSha256", `${label} manifest`);
  if (manifest.version !== releaseVersion || manifest.releaseId !== binding.releaseId
    || manifest.runtime?.runtimeRootPathSha256 !== windowsPathSha256(runtimeRoot)
    || manifest.runtime?.releaseRootPathSha256 !== windowsPathSha256(path.dirname(manifestPath))
    || manifest.runtime?.sourceD1PathSha256 !== windowsPathSha256(manifest.runtime?.sourceD1Path)
    || manifest.runtime?.persistRootPathSha256 !== windowsPathSha256(manifest.runtime?.persistRoot)
    || manifest.runtime?.protectedSourceRootPathSha256 !== windowsPathSha256(manifest.runtime?.protectedSourceRoot)) {
    fail(`${label} manifest 路径/身份无效`);
  }
  const guard = manifest.artifacts?.guardReceipt;
  const contract = manifest.artifacts?.contractReceipt;
  assertExactKeys(guard, ["version", "relativePath", "sha256"], `${label} guard pointer`);
  assertExactKeys(contract, ["version", "relativePath", "sha256"], `${label} contract pointer`);
  if (guard.relativePath !== "audit/legacy-worker-guard-receipt.json" || guard.sha256 !== binding.guardReceiptSha256
    || contract.version !== contractReceiptVersion || contract.relativePath !== "audit/sales-retired-code-receipt.json"
    || !hex64.test(contract.sha256 ?? "")) fail(`${label} manifest receipt pointer 无效`);
  const guardPath = path.join(path.dirname(manifestPath), ...guard.relativePath.split("/"));
  const contractPath = path.join(path.dirname(manifestPath), ...contract.relativePath.split("/"));
  if ((await readCanonical(guardPath, `${label} guard receipt material`)).sha256 !== guard.sha256
    || (await readCanonical(contractPath, `${label} contract receipt material`)).sha256 !== contract.sha256) {
    fail(`${label} manifest receipt material SHA-256 无效`);
  }
  return { manifestPath, manifestSha256: manifestRead.sha256, manifest };
}

async function readGuardReceipt(manifestContext, label) {
  const pointer = manifestContext.manifest.artifacts.guardReceipt;
  const target = path.join(path.dirname(manifestContext.manifestPath), ...pointer.relativePath.split("/"));
  const read = await readCanonical(target, `${label} guard receipt`);
  if (read.sha256 !== pointer.sha256) fail(`${label} guard receipt SHA-256 无效`);
  const receipt = read.value;
  assertExactKeys(receipt, [
    "version", "generatedAt", "sourceFingerprint", "status", "bindings", "checks", "entrypoints",
    "forbiddenLegacyDirectCommands", "receiptPayloadSha256",
  ], `${label} guard receipt`);
  validatePayload(receipt, "receiptPayloadSha256", `${label} guard receipt`);
  if (receipt.version !== "teruisi-legacy-worker-guard-receipt-v1" || receipt.status !== "passed"
    || receipt.sourceFingerprint !== manifestContext.manifest.source.sourceFingerprint
    || !Array.isArray(receipt.entrypoints) || receipt.entrypoints.length < 1) fail(`${label} guard receipt 身份无效`);
  const seen = new Set();
  for (const item of receipt.entrypoints) {
    assertExactKeys(item, ["relativePath", "sha256"], `${label} guard entrypoint`);
    if (typeof item.relativePath !== "string" || item.relativePath.includes("\\") || path.posix.isAbsolute(item.relativePath)
      || item.relativePath.split("/").some((part) => !part || part === "." || part === "..")
      || !hex64.test(item.sha256 ?? "") || seen.has(item.relativePath)) fail(`${label} guard entrypoint 无效`);
    seen.add(item.relativePath);
  }
  return { receipt, sha256: read.sha256 };
}

async function readActivationFence(manifestContext, guardContext, label, { required = false } = {}) {
  const entries = guardContext.receipt.entrypoints.filter((item) => item.relativePath === activationFenceRelativePath);
  if (entries.length === 0 && !required) return null;
  if (entries.length !== 1) fail(`${label} activation fence 数量无效`);
  const target = path.join(path.dirname(manifestContext.manifestPath), ...activationFenceRelativePath.split("/"));
  const read = await readCanonical(target, `${label} activation fence`);
  if (read.sha256 !== entries[0].sha256) fail(`${label} activation fence 未绑定 guard receipt`);
  assertExactKeys(read.value, [
    "version", "createdAt", "sourceFingerprint", "buildFingerprint", "payloadSha256",
  ], `${label} activation fence`);
  validatePayload(read.value, "payloadSha256", `${label} activation fence`);
  const expected = workerReleaseActivationFence({
    createdAt: manifestContext.manifest.createdAt,
    sourceFingerprint: manifestContext.manifest.source?.sourceFingerprint,
    buildFingerprint: manifestContext.manifest.build?.buildFingerprint,
  });
  if (canonicalJson(read.value) !== canonicalJson(expected)) fail(`${label} activation fence 未绑定 immutable build identity`);
  return read;
}

async function readBootstrap(runtimeRoot, { allowTestRuntimeRoot = false } = {}) {
  if (!allowTestRuntimeRoot && canonicalWindowsPath(runtimeRoot) !== canonicalWindowsPath(workerRuntimeRoot)) {
    fail(`rotation runtime 根必须固定为 ${workerRuntimeRoot}`);
  }
  await assertEntityDirectory(runtimeRoot, "Worker runtime 根");
  await assertEntityDirectory(path.join(runtimeRoot, "state"), "Worker runtime state 根");
  const markerRead = await readCanonical(path.join(runtimeRoot, "runtime-root.json"), "Worker runtime marker");
  assertExactKeys(markerRead.value, ["version", "runtimeRootPathSha256", "markerPayloadSha256"], "Worker runtime marker");
  validatePayload(markerRead.value, "markerPayloadSha256", "Worker runtime marker");
  if (markerRead.value.version !== releaseVersion
    || markerRead.value.runtimeRootPathSha256 !== windowsPathSha256(runtimeRoot)) fail("Worker runtime marker 身份无效");
  const pointerPath = path.join(runtimeRoot, "current-deployment.json");
  const pointerRead = await readCanonical(pointerPath, "bootstrap current pointer");
  const pointer = pointerRead.value;
  assertExactKeys(pointer, ["version", "releaseId", "manifestRelativePath", "manifestSha256", "pointerPayloadSha256"], "bootstrap current pointer");
  validatePayload(pointer, "pointerPayloadSha256", "bootstrap current pointer");
  if (pointer.version !== "teruisi-local-worker-current-v1" || !releaseIdPattern.test(pointer.releaseId ?? "")
    || pointer.manifestRelativePath !== `releases/${pointer.releaseId}/deployment-manifest.json`
    || !hex64.test(pointer.manifestSha256 ?? "")) fail("bootstrap current pointer 身份无效");

  const authorityPath = path.join(runtimeRoot, "state", "sales-postgresql-authority.json");
  const authoritySidecarPath = `${authorityPath}.sha256`;
  const authorityRead = await readCanonical(authorityPath, "bootstrap PostgreSQL authority");
  await assertRegularFile(authoritySidecarPath, "bootstrap PostgreSQL authority sidecar");
  if (!(await readFile(authoritySidecarPath)).equals(Buffer.from(`${authorityRead.sha256}\n`, "ascii"))) {
    fail("bootstrap PostgreSQL authority sidecar 无效");
  }
  const authority = authorityRead.value;
  assertExactKeys(authority, [
    "version", "domain", "authority", "cutoverId", "workerReleaseId", "workerReleaseManifestSha256",
    "djangoDeploymentManifestSha256", "guardReceiptSha256", "sourceD1PathSha256", "persistRootPathSha256", "payloadSha256",
  ], "bootstrap PostgreSQL authority");
  validatePayload(authority, "payloadSha256", "bootstrap PostgreSQL authority");
  if (authority.version !== "teruisi-sales-postgresql-authority-v1" || authority.domain !== "sales"
    || authority.authority !== "postgresql" || !cutoverIdPattern.test(authority.cutoverId ?? "")
    || ![authority.workerReleaseManifestSha256, authority.djangoDeploymentManifestSha256,
      authority.guardReceiptSha256, authority.sourceD1PathSha256, authority.persistRootPathSha256]
      .every((value) => hex64.test(value ?? ""))
    || authority.workerReleaseId !== pointer.releaseId
    || authority.workerReleaseManifestSha256 !== pointer.manifestSha256) {
    fail("bootstrap current/authority 元组无效");
  }
  const binding = releaseBinding({
    releaseId: authority.workerReleaseId,
    manifestSha256: authority.workerReleaseManifestSha256,
    guardReceiptSha256: authority.guardReceiptSha256,
  });
  const manifestContext = await readManifest(runtimeRoot, binding, "bootstrap");
  if (manifestContext.manifest.runtime.sourceD1PathSha256 !== authority.sourceD1PathSha256
    || manifestContext.manifest.runtime.persistRootPathSha256 !== authority.persistRootPathSha256) {
    fail("bootstrap authority 路径哈希未绑定 manifest");
  }
  return {
    pointerPath,
    pointerSha256: pointerRead.sha256,
    authorityPath,
    authoritySidecarPath,
    authoritySha256: authorityRead.sha256,
    authority,
    binding,
    manifestContext,
  };
}

function validateLineage(lineage, bootstrap, label) {
  assertExactKeys(lineage, [
    "bootstrapCurrentPointerSha256", "bootstrapAuthoritySha256", "cutoverId", "djangoDeploymentManifestSha256",
    "sourceD1PathSha256", "persistRootPathSha256", "attestationPayloadSha256", "attestationFileSha256",
    "forwardRecoverySha256", "salesRetirementMigrationSha256", "predecessorContractReceiptSha256",
    "successorContractReceiptSha256",
  ], label);
  for (const field of [
    "bootstrapCurrentPointerSha256", "bootstrapAuthoritySha256", "djangoDeploymentManifestSha256", "sourceD1PathSha256",
    "persistRootPathSha256", "attestationPayloadSha256", "attestationFileSha256", "forwardRecoverySha256",
    "salesRetirementMigrationSha256", "predecessorContractReceiptSha256", "successorContractReceiptSha256",
  ]) if (!hex64.test(lineage[field] ?? "")) fail(`${label} ${field}无效`);
  if (lineage.bootstrapCurrentPointerSha256 !== bootstrap.pointerSha256
    || lineage.bootstrapAuthoritySha256 !== bootstrap.authoritySha256
    || lineage.cutoverId !== bootstrap.authority.cutoverId
    || lineage.djangoDeploymentManifestSha256 !== bootstrap.authority.djangoDeploymentManifestSha256
    || lineage.sourceD1PathSha256 !== bootstrap.authority.sourceD1PathSha256
    || lineage.persistRootPathSha256 !== bootstrap.authority.persistRootPathSha256
    || lineage.salesRetirementMigrationSha256 !== salesRetirementMigrationSha256) {
    fail(`${label}未继承 bootstrap PostgreSQL-only lineage`);
  }
}

export function successorPayload({ sequence, predecessor, successor, lineage, approvedPlanSha256, activatedAt }) {
  validateBinding(predecessor, "successor predecessor");
  validateBinding(successor, "successor target");
  if (!Number.isSafeInteger(sequence) || sequence < 1 || sequence > maximumSuccessors) fail("successor sequence 无效");
  if (!hex64.test(approvedPlanSha256 ?? "") || typeof activatedAt !== "string" || Number.isNaN(Date.parse(activatedAt))) {
    fail("successor approval/timestamp 无效");
  }
  return withPayloadSha256({
    version: successorVersion,
    sequence,
    predecessor,
    successor,
    lineage,
    approvedPlanSha256,
    activatedAt,
  }, "payloadSha256");
}

function validateSuccessor(value, bootstrap, label) {
  assertExactKeys(value, [
    "version", "sequence", "predecessor", "successor", "lineage", "approvedPlanSha256", "activatedAt", "payloadSha256",
  ], label);
  validatePayload(value, "payloadSha256", label);
  if (value.version !== successorVersion) fail(`${label}版本无效`);
  validateBinding(value.predecessor, `${label} predecessor`);
  validateBinding(value.successor, `${label} successor`);
  validateLineage(value.lineage, bootstrap, `${label} lineage`);
  if (!Number.isSafeInteger(value.sequence) || value.sequence < 1 || value.sequence > maximumSuccessors
    || !hex64.test(value.approvedPlanSha256 ?? "") || typeof value.activatedAt !== "string"
    || Number.isNaN(Date.parse(value.activatedAt))) fail(`${label}序号/批准字段无效`);
  return value;
}

async function readSuccessorRecords(runtimeRoot, bootstrap, { acceptedIncompleteRecord = null } = {}) {
  const root = path.join(runtimeRoot, "state", successorDirectoryName);
  if (!(await exists(root))) return { root, recordsByPredecessor: new Map(), recordCount: 0, incompleteRecordCount: 0 };
  await assertEntityDirectory(root, "Worker successor 根");
  const names = await readdir(root);
  if (names.length > maximumSuccessors * 2) fail("Worker successor 文件数超过有界上限");
  const jsonNames = names.filter((name) => /^[0-9a-f]{64}\.json$/.test(name));
  const sidecarNames = names.filter((name) => /^[0-9a-f]{64}\.json\.sha256$/.test(name));
  if (jsonNames.length + sidecarNames.length !== names.length) fail("Worker successor 目录存在未知文件");
  const jsonSet = new Set(jsonNames);
  const sidecarBaseSet = new Set(sidecarNames.map((name) => name.slice(0, -".sha256".length)));
  if (sidecarNames.some((name) => !jsonSet.has(name.slice(0, -".sha256".length)))) {
    fail("Worker successor sidecar 孤立存在");
  }
  const missingSidecars = jsonNames.filter((name) => !sidecarBaseSet.has(name));
  const acceptedName = acceptedIncompleteRecord
    ? `${acceptedIncompleteRecord.predecessor.bindingSha256}.json`
    : null;
  if (missingSidecars.length > 0
      && (missingSidecars.length !== 1 || missingSidecars[0] !== acceptedName)) {
    fail("Worker successor record/sidecar 不完整");
  }
  const recordsByPredecessor = new Map();
  for (const name of jsonNames.sort()) {
    const bindingSha256 = name.slice(0, 64);
    const target = path.join(root, name);
    const read = await readCanonical(target, "Worker successor record");
    const value = validateSuccessor(read.value, bootstrap, "Worker successor record");
    if (!sidecarBaseSet.has(name)) {
      if (canonicalJson(value) !== canonicalJson(acceptedIncompleteRecord)) {
        fail("Worker successor 不完整 record 与批准恢复元组不一致");
      }
    } else {
      await assertRegularFile(`${target}.sha256`, "Worker successor sidecar");
      const sidecar = await readFile(`${target}.sha256`);
      if (!sidecar.equals(Buffer.from(`${read.sha256}\n`, "ascii"))) fail("Worker successor sidecar 无效");
    }
    if (value.predecessor.bindingSha256 !== bindingSha256 || recordsByPredecessor.has(bindingSha256)) {
      fail("Worker successor 文件名/前驱绑定无效或分叉");
    }
    recordsByPredecessor.set(bindingSha256, { value, rawSha256: read.sha256, path: target });
  }
  return { root, recordsByPredecessor, recordCount: recordsByPredecessor.size, incompleteRecordCount: missingSidecars.length };
}

async function verifyInstalledGuardEntrypoints(manifestContext, label) {
  const guard = await readGuardReceipt(manifestContext, label);
  const protectedRoot = manifestContext.manifest.runtime.protectedSourceRoot;
  await assertEntityDirectory(protectedRoot, `${label} protected source root`);
  for (const item of guard.receipt.entrypoints) {
    const installed = path.join(protectedRoot, ...item.relativePath.split("/"));
    await assertRegularFile(installed, `${label} installed ${item.relativePath}`);
    if (sha256Bytes(await readFile(installed)) !== item.sha256) fail(`${label} protected entrypoint 未匹配 guard：${item.relativePath}`);
  }
  return guard;
}

function chainStateSha256(bootstrap, records, head) {
  return sha256Canonical({
    bootstrapCurrentPointerSha256: bootstrap.pointerSha256,
    bootstrapAuthoritySha256: bootstrap.authoritySha256,
    records: records.map((item) => item.rawSha256),
    headBindingSha256: head.bindingSha256,
  });
}

async function validateSuccessorTransition({ runtimeRoot, bootstrap, records, head, headManifest, item, seenBindings }) {
  const record = item.value;
  if (canonicalJson(record.predecessor) !== canonicalJson(head) || record.sequence !== records.length + 1) {
    fail("Worker successor 链前驱或序号不连续");
  }
  if (seenBindings.has(record.successor.bindingSha256)) fail("Worker successor candidate 重复或形成环");
  const predecessorChainStateSha256 = chainStateSha256(bootstrap, records, head);
  const approvedPlan = await loadApprovedPlan(runtimeRoot, record.approvedPlanSha256, bootstrap);
  if (approvedPlan.plan.predecessorChainStateSha256 !== predecessorChainStateSha256
    || approvedPlan.plan.predecessorSequence !== records.length
    || canonicalJson(approvedPlan.plan.predecessor) !== canonicalJson(record.predecessor)
    || canonicalJson(approvedPlan.plan.candidate) !== canonicalJson(record.successor)
    || canonicalJson(approvedPlan.plan.lineage) !== canonicalJson(record.lineage)
    || canonicalJson(successorFromPlan(approvedPlan.plan, approvedPlan.sha256)) !== canonicalJson(record)) {
    fail("Worker successor 未绑定精确 approved plan/CAS tuple");
  }
  const successorManifest = await readManifest(runtimeRoot, record.successor, `successor ${record.sequence}`);
  const predecessorHasProof = Boolean(headManifest.manifest.artifacts.d1RetirementReceipt);
  const successorHasProof = Boolean(successorManifest.manifest.artifacts.d1RetirementReceipt);
  if (predecessorHasProof && !successorHasProof) fail("D1 retirement proof downgrade is forbidden");
  if (successorHasProof) {
    const proof = await readD1RetirementReceipt(successorManifest.manifest, path.dirname(successorManifest.manifestPath), {
      bootstrapAuthoritySha256: bootstrap.authoritySha256,
    });
    if (predecessorHasProof) {
      const previousProof = await readD1RetirementReceipt(headManifest.manifest, path.dirname(headManifest.manifestPath));
      if (proof.proofSha256 !== previousProof.proofSha256) fail("D1 retirement evidence changed across successors");
    } else if (proof.adoptionPredecessorManifestSha256 !== headManifest.manifestSha256) {
      fail("D1 retirement proof does not bind the adoption predecessor");
    }
  }
  const expectedEntrypoints = await entrypointPlanFromReceipts(headManifest, successorManifest);
  if (canonicalJson(approvedPlan.plan.protectedEntrypoints) !== canonicalJson(expectedEntrypoints)) {
    fail("Worker successor approved plan 未精确绑定 predecessor/candidate guard entrypoints");
  }
  if (successorManifest.manifest.runtime.sourceD1PathSha256 !== bootstrap.authority.sourceD1PathSha256
    || successorManifest.manifest.runtime.persistRootPathSha256 !== bootstrap.authority.persistRootPathSha256
    || successorManifest.manifest.runtime.protectedSourceRootPathSha256
      !== bootstrap.manifestContext.manifest.runtime.protectedSourceRootPathSha256
    || record.lineage.predecessorContractReceiptSha256 !== headManifest.manifest.artifacts.contractReceipt.sha256
    || record.lineage.successorContractReceiptSha256 !== successorManifest.manifest.artifacts.contractReceipt.sha256) {
    fail("Worker successor manifest 未保持 PostgreSQL authority/receipt lineage");
  }
  if (records.length > 0) {
    const previous = records[records.length - 1].value.lineage;
    for (const field of ["attestationPayloadSha256", "attestationFileSha256", "forwardRecoverySha256"]) {
      if (record.lineage[field] !== previous[field]) fail("Worker successor 原始 cutover evidence lineage 发生变化");
    }
  }
  return { approvedPlan, successorManifest };
}

async function resolveEffectiveReleaseChainInternal({
  runtimeRoot = workerRuntimeRoot,
  allowTestRuntimeRoot = false,
  verifyInstalledHead = false,
  acceptedIncompleteRecord = null,
} = {}) {
  runtimeRoot = path.resolve(runtimeRoot);
  const bootstrap = await readBootstrap(runtimeRoot, { allowTestRuntimeRoot });
  const successorSet = await readSuccessorRecords(runtimeRoot, bootstrap, { acceptedIncompleteRecord });
  let head = bootstrap.binding;
  let headManifest = bootstrap.manifestContext;
  const records = [];
  const seen = new Set([head.bindingSha256]);
  while (successorSet.recordsByPredecessor.has(head.bindingSha256)) {
    if (records.length >= maximumSuccessors) fail("Worker successor 链超过有界上限");
    const item = successorSet.recordsByPredecessor.get(head.bindingSha256);
    const record = item.value;
    const transition = await validateSuccessorTransition({
      runtimeRoot, bootstrap, records, head, headManifest, item, seenBindings: seen,
    });
    records.push(item);
    head = record.successor;
    headManifest = transition.successorManifest;
    seen.add(head.bindingSha256);
  }
  if (records.length !== successorSet.recordCount) fail("Worker successor 目录存在不可达记录、分叉或伪造前驱");
  const resolvedChainStateSha256 = chainStateSha256(bootstrap, records, head);
  const headGuard = verifyInstalledHead ? await verifyInstalledGuardEntrypoints(headManifest, "effective head") : null;
  return {
    status: "resolved",
    version: "teruisi-local-worker-effective-release-v1",
    runtimeRoot,
    bootstrap,
    head,
    headManifestPath: headManifest.manifestPath,
    headManifest: headManifest.manifest,
    headGuardReceiptSha256: headGuard?.sha256 ?? head.guardReceiptSha256,
    successorCount: records.length,
    chainStateSha256: resolvedChainStateSha256,
    records,
    incompleteRecordCount: successorSet.incompleteRecordCount ?? 0,
  };
}

export async function resolveEffectiveReleaseChain(options = {}) {
  return resolveEffectiveReleaseChainInternal(options);
}

async function readCutoverEvidence(authority) {
  const digest = sha256Bytes(Buffer.from(authority.cutoverId, "utf8")).slice(0, 24);
  const auditRoot = path.join(djangoSalesRuntimeRoot, "audits", "sales-cutover");
  await assertEntityDirectory(auditRoot, "Django sales cutover audit 根");
  const forwardPath = path.join(auditRoot, `sales-cutover-${digest}.forward-recovery.json`);
  const attestationPath = path.join(auditRoot, `sales-cutover-${digest}.attestation.json`);
  const forward = await readStableJson(forwardPath, "sales forward-recovery evidence");
  const value = forward.value;
  if (value?.version !== "teruisi-sales-forward-recovery-v3" || value.status !== "completed"
    || value.cutoverId !== authority.cutoverId
    || value.djangoDeploymentManifestSha256 !== authority.djangoDeploymentManifestSha256
    || value.workerReleaseManifestSha256 !== authority.workerReleaseManifestSha256
    || value.workerReleaseId !== authority.workerReleaseId
    || value.workerGuardReceiptSha256 !== authority.guardReceiptSha256
    || value.workerSourceD1PathSha256 !== authority.sourceD1PathSha256
    || value.workerPersistRootPathSha256 !== authority.persistRootPathSha256
    || value.workerAuthoritySha256 !== authority.rawSha256
    || !hex64.test(value.attestationPayloadSha256 ?? "")) {
    fail("sales forward-recovery evidence 未绑定 bootstrap authority completed tuple");
  }
  const attestation = await readStableJson(attestationPath, "sales cutover attestation evidence");
  if (attestation.value?.schemaVersion !== "sales-cutover-attestation-v2"
    || attestation.value.payloadSha256 !== value.attestationPayloadSha256
    || sha256Canonical(attestation.value.payload) !== attestation.value.payloadSha256
    || attestation.value.payload?.cutoverId !== authority.cutoverId) {
    fail("sales cutover attestation evidence 无效");
  }
  return {
    attestationPayloadSha256: value.attestationPayloadSha256,
    attestationFileSha256: attestation.sha256,
    forwardRecoverySha256: forward.sha256,
  };
}

async function verifyReleaseWithOwnVerifier(chain, { allowTestRuntimeRoot = false, processPolicy = "stopped" } = {}) {
  if (!["stopped", "stopped-or-exact-release"].includes(processPolicy)) fail("Invalid predecessor process policy");
  const releaseTool = path.join(path.dirname(chain.headManifestPath), "tools", "worker-local-release.mjs");
  await assertRegularFile(releaseTool, "predecessor self-verifier");
  const args = [
    releaseTool, "verify", "--manifest", chain.headManifestPath,
    "--approved-manifest-sha256", chain.head.manifestSha256,
    "--expected-source-d1-path-sha256", chain.bootstrap.authority.sourceD1PathSha256,
    "--expected-persist-root-path-sha256", chain.bootstrap.authority.persistRootPathSha256,
    "--expected-host", workerHost, "--expected-port", String(workerPort),
    "--require-sales-retired-code-receipt", "--process-policy", processPolicy, "--json",
  ];
  if (allowTestRuntimeRoot) args.push("--allow-test-runtime-root");
  await runProcess(process.execPath, args, { cwd: path.dirname(chain.headManifestPath), label: "predecessor immutable release 自校验" });
}

async function entrypointPlanFromReceipts(predecessorManifest, candidateManifest) {
  const predecessorGuard = await readGuardReceipt(predecessorManifest, "predecessor");
  const candidateGuard = await readGuardReceipt(candidateManifest, "candidate");
  await readActivationFence(predecessorManifest, predecessorGuard, "predecessor");
  await readActivationFence(candidateManifest, candidateGuard, "candidate", { required: true });
  const predecessor = new Map(predecessorGuard.receipt.entrypoints.map((item) => [item.relativePath, item.sha256]));
  const candidate = new Map(candidateGuard.receipt.entrypoints.map((item) => [item.relativePath, item.sha256]));
  for (const relativePath of predecessor.keys()) {
    if (!candidate.has(relativePath)) fail(`candidate guard 不得移除受保护入口：${relativePath}`);
  }
  const items = [];
  for (const [relativePath, candidateSha256] of candidate.entries()) {
    const predecessorSha256 = predecessor.get(relativePath) ?? null;
    items.push({ relativePath, predecessorSha256, candidateSha256 });
  }
  if (predecessor.has(activationFenceRelativePath)
      && predecessor.get(activationFenceRelativePath) === candidate.get(activationFenceRelativePath)) {
    fail("candidate activation fence 必须随 immutable build identity 唯一变化");
  }
  if (!items.some((item) => item.predecessorSha256 !== null && item.predecessorSha256 !== item.candidateSha256)) {
    fail("candidate 没有可先行使 predecessor guard 失败关闭的受保护入口变化");
  }
  return items;
}

async function verifyCandidateGuardPlanPreflight(candidateManifest) {
  const guard = await readGuardReceipt(candidateManifest, "candidate plan preflight");
  const keyFiles = candidateManifest.manifest.artifacts?.keyFiles;
  if (!Array.isArray(keyFiles)) fail("candidate plan preflight keyFiles 无效");
  const keyFilesByPath = new Map();
  for (const item of keyFiles) {
    assertExactKeys(item, ["relativePath", "sha256"], "candidate plan preflight keyFile");
    if (typeof item.relativePath !== "string" || !hex64.test(item.sha256 ?? "") || keyFilesByPath.has(item.relativePath)) {
      fail("candidate plan preflight keyFile 身份无效");
    }
    keyFilesByPath.set(item.relativePath, item.sha256);
  }
  const candidateRoot = path.dirname(candidateManifest.manifestPath);
  for (const item of guard.receipt.entrypoints) {
    if (keyFilesByPath.get(item.relativePath) !== item.sha256) {
      fail(`candidate plan preflight keyFiles 未绑定 guard entrypoint：${item.relativePath}`);
    }
    const source = path.join(candidateRoot, ...item.relativePath.split("/"));
    await assertRegularFile(source, `candidate entrypoint source ${item.relativePath}`);
    if (sha256Bytes(await readFile(source)) !== item.sha256) {
      fail(`candidate entrypoint source SHA-256 无效：${item.relativePath}`);
    }
  }
}

export async function buildEntrypointPlan(predecessorManifest, candidateManifest) {
  await verifyCandidateGuardPlanPreflight(candidateManifest);
  const items = await entrypointPlanFromReceipts(predecessorManifest, candidateManifest);
  const protectedRoot = candidateManifest.manifest.runtime.protectedSourceRoot;
  for (const { relativePath, predecessorSha256, candidateSha256 } of items) {
    const target = path.join(protectedRoot, ...relativePath.split("/"));
    if (await exists(target)) {
      await assertRegularFile(target, `protected entrypoint ${relativePath}`);
      const installedSha256 = sha256Bytes(await readFile(target));
      if (![predecessorSha256, candidateSha256].includes(installedSha256)) {
        fail(`protected entrypoint 既不属于 predecessor 也不属于 candidate：${relativePath}`);
      }
    } else if (predecessorSha256 !== null) {
      fail(`predecessor protected entrypoint 缺失：${relativePath}`);
    }
  }
  return items;
}

export function rotationPlanPayload({ createdAt, chain, candidate, lineage, protectedEntrypoints }) {
  if (typeof createdAt !== "string" || Number.isNaN(Date.parse(createdAt))) fail("rotation plan createdAt 无效");
  validateBinding(chain.head, "rotation plan predecessor");
  validateBinding(candidate, "rotation plan candidate");
  if (!Array.isArray(protectedEntrypoints) || protectedEntrypoints.length < 1) fail("rotation plan protected entrypoints 为空");
  return withPayloadSha256({
    version: rotationPlanVersion,
    status: "candidate_verified",
    createdAt,
    predecessor: chain.head,
    candidate,
    predecessorSequence: chain.successorCount,
    predecessorChainStateSha256: chain.chainStateSha256,
    lineage,
    protectedEntrypoints,
  }, "payloadSha256");
}

function validateRotationPlan(plan, bootstrap, label) {
  assertExactKeys(plan, [
    "version", "status", "createdAt", "predecessor", "candidate", "predecessorSequence",
    "predecessorChainStateSha256", "lineage", "protectedEntrypoints", "payloadSha256",
  ], label);
  validatePayload(plan, "payloadSha256", label);
  if (plan.version !== rotationPlanVersion || plan.status !== "candidate_verified"
    || typeof plan.createdAt !== "string" || Number.isNaN(Date.parse(plan.createdAt))
    || !Number.isSafeInteger(plan.predecessorSequence) || plan.predecessorSequence < 0
    || !hex64.test(plan.predecessorChainStateSha256 ?? "")) fail(`${label}身份无效`);
  validateBinding(plan.predecessor, `${label} predecessor`);
  validateBinding(plan.candidate, `${label} candidate`);
  validateLineage(plan.lineage, bootstrap, `${label} lineage`);
  if (!Array.isArray(plan.protectedEntrypoints) || plan.protectedEntrypoints.length < 1) fail(`${label}入口计划无效`);
  const paths = new Set();
  for (const item of plan.protectedEntrypoints) {
    assertExactKeys(item, ["relativePath", "predecessorSha256", "candidateSha256"], `${label} entrypoint`);
    if (typeof item.relativePath !== "string" || item.relativePath.includes("\\") || path.posix.isAbsolute(item.relativePath)
      || item.relativePath.split("/").some((part) => !part || part === "." || part === "..")
      || paths.has(item.relativePath)
      || !(item.predecessorSha256 === null || hex64.test(item.predecessorSha256 ?? ""))
      || !hex64.test(item.candidateSha256 ?? "")) fail(`${label} entrypoint 无效`);
    paths.add(item.relativePath);
  }
  return plan;
}

export async function publishRotationPlan(runtimeRoot, plan) {
  const raw = Buffer.from(`${canonicalJson(plan)}\n`, "utf8");
  const sha256 = sha256Bytes(raw);
  const root = path.join(runtimeRoot, "state", rotationPlanDirectoryName);
  if (!(await exists(root))) await mkdir(root);
  await assertEntityDirectory(root, "rotation plan 根");
  const published = await publishCanonicalWithSidecar(path.join(root, `${sha256}.json`), plan, "rotation plan");
  return { ...published, planSha256: sha256 };
}

async function loadApprovedPlan(runtimeRoot, approvedPlanSha256, bootstrap) {
  if (!hex64.test(approvedPlanSha256 ?? "")) fail("approved plan SHA-256 无效");
  const target = path.join(runtimeRoot, "state", rotationPlanDirectoryName, `${approvedPlanSha256}.json`);
  const read = await readCanonical(target, "approved rotation plan");
  if (read.sha256 !== approvedPlanSha256) fail("approved rotation plan 原始 SHA-256 不一致");
  await assertRegularFile(`${target}.sha256`, "approved rotation plan sidecar");
  const sidecar = await readFile(`${target}.sha256`);
  if (!sidecar.equals(Buffer.from(`${read.sha256}\n`, "ascii"))) fail("approved rotation plan sidecar 无效");
  return { plan: validateRotationPlan(read.value, bootstrap, "approved rotation plan"), path: target, sha256: read.sha256 };
}

function successorFromPlan(plan, approvedPlanSha256) {
  return successorPayload({
    sequence: plan.predecessorSequence + 1,
    predecessor: plan.predecessor,
    successor: plan.candidate,
    lineage: plan.lineage,
    approvedPlanSha256,
    activatedAt: plan.createdAt,
  });
}

export async function publishSuccessorRecord(runtimeRoot, record, bootstrap) {
  validateSuccessor(record, bootstrap, "approved successor record");
  const root = path.join(runtimeRoot, "state", successorDirectoryName);
  if (!(await exists(root))) await mkdir(root);
  await assertEntityDirectory(root, "Worker successor 根");
  return publishCanonicalWithSidecar(
    path.join(root, `${record.predecessor.bindingSha256}.json`),
    record,
    "Worker successor record",
  );
}

export async function atomicInstallFile(source, target, expectedSha256) {
  await assertRegularFile(source, "candidate protected entrypoint");
  await assertEntityDirectory(path.dirname(target), "protected entrypoint parent");
  const raw = await readFile(source);
  if (sha256Bytes(raw) !== expectedSha256) fail("candidate protected entrypoint 源哈希变化");
  const temporary = `${target}.rotation-${randomUUID()}.tmp`;
  let handle;
  try {
    handle = await open(temporary, "wx");
    await handle.writeFile(raw);
    await handle.sync();
    await handle.close();
    handle = null;
    await rename(temporary, target);
  } finally {
    if (handle) await handle.close().catch(() => {});
    await rm(temporary, { force: true }).catch(() => {});
  }
  if (sha256Bytes(await readFile(target)) !== expectedSha256) fail("candidate protected entrypoint 安装后回读失败");
}

async function installProtectedEntrypoints(plan, candidateManifest, { afterEntrypointInstalled = null } = {}) {
  const protectedRoot = candidateManifest.manifest.runtime.protectedSourceRoot;
  const candidateRoot = path.dirname(candidateManifest.manifestPath);
  await assertEntityDirectory(protectedRoot, "protected source root");
  const activationFenceParent = path.join(protectedRoot, path.dirname(activationFenceRelativePath));
  if (!(await exists(activationFenceParent))) await mkdir(activationFenceParent);
  await assertEntityDirectory(activationFenceParent, "protected activation fence parent");
  const fence = plan.protectedEntrypoints.find((item) => item.relativePath === activationFenceRelativePath
    && item.predecessorSha256 !== null && item.predecessorSha256 !== item.candidateSha256)
    ?? plan.protectedEntrypoints.find((item) => item.predecessorSha256 !== null
      && item.predecessorSha256 !== item.candidateSha256);
  if (!fence) fail("approved plan 缺少 predecessor guard fail-closed 安装栅栏");
  const ordered = [fence, ...plan.protectedEntrypoints.filter((item) => item !== fence)];
  for (const item of ordered) {
    const target = path.join(protectedRoot, ...item.relativePath.split("/"));
    const installedExists = await exists(target);
    if (installedExists) {
      await assertRegularFile(target, `installed entrypoint ${item.relativePath}`);
      const installedSha256 = sha256Bytes(await readFile(target));
      if (installedSha256 === item.candidateSha256) continue;
      if (installedSha256 !== item.predecessorSha256) fail(`installed entrypoint CAS 冲突：${item.relativePath}`);
    } else if (item.predecessorSha256 !== null) {
      fail(`installed predecessor entrypoint 缺失：${item.relativePath}`);
    }
    const source = path.join(candidateRoot, ...item.relativePath.split("/"));
    await atomicInstallFile(source, target, item.candidateSha256);
    if (afterEntrypointInstalled) await afterEntrypointInstalled(item);
  }
}

function startupBindingForPlan(plan, candidateManifest) {
  const service = plan.protectedEntrypoints.find((item) => item.relativePath === "tools/worker-local-service.ps1");
  if (!service || !hex64.test(service.candidateSha256 ?? "")) fail("startup service entrypoint binding 无效");
  return withPayloadSha256({
    status: "verified",
    releaseId: plan.candidate.releaseId,
    manifestSha256: plan.candidate.manifestSha256,
    serviceEntrypointSha256: service.candidateSha256,
    manifestPathSha256: windowsPathSha256(candidateManifest.manifestPath),
  }, "bindingSha256");
}

async function publishConsumption(runtimeRoot, planRead, successorRead, startupBinding) {
  const value = withPayloadSha256({
    version: rotationConsumptionVersion,
    status: "activated",
    approvedPlanSha256: planRead.sha256,
    successorRecordSha256: successorRead.sha256,
    predecessorBindingSha256: planRead.plan.predecessor.bindingSha256,
    successorBindingSha256: planRead.plan.candidate.bindingSha256,
    startupBinding,
    activatedAt: planRead.plan.createdAt,
  }, "payloadSha256");
  const root = path.join(runtimeRoot, "state", rotationConsumptionDirectoryName);
  if (!(await exists(root))) await mkdir(root);
  await assertEntityDirectory(root, "rotation consumption 根");
  return publishCanonicalWithSidecar(path.join(root, `${planRead.sha256}.json`), value, "rotation consumption");
}

async function acquireRotationLock() {
  const server = createServer();
  server.unref();
  await new Promise((resolve, reject) => {
    server.once("error", (error) => reject(new Error(`Worker rotation 唯一锁不可用：${error?.code ?? "unknown"}`)));
    server.listen(rotationLockPipe, resolve);
  });
  return () => new Promise((resolve) => server.close(() => resolve()));
}

async function acquireWorkerServiceMutex() {
  const script = [
    "$ErrorActionPreference='Stop'",
    "$mutex=[System.Threading.Mutex]::new($false,'Local\\TERUISI.Worker.LocalService.v1')",
    "$acquired=$false",
    "try {",
    "  try { $acquired=$mutex.WaitOne([TimeSpan]::FromMinutes(30)) } catch [System.Threading.AbandonedMutexException] { $acquired=$true }",
    "  if (-not $acquired) { throw 'service mutex timeout' }",
    "  [Console]::Out.WriteLine('LOCKED')",
    "  [Console]::Out.Flush()",
    "  [void][Console]::In.ReadLine()",
    "} finally {",
    "  if ($acquired) { $mutex.ReleaseMutex() }",
    "  $mutex.Dispose()",
    "}",
  ].join("\n");
  const child = spawn("powershell.exe", [
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script,
  ], { shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  let stdout = "";
  let stderr = "";
  let settled = false;
  let childClosed = false;
  let childExitCode = null;
  child.on("close", (code) => {
    childClosed = true;
    childExitCode = code;
  });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch {}
      reject(new Error("Worker service mutex 获取超时"));
    }, 30 * 60 * 1000);
    timeout.unref();
    const rejectOnce = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(error);
    };
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
      if (stdout.length > 128) return rejectOnce(new Error("Worker service mutex 输出无效"));
      if (!settled && /(?:^|\r?\n)LOCKED\r?\n/.test(stdout)) {
        settled = true;
        clearTimeout(timeout);
        resolve();
      }
    });
    child.stderr.on("data", (chunk) => {
      if (stderr.length < 1_024) stderr += chunk.toString("utf8");
    });
    child.once("error", () => rejectOnce(new Error("Worker service mutex helper 启动失败")));
    child.once("close", (code) => {
      if (!settled) rejectOnce(new Error(`Worker service mutex helper 提前退出：${code ?? "unknown"}; ${stderr.trim()}`));
    });
  });
  return () => new Promise((resolve, reject) => {
    if (childClosed) {
      if (childExitCode === 0) resolve();
      else reject(new Error(`Worker service mutex helper 已提前退出：${childExitCode ?? "unknown"}`));
      return;
    }
    child.once("close", (code) => code === 0 ? resolve() : reject(new Error(`Worker service mutex helper 释放失败：${code ?? "unknown"}`)));
    child.stdin.end("\n");
  });
}

async function assertStoppedForCandidate(context, allowTestRuntimeRoot) {
  await verifyWorkerRelease({
    manifestPath: context.manifestPath,
    approvedManifestSha256: context.manifestSha256,
    expectedSourceD1PathSha256: context.manifest.runtime.sourceD1PathSha256,
    expectedPersistRootPathSha256: context.manifest.runtime.persistRootPathSha256,
    expectedHost: workerHost,
    expectedPort: workerPort,
    requireSalesRetiredCodeReceipt: true,
    processPolicy: "stopped",
    allowTestRuntimeRoot,
  });
}

async function invokeStartupShortcutAction(candidateManifest, action) {
  if (!["InstallStartup", "VerifyStartup"].includes(action)) fail("startup shortcut action 无效");
  const servicePath = path.join(path.dirname(candidateManifest.manifestPath), "tools", "worker-local-service.ps1");
  await assertRegularFile(servicePath, "candidate startup service entrypoint");
  const { stdout } = await runProcess("powershell.exe", [
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", servicePath,
    "-Action", action, "-ManifestPath", candidateManifest.manifestPath, "-Json",
  ], { label: `effective head startup shortcut ${action}` });
  const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length !== 1) fail("startup shortcut 安装回读输出不唯一");
  let result;
  try {
    result = JSON.parse(lines[0]);
  } catch {
    fail("startup shortcut 安装回读不是 JSON");
  }
  assertExactKeys(result, ["status", "version", "releaseId", "manifestSha256", "startupVerified"], "startup shortcut 安装回读");
  const acceptedStatuses = action === "InstallStartup" ? ["installed", "already_installed"] : ["verified"];
  if (!acceptedStatuses.includes(result.status)
      || result.version !== "teruisi-local-worker-status-v1"
      || result.releaseId !== candidateManifest.manifest.releaseId
      || result.manifestSha256 !== candidateManifest.manifestSha256
      || result.startupVerified !== true) fail("startup shortcut 未精确重绑 effective head");
  return result;
}

export async function verifyPreparationHeadUnchanged(before, after, verifyHead) {
  if (![before?.chainStateSha256, after?.chainStateSha256, before?.head?.bindingSha256, after?.head?.bindingSha256]
    .every((value) => typeof value === "string" && hex64.test(value)) || typeof verifyHead !== "function") {
    fail("Online preparation requires complete predecessor bindings");
  }
  if (after.chainStateSha256 !== before.chainStateSha256 || after.head.bindingSha256 !== before.head.bindingSha256) {
    fail("Online preparation predecessor changed; candidate cannot be planned");
  }
  await verifyHead(after);
  return { status: "preparation_only", predecessorBindingSha256: before.head.bindingSha256 };
}

export async function planWorkerReleaseRotation({ now = new Date(), allowTestRuntimeRoot = false, adoptD1ControlRetirement = false, prepareOnline = false } = {}) {
  if (prepareOnline && adoptD1ControlRetirement) fail("Initial retirement adoption still requires a stopped release");
  const releaseLock = await acquireRotationLock();
  let serviceLock;
  try {
    serviceLock = await acquireWorkerServiceMutex();
    // A verifier update necessarily creates a short, stopped-only mixed state:
    // the source/protected copy can already be the candidate while the immutable
    // effective head is still the predecessor.  Resolve the append-only chain
    // without treating that intentional mismatch as an installed-head success;
    // the predecessor's immutable verifier below still proves it is stopped.
    const before = await resolveEffectiveReleaseChain({ allowTestRuntimeRoot, verifyInstalledHead: prepareOnline });
    await verifyReleaseWithOwnVerifier(before, { allowTestRuntimeRoot,
      processPolicy: prepareOnline ? "stopped-or-exact-release" : "stopped" });
    const evidence = await readCutoverEvidence({ ...before.bootstrap.authority, rawSha256: before.bootstrap.authoritySha256 });
    if (before.records.length > 0) {
      const established = before.records[before.records.length - 1].value.lineage;
      for (const field of ["attestationPayloadSha256", "attestationFileSha256", "forwardRecoverySha256"]) {
        if (evidence[field] !== established[field]) fail("cutover evidence 与已建立 successor lineage 不一致");
      }
    }
    let retirementProof;
    if (before.headManifest.artifacts.d1RetirementReceipt) {
      if (adoptD1ControlRetirement) fail("D1 control retirement has already been adopted");
      retirementProof = await readD1RetirementReceipt(before.headManifest, path.dirname(before.headManifestPath), {
        bootstrapAuthoritySha256: before.bootstrap.authoritySha256,
      });
    } else {
      if (!adoptD1ControlRetirement) fail("Initial D1 control retirement requires plan --adopt-d1-control-retirement");
      const { collectD1RetirementProof } = await import("./collect-d1-retirement-proof.mjs");
      retirementProof = await collectD1RetirementProof({ chain: before, sourceRoot: workerSourceRoot,
        djangoRuntimeRoot: djangoSalesRuntimeRoot, retainedEvidence: evidence, now });
    }
    // Do not block service recovery throughout npm/build/hash preparation.
    // Rotation remains serialized; each boundary reacquires the lifecycle lock
    // and validates the current installed predecessor with its own verifier.
    if (prepareOnline) { await serviceLock(); serviceLock = null; }
    const verifyPreparationPredecessor = prepareOnline ? async () => {
      const unlock = await acquireWorkerServiceMutex();
      try {
        const current = await resolveEffectiveReleaseChain({ allowTestRuntimeRoot, verifyInstalledHead: true });
        return await verifyPreparationHeadUnchanged(before, current, (head) =>
          verifyReleaseWithOwnVerifier(head, { allowTestRuntimeRoot, processPolicy: "stopped-or-exact-release" }));
      } finally { await unlock(); }
    } : undefined;
    const built = await buildWorkerReleaseCandidate({
      sourceRoot: workerSourceRoot,
      runtimeRoot: workerRuntimeRoot,
      devVarsSource: workerDevVarsSource,
      persistRoot: workerPersistRoot,
      sourceD1Path: before.headManifest.runtime.sourceD1Path,
      retirementProof,
      verifyPreparationPredecessor,
      now,
      allowTestRuntimeRoot,
    });
    if (prepareOnline) {
      serviceLock = await acquireWorkerServiceMutex();
      const current = await resolveEffectiveReleaseChain({ allowTestRuntimeRoot, verifyInstalledHead: true });
      await verifyPreparationHeadUnchanged(before, current, (head) =>
        verifyReleaseWithOwnVerifier(head, { allowTestRuntimeRoot, processPolicy: "stopped-or-exact-release" }));
    }
    const after = await resolveEffectiveReleaseChain({ allowTestRuntimeRoot, verifyInstalledHead: false });
    if (after.chainStateSha256 !== before.chainStateSha256 || after.head.bindingSha256 !== before.head.bindingSha256) {
      fail("rotation candidate 构建期间 effective head 发生变化；候选保留但不得生成计划");
    }
    const candidate = releaseBinding({
      releaseId: built.releaseId,
      manifestSha256: built.manifestSha256,
      guardReceiptSha256: (await readCanonical(
        path.join(path.dirname(built.manifestPath), "audit", "legacy-worker-guard-receipt.json"),
        "candidate guard receipt",
      )).sha256,
    });
    const candidateManifest = await readManifest(workerRuntimeRoot, candidate, "candidate");
    if (candidateManifest.manifest.runtime.sourceD1PathSha256 !== before.bootstrap.authority.sourceD1PathSha256
      || candidateManifest.manifest.runtime.persistRootPathSha256 !== before.bootstrap.authority.persistRootPathSha256
      || candidateManifest.manifest.runtime.protectedSourceRootPathSha256
        !== before.headManifest.runtime.protectedSourceRootPathSha256) fail("candidate 未继承 authority 固定路径");
    const predecessorManifestContext = {
      manifestPath: before.headManifestPath,
      manifestSha256: before.head.manifestSha256,
      manifest: before.headManifest,
    };
    const protectedEntrypoints = await buildEntrypointPlan(predecessorManifestContext, candidateManifest);
    const lineage = {
      bootstrapCurrentPointerSha256: before.bootstrap.pointerSha256,
      bootstrapAuthoritySha256: before.bootstrap.authoritySha256,
      cutoverId: before.bootstrap.authority.cutoverId,
      djangoDeploymentManifestSha256: before.bootstrap.authority.djangoDeploymentManifestSha256,
      sourceD1PathSha256: before.bootstrap.authority.sourceD1PathSha256,
      persistRootPathSha256: before.bootstrap.authority.persistRootPathSha256,
      ...evidence,
      salesRetirementMigrationSha256,
      predecessorContractReceiptSha256: predecessorManifestContext.manifest.artifacts.contractReceipt.sha256,
      successorContractReceiptSha256: candidateManifest.manifest.artifacts.contractReceipt.sha256,
    };
    const plan = rotationPlanPayload({ createdAt: now.toISOString(), chain: before, candidate, lineage, protectedEntrypoints });
    const publication = await publishRotationPlan(workerRuntimeRoot, plan);
    return {
      status: "planned",
      version: rotationPlanVersion,
      planSha256: publication.planSha256,
      planPath: publication.path,
      predecessorReleaseId: plan.predecessor.releaseId,
      candidateReleaseId: plan.candidate.releaseId,
      candidateManifestSha256: plan.candidate.manifestSha256,
      candidateGuardReceiptSha256: plan.candidate.guardReceiptSha256,
      cutoverId: plan.lineage.cutoverId,
    };
  } finally {
    try {
      if (serviceLock) await serviceLock();
    } finally {
      await releaseLock();
    }
  }
}

const cutoverEvidenceFields = Object.freeze([
  "attestationPayloadSha256", "attestationFileSha256", "forwardRecoverySha256",
]);

async function validateApprovedRotationBeforeMutation({
  runtimeRoot,
  approvedPlanSha256,
  allowTestRuntimeRoot,
  readCurrentCutoverEvidence,
}) {
  const bootstrap = await readBootstrap(runtimeRoot, { allowTestRuntimeRoot });
  const planRead = await loadApprovedPlan(runtimeRoot, approvedPlanSha256, bootstrap);
  const record = successorFromPlan(planRead.plan, planRead.sha256);
  const successorPath = path.join(runtimeRoot, "state", successorDirectoryName, `${record.predecessor.bindingSha256}.json`);
  const recordExists = await exists(successorPath);
  const sidecarExists = await exists(`${successorPath}.sha256`);
  if (!recordExists && sidecarExists) fail("Worker successor sidecar 孤立存在，拒绝恢复");
  const acceptedIncompleteRecord = recordExists && !sidecarExists ? record : null;
  const chain = await resolveEffectiveReleaseChainInternal({
    runtimeRoot,
    allowTestRuntimeRoot,
    verifyInstalledHead: false,
    acceptedIncompleteRecord,
  });
  const currentEvidence = await readCurrentCutoverEvidence({
    ...bootstrap.authority,
    rawSha256: bootstrap.authoritySha256,
  });
  for (const field of cutoverEvidenceFields) {
    if (!hex64.test(currentEvidence?.[field] ?? "") || planRead.plan.lineage[field] !== currentEvidence[field]) {
      fail("approved rotation plan 未绑定当前真实 cutover evidence");
    }
    for (const existing of chain.records) {
      if (existing.value.lineage[field] !== currentEvidence[field]) {
        fail("已有 successor lineage 与当前真实 cutover evidence 不一致");
      }
    }
  }

  let state;
  let candidateManifest;
  let successorRead = null;
  if (chain.head.bindingSha256 === planRead.plan.candidate.bindingSha256) {
    const last = chain.records[chain.records.length - 1];
    if (!last) fail("Worker successor candidate 重复或形成环");
    if (path.resolve(last.path) !== path.resolve(successorPath)) fail("已激活 successor 不是批准计划的精确链尾");
    successorRead = await readCanonical(successorPath, "approved successor recovery record");
    if (canonicalJson(successorRead.value) !== canonicalJson(record)) fail("已激活 successor 与批准元组不一致");
    candidateManifest = await readManifest(runtimeRoot, planRead.plan.candidate, "approved candidate recovery");
    state = sidecarExists ? "activated" : "record_incomplete";
  } else {
    if (recordExists
      || chain.head.bindingSha256 !== planRead.plan.predecessor.bindingSha256
      || chain.chainStateSha256 !== planRead.plan.predecessorChainStateSha256
      || chain.successorCount !== planRead.plan.predecessorSequence) {
      fail("approved rotation plan 的 predecessor CAS 已失效");
    }
    const headManifest = {
      manifestPath: chain.headManifestPath,
      manifestSha256: chain.head.manifestSha256,
      manifest: chain.headManifest,
    };
    const raw = Buffer.from(`${canonicalJson(record)}\n`, "utf8");
    const transition = await validateSuccessorTransition({
      runtimeRoot,
      bootstrap,
      records: chain.records,
      head: chain.head,
      headManifest,
      item: { value: record, rawSha256: sha256Bytes(raw), path: successorPath },
      seenBindings: new Set([bootstrap.binding.bindingSha256, ...chain.records.map((item) => item.value.successor.bindingSha256)]),
    });
    candidateManifest = transition.successorManifest;
    state = "pending";
  }
  return { bootstrap, planRead, record, successorPath, successorRead, chain, candidateManifest, state };
}

async function applyApprovedRotationPlanCore({
  runtimeRoot,
  approvedPlanSha256,
  allowTestRuntimeRoot,
  dependencies,
}) {
  let validated = await validateApprovedRotationBeforeMutation({
    runtimeRoot, approvedPlanSha256, allowTestRuntimeRoot,
    readCurrentCutoverEvidence: dependencies.readCurrentCutoverEvidence,
  });
  await readD1RetirementReceipt(validated.candidateManifest.manifest, path.dirname(validated.candidateManifest.manifestPath), {
    bootstrapAuthoritySha256: validated.bootstrap.authoritySha256,
  });
  await dependencies.verifyHeadStopped(validated.chain, { allowTestRuntimeRoot });
  await dependencies.assertCandidateStopped(validated.candidateManifest, allowTestRuntimeRoot);
  const beforeMutation = await validateApprovedRotationBeforeMutation({
    runtimeRoot, approvedPlanSha256, allowTestRuntimeRoot,
    readCurrentCutoverEvidence: dependencies.readCurrentCutoverEvidence,
  });
  if (beforeMutation.state !== validated.state
      || beforeMutation.chain.chainStateSha256 !== validated.chain.chainStateSha256
      || beforeMutation.planRead.sha256 !== validated.planRead.sha256) {
    fail("approved rotation tuple 在 apply 前最终 CAS 复核中发生变化");
  }
  validated = beforeMutation;
  await dependencies.verifyHeadStopped(validated.chain, { allowTestRuntimeRoot });
  await dependencies.assertCandidateStopped(validated.candidateManifest, allowTestRuntimeRoot);
  if (validated.state === "pending" && validated.candidateManifest.manifest.artifacts.d1RetirementReceipt
      && !validated.chain.headManifest.artifacts.d1RetirementReceipt) {
    if (!dependencies.verifyD1Adoption) fail("Initial D1 adoption requires independent source revalidation");
    await dependencies.verifyD1Adoption(validated);
  }
  await installProtectedEntrypoints(validated.planRead.plan, validated.candidateManifest, {
    afterEntrypointInstalled: dependencies.afterEntrypointInstalled,
  });
  await dependencies.assertCandidateStopped(validated.candidateManifest, allowTestRuntimeRoot);
  await verifyInstalledGuardEntrypoints(validated.candidateManifest, "approved candidate prepublication");

  let successorPublication;
  if (validated.state === "pending" || validated.state === "record_incomplete") {
    successorPublication = await publishSuccessorRecord(runtimeRoot, validated.record, validated.bootstrap);
  } else {
    successorPublication = { path: validated.successorPath, sha256: validated.successorRead.sha256 };
  }
  const chain = await resolveEffectiveReleaseChain({ runtimeRoot, allowTestRuntimeRoot, verifyInstalledHead: true });
  if (chain.head.bindingSha256 !== validated.planRead.plan.candidate.bindingSha256) {
    fail("successor 发布后 effective head 未切换到 candidate");
  }
  await dependencies.installAndVerifyStartup(validated.candidateManifest);
  const startupBinding = startupBindingForPlan(validated.planRead.plan, validated.candidateManifest);
  const consumption = await publishConsumption(
    runtimeRoot, validated.planRead, successorPublication, startupBinding,
  );
  return {
    status: validated.state === "pending" ? "activated" : "already_activated",
    version: successorVersion,
    planSha256: validated.planRead.sha256,
    predecessorReleaseId: validated.planRead.plan.predecessor.releaseId,
    releaseId: validated.planRead.plan.candidate.releaseId,
    manifestPath: validated.candidateManifest.manifestPath,
    manifestSha256: validated.planRead.plan.candidate.manifestSha256,
    successorSha256: successorPublication.sha256,
    consumptionSha256: consumption.sha256,
    startupBindingSha256: startupBinding.bindingSha256,
  };
}

/** @param {{ runtimeRoot: string, approvedPlanSha256: string, cutoverEvidence: object, testDependencies?: object }} options */
export async function applyApprovedRotationPlanForTest({
  runtimeRoot,
  approvedPlanSha256,
  cutoverEvidence,
  testDependencies = {},
} = {}) {
  if (typeof runtimeRoot !== "string" || !path.win32.isAbsolute(runtimeRoot)
      || canonicalWindowsPath(runtimeRoot) === canonicalWindowsPath(workerRuntimeRoot)) {
    fail("test apply 必须使用非 production 的绝对临时 runtime");
  }
  for (const field of cutoverEvidenceFields) if (!hex64.test(cutoverEvidence?.[field] ?? "")) fail("test cutover evidence 无效");
  const noOp = async () => {};
  const dependencies = {
    readCurrentCutoverEvidence: async () => cutoverEvidence,
    verifyD1Adoption: testDependencies.verifyD1Adoption ?? noOp,
    verifyHeadStopped: testDependencies.verifyHeadStopped ?? noOp,
    assertCandidateStopped: testDependencies.assertCandidateStopped ?? noOp,
    installAndVerifyStartup: testDependencies.installAndVerifyStartup ?? noOp,
    afterEntrypointInstalled: testDependencies.afterEntrypointInstalled ?? null,
  };
  for (const [name, value] of Object.entries(dependencies)) {
    if (name !== "afterEntrypointInstalled" && typeof value !== "function") fail(`test dependency ${name} 无效`);
  }
  if (dependencies.afterEntrypointInstalled !== null && typeof dependencies.afterEntrypointInstalled !== "function") {
    fail("test dependency afterEntrypointInstalled 无效");
  }
  return applyApprovedRotationPlanCore({
    runtimeRoot: path.resolve(runtimeRoot), approvedPlanSha256, allowTestRuntimeRoot: true, dependencies,
  });
}

export async function applyApprovedRotationPlan({ approvedPlanSha256, allowTestRuntimeRoot = false } = {}) {
  if (allowTestRuntimeRoot) fail("production apply 不接受 test runtime 覆盖");
  const releaseLock = await acquireRotationLock();
  let serviceLock;
  try {
    serviceLock = await acquireWorkerServiceMutex();
    const dependencies = {
      readCurrentCutoverEvidence: readCutoverEvidence,
      verifyD1Adoption: async (validated) => {
        const { collectD1RetirementProof, sameAdoptionEvidence } = await import("./collect-d1-retirement-proof.mjs");
        const expected = await readD1RetirementReceipt(validated.candidateManifest.manifest, path.dirname(validated.candidateManifest.manifestPath));
        const retainedEvidence = await readCutoverEvidence({ ...validated.bootstrap.authority, rawSha256: validated.bootstrap.authoritySha256 });
        const fresh = await collectD1RetirementProof({ chain: validated.chain,
          sourceRoot: path.join(path.dirname(validated.candidateManifest.manifestPath), "source-snapshot"),
          djangoRuntimeRoot: djangoSalesRuntimeRoot, retainedEvidence });
        if (!sameAdoptionEvidence(expected, fresh)) fail("D1 adoption evidence changed after the approved plan");
      },
      verifyHeadStopped: verifyReleaseWithOwnVerifier,
      assertCandidateStopped: assertStoppedForCandidate,
      afterEntrypointInstalled: null,
      installAndVerifyStartup: async (candidateManifest) => {
        const releaseHeldMutex = serviceLock;
        serviceLock = null;
        await releaseHeldMutex();
        await invokeStartupShortcutAction(candidateManifest, "InstallStartup");
        serviceLock = await acquireWorkerServiceMutex();
        return invokeStartupShortcutAction(candidateManifest, "VerifyStartup");
      },
    };
    return await applyApprovedRotationPlanCore({
      runtimeRoot: workerRuntimeRoot, approvedPlanSha256, allowTestRuntimeRoot: false, dependencies,
    });
  } finally {
    try {
      if (serviceLock) await serviceLock();
    } finally {
      await releaseLock();
    }
  }
}

function parseCli(argv) {
  if (argv.length === 0 || !["plan", "apply", "resolve"].includes(argv[0])) fail("只支持 plan/apply/resolve");
  const command = argv[0];
  const values = new Map();
  const flags = new Set();
  for (let index = 1; index < argv.length; index += 1) {
    const token = argv[index];
    if (["--json", "--allow-test-runtime-root", "--adopt-d1-control-retirement", "--prepare-online"].includes(token)) {
      if (flags.has(token)) fail(`参数重复：${token}`);
      flags.add(token);
      continue;
    }
    if (!token.startsWith("--") || index + 1 >= argv.length || argv[index + 1].startsWith("--") || values.has(token)) {
      fail(`参数无效或重复：${token}`);
    }
    values.set(token, argv[++index]);
  }
  return { command, values, flags };
}

async function main() {
  const { command, values, flags } = parseCli(process.argv.slice(2));
  const allowTestRuntimeRoot = flags.has("--allow-test-runtime-root");
  if (command !== "plan" && flags.has("--adopt-d1-control-retirement")) fail("D1 retirement adoption flag is plan-only");
  if (command !== "plan" && flags.has("--prepare-online")) fail("Online preparation flag is plan-only");
  let result;
  if (command === "plan") {
    if (values.size > 0 || allowTestRuntimeRoot) fail("production plan 不接受路径、命令或测试覆盖");
    result = await planWorkerReleaseRotation({ adoptD1ControlRetirement: flags.has("--adopt-d1-control-retirement"), prepareOnline: flags.has("--prepare-online") });
  } else if (command === "apply") {
    if (values.size !== 1 || !values.has("--approved-plan-sha256") || allowTestRuntimeRoot) {
      fail("production apply 只接受 --approved-plan-sha256");
    }
    result = await applyApprovedRotationPlan({ approvedPlanSha256: values.get("--approved-plan-sha256") });
  } else {
    const allowed = new Set(["--runtime-root"]);
    for (const name of values.keys()) if (!allowed.has(name)) fail(`resolve 不支持参数 ${name}`);
    if (values.has("--runtime-root") && !allowTestRuntimeRoot) fail("production resolve 不接受 runtime 路径覆盖");
    result = await resolveEffectiveReleaseChain({
      runtimeRoot: values.get("--runtime-root") ?? workerRuntimeRoot,
      allowTestRuntimeRoot,
      verifyInstalledHead: true,
    });
    result = {
      status: result.status,
      version: result.version,
      releaseId: result.head.releaseId,
      manifestPath: result.headManifestPath,
      manifestSha256: result.head.manifestSha256,
      guardReceiptSha256: result.head.guardReceiptSha256,
      successorCount: result.successorCount,
      chainStateSha256: result.chainStateSha256,
    };
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === modulePath) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
