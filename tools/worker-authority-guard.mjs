import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { resolveEffectiveReleaseChain } from "./worker-local-release-rotation.mjs";
import { readD1RetirementReceipt } from "./d1-retirement-proof.mjs";

import {
  canonicalJson,
  canonicalWindowsPath,
  processReceiptVersion,
  releaseVersion,
  sha256Bytes,
  sha256Canonical,
  workerGuardCheckNames,
  workerGuardEntrypointPaths,
  workerGuardForbiddenScans,
  workerRuntimeRoot,
  windowsPathSha256,
} from "./worker-local-release.mjs";

export const salesAuthorityVersion = "teruisi-sales-postgresql-authority-v1";
export const salesAuthorityFileName = "sales-postgresql-authority.json";
export const salesAuthoritySidecarFileName = "sales-postgresql-authority.json.sha256";
export const workerGuardReceiptVersion = "teruisi-legacy-worker-guard-receipt-v1";

const hex64 = /^[0-9a-f]{64}$/;
const releaseIdPattern = /^\d{8}T\d{6}Z-[0-9a-f]{16}$/;

function fail(message) {
  throw new Error(message);
}

function exactKeys(value, expected, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${label}必须为 JSON 对象`);
  if (canonicalJson(Object.keys(value).sort()) !== canonicalJson([...expected].sort())) fail(`${label}字段集合无效`);
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

async function assertNoReparsePath(target, label) {
  const absolute = path.resolve(target);
  const parsed = path.parse(absolute);
  let current = parsed.root;
  for (const part of absolute.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const info = await lstat(current);
    if (info.isSymbolicLink()) fail(`${label}不得包含重解析点`);
  }
  const resolved = await realpath(absolute);
  if (canonicalWindowsPath(resolved) !== canonicalWindowsPath(absolute)) fail(`${label}真实路径不一致`);
}

async function readCanonical(target, label) {
  const info = await lstat(target);
  if (!info.isFile() || info.isSymbolicLink()) fail(`${label}必须是普通文件`);
  const raw = await readFile(target);
  if (raw.length === 0 || raw[0] === 0xef) fail(`${label}必须是 UTF-8 无 BOM canonical JSON`);
  let value;
  try {
    value = JSON.parse(raw.toString("utf8"));
  } catch {
    fail(`${label}不是有效 JSON`);
  }
  if (!raw.equals(Buffer.from(`${canonicalJson(value)}\n`, "utf8"))) fail(`${label}字节表示不唯一`);
  return { value, raw, sha256: sha256Bytes(raw) };
}

function validatePayload(value, field, label) {
  if (!hex64.test(value[field] ?? "")) fail(`${label}自哈希字段无效`);
  const core = { ...value };
  delete core[field];
  if (sha256Canonical(core) !== value[field]) fail(`${label}自哈希无效`);
}

export function authorityPaths(runtimeRoot = workerRuntimeRoot) {
  const stateRoot = path.join(runtimeRoot, "state");
  return {
    authorityPath: path.join(stateRoot, salesAuthorityFileName),
    sidecarPath: path.join(stateRoot, salesAuthoritySidecarFileName),
  };
}

export async function readSalesAuthority(runtimeRoot = workerRuntimeRoot) {
  const { authorityPath, sidecarPath } = authorityPaths(runtimeRoot);
  await assertNoReparsePath(runtimeRoot, "Worker runtime root");
  const stateRoot = path.dirname(authorityPath);
  await assertNoReparsePath(stateRoot, "Worker runtime state root");
  const stateInfo = await lstat(stateRoot);
  if (!stateInfo.isDirectory() || stateInfo.isSymbolicLink()) fail("Worker runtime state root 必须是实体目录");
  const authorityExists = await exists(authorityPath);
  const sidecarExists = await exists(sidecarPath);
  if (!authorityExists && !sidecarExists) return null;
  if (!authorityExists || !sidecarExists) fail("sales PostgreSQL authority sentinel/sidecar 不完整");
  await assertNoReparsePath(authorityPath, "sales authority sentinel");
  await assertNoReparsePath(sidecarPath, "sales authority sidecar");
  const authority = await readCanonical(authorityPath, "sales PostgreSQL authority sentinel");
  const sidecarInfo = await lstat(sidecarPath);
  if (!sidecarInfo.isFile() || sidecarInfo.isSymbolicLink()) fail("sales authority sidecar 必须是普通文件");
  const sidecar = await readFile(sidecarPath);
  if (!sidecar.equals(Buffer.from(`${authority.sha256}\n`, "ascii"))) fail("sales authority sidecar 与 sentinel 原始文件不一致");
  const value = authority.value;
  exactKeys(value, [
    "version", "domain", "authority", "cutoverId", "workerReleaseId", "workerReleaseManifestSha256",
    "djangoDeploymentManifestSha256", "guardReceiptSha256", "sourceD1PathSha256", "persistRootPathSha256", "payloadSha256",
  ], "sales PostgreSQL authority sentinel");
  validatePayload(value, "payloadSha256", "sales PostgreSQL authority sentinel");
  if (value.version !== salesAuthorityVersion || value.domain !== "sales" || value.authority !== "postgresql") fail("sales authority sentinel 身份无效");
  if (typeof value.cutoverId !== "string" || !/^[A-Za-z0-9._:-]{8,128}$/.test(value.cutoverId)) fail("sales authority cutoverId 无效");
  if (!releaseIdPattern.test(value.workerReleaseId ?? "")) fail("sales authority workerReleaseId 无效");
  for (const field of [
    "workerReleaseManifestSha256", "djangoDeploymentManifestSha256", "guardReceiptSha256", "sourceD1PathSha256", "persistRootPathSha256",
  ]) {
    if (!hex64.test(value[field] ?? "")) fail(`sales authority ${field}无效`);
  }
  return { ...value, rawSha256: authority.sha256 };
}

async function readCurrentRelease(runtimeRoot = workerRuntimeRoot) {
  const pointerPath = path.join(runtimeRoot, "current-deployment.json");
  if (!(await exists(pointerPath))) return null;
  await assertNoReparsePath(pointerPath, "Worker current pointer");
  const pointerRead = await readCanonical(pointerPath, "Worker current pointer");
  const pointer = pointerRead.value;
  exactKeys(pointer, ["version", "releaseId", "manifestRelativePath", "manifestSha256", "pointerPayloadSha256"], "Worker current pointer");
  validatePayload(pointer, "pointerPayloadSha256", "Worker current pointer");
  if (pointer.version !== "teruisi-local-worker-current-v1" || !releaseIdPattern.test(pointer.releaseId ?? "") || !hex64.test(pointer.manifestSha256 ?? "")) {
    fail("Worker current pointer 身份无效");
  }
  const expectedRelative = `releases/${pointer.releaseId}/deployment-manifest.json`;
  if (pointer.manifestRelativePath !== expectedRelative) fail("Worker current pointer 路径无效");
  const manifestPath = path.join(runtimeRoot, ...expectedRelative.split("/"));
  await assertNoReparsePath(manifestPath, "Worker current manifest");
  const manifestRead = await readCanonical(manifestPath, "Worker current manifest");
  if (manifestRead.sha256 !== pointer.manifestSha256) fail("Worker current manifest 哈希不一致");
  const manifest = manifestRead.value;
  exactKeys(manifest, [
    "version", "releaseId", "createdAt", "source", "build", "runtime", "artifacts", "processIdentity", "manifestPayloadSha256",
  ], "Worker current manifest");
  validatePayload(manifest, "manifestPayloadSha256", "Worker current manifest");
  if (manifest.version !== releaseVersion || manifest.releaseId !== pointer.releaseId) fail("Worker current manifest 身份无效");
  exactKeys(manifest.runtime, [
    "runtimeRootPathSha256", "releaseRootPathSha256", "persistRootPathSha256", "sourceD1PathSha256", "host", "port",
    "persistRoot", "sourceD1Path", "protectedSourceRoot", "protectedSourceRootPathSha256", "cliOverridesAllowed", "helperMode",
    "helperHost", "helperPort", "helperMutableRoot", "helperMutableRootPathSha256", "devVars",
  ], "Worker current manifest runtime");
  if (manifest.runtime.runtimeRootPathSha256 !== windowsPathSha256(runtimeRoot)
    || manifest.runtime.releaseRootPathSha256 !== windowsPathSha256(path.dirname(manifestPath))
    || manifest.runtime.persistRootPathSha256 !== windowsPathSha256(manifest.runtime.persistRoot)
    || manifest.runtime.sourceD1PathSha256 !== windowsPathSha256(manifest.runtime.sourceD1Path)
    || manifest.runtime.protectedSourceRootPathSha256 !== windowsPathSha256(manifest.runtime.protectedSourceRoot)
    || manifest.runtime.helperMutableRootPathSha256 !== windowsPathSha256(manifest.runtime.helperMutableRoot)
    || manifest.runtime.helperMode !== "supervisor_managed_immutable_bundle"
    || manifest.runtime.helperHost !== "127.0.0.1" || manifest.runtime.helperPort !== 5791
    || manifest.runtime.helperMutableRoot !== manifest.runtime.protectedSourceRoot
    || manifest.runtime.helperMutableRootPathSha256 !== manifest.runtime.protectedSourceRootPathSha256) {
    fail("Worker current manifest runtime 路径绑定无效");
  }
  return { manifestPath, manifestSha256: manifestRead.sha256, manifest };
}

async function readAndVerifyGuardReceipt(current) {
  const pointer = current.manifest.artifacts?.guardReceipt;
  exactKeys(pointer, ["version", "relativePath", "sha256"], "legacy Worker guard receipt pointer");
  if (pointer.version !== workerGuardReceiptVersion || pointer.relativePath !== "audit/legacy-worker-guard-receipt.json" || !hex64.test(pointer.sha256 ?? "")) {
    fail("legacy Worker guard receipt pointer 无效");
  }
  const releaseRoot = path.dirname(current.manifestPath);
  const receiptPath = path.join(releaseRoot, ...pointer.relativePath.split("/"));
  await assertNoReparsePath(receiptPath, "legacy Worker guard receipt");
  const receiptRead = await readCanonical(receiptPath, "legacy Worker guard receipt");
  if (receiptRead.sha256 !== pointer.sha256) fail("legacy Worker guard receipt 原始文件哈希无效");
  const receipt = receiptRead.value;
  exactKeys(receipt, [
    "version", "generatedAt", "sourceFingerprint", "status", "bindings", "checks", "entrypoints",
    "forbiddenLegacyDirectCommands", "receiptPayloadSha256",
  ], "legacy Worker guard receipt");
  validatePayload(receipt, "receiptPayloadSha256", "legacy Worker guard receipt");
  if (receipt.version !== workerGuardReceiptVersion || receipt.status !== "passed"
    || receipt.sourceFingerprint !== current.manifest.source.sourceFingerprint) fail("legacy Worker guard receipt 身份无效");
  exactKeys(receipt.bindings, [
    "protectedSourceRoot", "protectedSourceRootPathSha256", "persistRoot", "persistRootPathSha256", "sourceD1Path",
    "sourceD1PathSha256", "authorityRelativePath", "authoritySidecarRelativePath",
  ], "legacy Worker guard bindings");
  if (receipt.bindings.protectedSourceRootPathSha256 !== windowsPathSha256(receipt.bindings.protectedSourceRoot)
    || receipt.bindings.persistRootPathSha256 !== windowsPathSha256(receipt.bindings.persistRoot)
    || receipt.bindings.sourceD1PathSha256 !== windowsPathSha256(receipt.bindings.sourceD1Path)
    || receipt.bindings.sourceD1PathSha256 !== current.manifest.runtime.sourceD1PathSha256
    || receipt.bindings.persistRootPathSha256 !== current.manifest.runtime.persistRootPathSha256
    || receipt.bindings.protectedSourceRootPathSha256 !== current.manifest.runtime.protectedSourceRootPathSha256
    || receipt.bindings.authorityRelativePath !== "state/sales-postgresql-authority.json"
    || receipt.bindings.authoritySidecarRelativePath !== "state/sales-postgresql-authority.json.sha256") fail("legacy Worker guard bindings 无效");
  exactKeys(receipt.checks, workerGuardCheckNames, "legacy Worker guard checks");
  if (Object.values(receipt.checks).some((value) => value !== true)) fail("legacy Worker guard checks 未全部通过");
  if (!Array.isArray(receipt.entrypoints)
    || canonicalJson(receipt.entrypoints.map((item) => item?.relativePath)) !== canonicalJson([...workerGuardEntrypointPaths])) {
    fail("legacy Worker guard entrypoints 无效");
  }
  if (canonicalJson(receipt.forbiddenLegacyDirectCommands) !== canonicalJson(workerGuardForbiddenScans)) {
    fail("legacy Worker guard forbidden scan 无效");
  }
  await assertNoReparsePath(receipt.bindings.protectedSourceRoot, "protected source root");
  for (const item of receipt.entrypoints) {
    exactKeys(item, ["relativePath", "sha256"], "legacy Worker guard entrypoint");
    if (!workerGuardEntrypointPaths.includes(item.relativePath) || !hex64.test(item.sha256 ?? "")) fail("legacy Worker guard entrypoint 无效");
    const installedPath = path.join(receipt.bindings.protectedSourceRoot, ...item.relativePath.split("/"));
    await assertNoReparsePath(installedPath, `legacy Worker guard entrypoint ${item.relativePath}`);
    if (sha256Bytes(await readFile(installedPath)) !== item.sha256) fail(`legacy Worker guard 入口未安装或已变更：${item.relativePath}`);
  }
  return { receipt, sha256: pointer.sha256 };
}

async function currentTombstoneState(runtimeRoot, { allowTestRuntimeRoot = false } = {}) {
  if (!(await exists(runtimeRoot))) return { current: null, guard: null, tombstone: false, runtimeInstalled: false };
  await assertNoReparsePath(runtimeRoot, "Worker runtime root");
  const markerPath = path.join(runtimeRoot, "runtime-root.json");
  if (!(await exists(markerPath))) fail("Worker runtime root 已存在但缺少受控 marker");
  await assertNoReparsePath(markerPath, "Worker runtime marker");
  const markerRead = await readCanonical(markerPath, "Worker runtime marker");
  exactKeys(markerRead.value, ["version", "runtimeRootPathSha256", "markerPayloadSha256"], "Worker runtime marker");
  validatePayload(markerRead.value, "markerPayloadSha256", "Worker runtime marker");
  if (markerRead.value.version !== releaseVersion
    || markerRead.value.runtimeRootPathSha256 !== windowsPathSha256(runtimeRoot)) fail("Worker runtime marker 身份无效");
  const bootstrapCurrent = await readCurrentRelease(runtimeRoot);
  if (!bootstrapCurrent) fail("Worker runtime 已安装但 current release/guard receipt 缺失");
  const authority = await readSalesAuthority(runtimeRoot);
  let current = bootstrapCurrent;
  let effective = null;
  if (authority) {
    effective = await resolveEffectiveReleaseChain({ runtimeRoot, allowTestRuntimeRoot, verifyInstalledHead: false });
    current = {
      manifestPath: effective.headManifestPath,
      manifestSha256: effective.head.manifestSha256,
      manifest: effective.headManifest,
    };
  }
  const guard = await readAndVerifyGuardReceipt(current);
  // The old source path remains immutable lineage metadata. Runtime admission
  // reads the manifest-bound proof and never opens the historical database.
  const retirementProof = authority ? await readD1RetirementReceipt(current.manifest, path.dirname(current.manifestPath), {
    bootstrapAuthoritySha256: effective.bootstrap.authoritySha256,
  }) : null;
  return {
    current,
    bootstrapCurrent,
    effective,
    authority,
    guard,
    retirementProof,
    runtimeInstalled: true,
  };
}

export async function assertLegacyWorkerLaunchAllowed({ runtimeRoot = workerRuntimeRoot } = {}) {
  // An installed managed runtime cannot become a legacy runtime if its D1
  // archive or authority files are missing. No database access is needed.
  if (await exists(runtimeRoot)) fail("受控 runtime 已安装，旧源码 Worker 入口已永久失效");
  return { status: "allowed", mode: "legacy-pre-release-install" };
}

/** @param {{ manifestPath: string, manifestSha256: string, runtimeRoot?: string, allowTestRuntimeRoot?: boolean }} options */
export async function assertReleaseWorkerLaunchAllowed({
  manifestPath,
  manifestSha256,
  runtimeRoot = workerRuntimeRoot,
  allowTestRuntimeRoot = false,
} = {}) {
  if (typeof manifestPath !== "string" || !path.win32.isAbsolute(manifestPath) || !hex64.test(manifestSha256 ?? "")) {
    fail("不可变 Worker release 身份参数无效");
  }
  const manifestRead = await readCanonical(manifestPath, "Worker release manifest");
  if (manifestRead.sha256 !== manifestSha256) fail("Worker release manifest 原始文件哈希不一致");
  const manifest = manifestRead.value;
  const context = await currentTombstoneState(runtimeRoot, { allowTestRuntimeRoot });
  if (!context.current || context.current.manifestSha256 !== manifestSha256
    || context.current.manifest.releaseId !== manifest.releaseId
    || path.resolve(context.current.manifestPath) !== path.resolve(manifestPath)) {
    fail("Worker release 不是已安装且受 guard receipt 保护的 effective head release");
  }
  const authority = context.authority ?? await readSalesAuthority(runtimeRoot);
  if (!authority) fail("sales authority sentinel 尚未发布，专用 retired release 禁止提前启动");
  if (!context.retirementProof) fail("缺少与 effective head 绑定的全局 D1 退役证明，禁止启动");
  if ((context.effective?.successorCount ?? 0) === 0
      && authority.guardReceiptSha256 !== context.guard.sha256
    || context.current.manifest.artifacts.guardReceipt.sha256 !== context.guard.sha256
    || authority.sourceD1PathSha256 !== manifest.runtime.sourceD1PathSha256
    || authority.persistRootPathSha256 !== manifest.runtime.persistRootPathSha256) {
    fail("sales authority/successor lineage 未授权 effective head Worker release");
  }
  return {
    status: "allowed",
    mode: context.effective?.successorCount > 0 ? "release-post-cutover-successor" : "release-post-cutover",
    authorityRawSha256: authority.rawSha256,
    effectiveHeadBindingSha256: context.effective?.head.bindingSha256 ?? null,
  };
}

export function processReceiptCore({ releaseId, manifestSha256, supervisorPid, supervisorCreationDate, supervisorEntrypointPathSha256, manifestPathSha256 }) {
  return {
    version: processReceiptVersion,
    releaseId,
    manifestSha256,
    supervisorPid,
    supervisorCreationDate,
    supervisorEntrypointPathSha256,
    manifestPathSha256,
  };
}
