import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  copyFile,
  cp,
  link,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  realpath,
  rename,
  rmdir,
  rm,
  stat,
  unlink,
  writeFile,
} from "node:fs/promises";
import { createConnection, createServer } from "node:net";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
// Keep the trusted verifier self-contained when Django deploys its exact file.
export const d1ProofVersion = "teruisi-global-d1-retirement-proof-v1";
export const d1ReceiptVersion = "teruisi-global-d1-retirement-binding-v1";
export const d1ReceiptRelativePath = "audit/global-d1-retirement.json";
export const d1RetiredDomains = Object.freeze([
  "sales", "finance", "netshop", "market", "products", "inventory",
  "workflow-launch", "workflow-operations", "customer-service", "erp-reference", "access-control", "ai-assistant",
]);
const hex = /^[0-9a-f]{64}$/;
const proofReleaseIdPattern = /^\d{8}T\d{6}Z-[0-9a-f]{16}$/;
export function canonicalProofJson(value) {
  return canonicalJson(value);
}
export function proofHash(value) { return createHash("sha256").update(canonicalProofJson(value)).digest("hex"); }
export function proofBytesHash(value) { return createHash("sha256").update(value).digest("hex"); }
function keys(value, expected, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || canonicalProofJson(Object.keys(value).sort()) !== canonicalProofJson([...expected].sort())) {
    throw new Error(`${label}: invalid fields`);
  }
}
function hashFields(value, fields) {
  if (fields.some((field) => typeof value[field] !== "string" || !hex.test(value[field]))) throw new Error("D1 retirement proof: invalid digest");
}
function date(value) {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT/.test(value)
      || !Number.isFinite(Date.parse(value))) throw new Error("D1 retirement proof: invalid timestamp");
}
export function sealProof(value, field) { return { ...value, [field]: proofHash(value) }; }
function selfHash(value, field) {
  const { [field]: digest, ...core } = value;
  if (!hex.test(digest ?? "") || digest !== proofHash(core)) throw new Error("D1 retirement proof: digest mismatch");
}
export function validateD1RetirementProof(proof, expected = {}) {
  keys(proof, ["version", "verifiedAt", "runtimeRootPathSha256", "sourceD1PathSha256", "persistRootPathSha256",
    "bootstrapAuthoritySha256", "adoptionPredecessorManifestSha256", "sourceSchemaSha256", "postgresEvidenceSha256",
    "retainedEvidenceSha256", "domains", "proofSha256"], "D1 retirement proof");
  selfHash(proof, "proofSha256");
  if (proof.version !== d1ProofVersion) throw new Error("D1 retirement proof: unsupported version");
  date(proof.verifiedAt);
  hashFields(proof, ["runtimeRootPathSha256", "sourceD1PathSha256", "persistRootPathSha256", "bootstrapAuthoritySha256",
    "adoptionPredecessorManifestSha256", "sourceSchemaSha256", "postgresEvidenceSha256", "retainedEvidenceSha256"]);
  if (!Array.isArray(proof.domains) || canonicalProofJson(proof.domains.map((item) => item?.domain)) !== canonicalProofJson(d1RetiredDomains)) {
    throw new Error("D1 retirement proof: incomplete or reordered domains");
  }
  for (const item of proof.domains) {
    keys(item, ["domain", "cutoverId", "migrationSha256", "objectsSha256", "receiptSha256", "viewCount", "guardCount"], "D1 domain proof");
    hashFields(item, ["migrationSha256", "objectsSha256", "receiptSha256"]);
    if (typeof item.cutoverId !== "string" || !/^[A-Za-z0-9._:-]{8,128}$/.test(item.cutoverId)
        || !Number.isSafeInteger(item.viewCount) || item.viewCount < 0
        || !Number.isSafeInteger(item.guardCount) || item.guardCount < 0
        || (item.domain === "finance" ? item.guardCount < 1 : item.viewCount < 1)) throw new Error("D1 retirement proof: invalid domain evidence");
  }
  for (const [field, value] of Object.entries(expected)) {
    if (!Object.hasOwn(proof, field) || proof[field] !== value) throw new Error(`D1 retirement proof: ${field} binding mismatch`);
  }
  return proof;
}
export function createD1RetirementReceipt(proof, manifest) {
  validateD1RetirementProof(proof, {
    runtimeRootPathSha256: manifest.runtime.runtimeRootPathSha256,
    sourceD1PathSha256: manifest.runtime.sourceD1PathSha256,
    persistRootPathSha256: manifest.runtime.persistRootPathSha256,
  });
  return sealProof({ version: d1ReceiptVersion, releaseId: manifest.releaseId,
    sourceFingerprint: manifest.source.sourceFingerprint, buildFingerprint: manifest.build.buildFingerprint, proof }, "receiptPayloadSha256");
}
export async function readD1RetirementReceipt(manifest, releaseRoot, expected = {}) {
  const pointer = manifest.artifacts?.d1RetirementReceipt;
  keys(pointer, ["version", "relativePath", "sha256"], "D1 retirement receipt pointer");
  if (pointer.version !== d1ReceiptVersion || pointer.relativePath !== d1ReceiptRelativePath || !hex.test(pointer.sha256 ?? "")) {
    throw new Error("D1 retirement proof is required for this release");
  }
  const target = path.resolve(releaseRoot, ...d1ReceiptRelativePath.split("/"));
  let current = path.parse(target).root;
  for (const part of target.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if ((await lstat(current)).isSymbolicLink()) throw new Error("D1 retirement receipt: reparse path forbidden");
  }
  const info = await lstat(target);
  if (!info.isFile() || info.nlink !== 1 || path.resolve(await realpath(target)).toLowerCase() !== target.toLowerCase()
      || info.size > 64 * 1024) throw new Error("D1 retirement receipt: invalid file");
  const raw = await readFile(target);
  if (proofBytesHash(raw) !== pointer.sha256) throw new Error("D1 retirement receipt: file digest mismatch");
  const receipt = JSON.parse(raw.toString("utf8"));
  if (!raw.equals(Buffer.from(`${canonicalProofJson(receipt)}\n`))) throw new Error("D1 retirement receipt: noncanonical bytes");
  keys(receipt, ["version", "releaseId", "sourceFingerprint", "buildFingerprint", "proof", "receiptPayloadSha256"], "D1 retirement receipt");
  selfHash(receipt, "receiptPayloadSha256");
  hashFields(receipt, ["sourceFingerprint", "buildFingerprint"]);
  if (receipt.version !== d1ReceiptVersion || !proofReleaseIdPattern.test(receipt.releaseId ?? "")
      || receipt.releaseId !== manifest.releaseId || receipt.sourceFingerprint !== manifest.source.sourceFingerprint
      || receipt.buildFingerprint !== manifest.build.buildFingerprint) throw new Error("D1 retirement receipt: release mismatch");
  return validateD1RetirementProof(receipt.proof, {
    runtimeRootPathSha256: manifest.runtime.runtimeRootPathSha256,
    sourceD1PathSha256: manifest.runtime.sourceD1PathSha256,
    persistRootPathSha256: manifest.runtime.persistRootPathSha256,
    ...expected,
  });
}


export const workerRuntimeRoot = "D:\\teruisi-runtime\\teruisi-worker-sales";
// Existing immutable releases retain the original combined-tree provenance.
// New successors are built only from the clean integration worktree so the
// user's live development tree never has to be edited or moved for a release.
export const workerLegacySourceRoot = "D:\\运营管理系统-django-sales-combined";
export const workerSourceRoot = "D:\\运营管理系统-sales-django-release";
export const workerProtectedSourceRoot = "D:\\运营管理系统";
export const workerPersistRoot = "D:\\运营管理系统\\.wrangler\\state";
export const workerDevVarsSource = "D:\\运营管理系统\\.dev.vars";
export const workerHost = "127.0.0.1";
export const workerPort = 3000;
export const workerHelperHost = "127.0.0.1";
export const workerHelperPort = 5791;
export const releaseVersion = "teruisi-local-worker-release-v1";
export const verificationVersion = "teruisi-local-worker-release-verification-v1";
export const contractReceiptVersion = "teruisi-sales-retired-code-receipt-v1";
export const helperReceiptVersion = "teruisi-worker-helper-build-receipt-v1";
export const processReceiptVersion = "teruisi-local-worker-process-v1";
export const supervisorPrelaunchVerificationReceiptVersion = "teruisi-local-worker-supervisor-prelaunch-verification-v1";
export const supervisorPrelaunchVerificationReceiptRelativePath = "state/worker-startup-verification.json";
export const supervisorPrelaunchVerificationReceiptMaxAgeMs = 120_000;
export const activationFenceVersion = "teruisi-local-worker-release-activation-fence-v1";
export const activationFenceRelativePath = ".runtime/worker-release-activation-fence.json";
export const treeHashAlgorithm = "sha256-ordinal-path-length-content-v1";
export const treeHashMetadataConcurrency = 64;
export const treeHashReadConcurrency = 16;
export const treeHashReadBatchBytes = 32 * 1024 * 1024;
export const npmCiArguments = Object.freeze(["ci", "--ignore-scripts=false", "--no-audit", "--no-fund"]);
export const bundledNpmPackageRootRelativePath = "node_modules/npm";
export const bundledNpmCliRelativePath = "node_modules/npm/bin/npm-cli.js";
export const bundledNpmPackageJsonRelativePath = "node_modules/npm/package.json";
export const salesRetirementMigrationSha256 = "f981a62efd0515a7f64dd9f174151b8cfeb0c4b071d8236c481b5459761a3b8f";
// Updated only after the standalone helper builder has passed its closure,
// mutable-root, resource and health tests.  The trusted Django verifier uses
// this as the build-code trust root; the candidate cannot self-certify a
// weakened bundle builder.
export const trustedHelperBuilderSha256 = "ccf1e8252dce50b7cbe20fa84ea9dfa654b03ec689993d3504f469e7c366ad38";

const salesContractTestFiles = Object.freeze([
  "tests/sales-d1-retirement.test.ts",
  "tests/django-sales-route-integration.test.ts",
]);
const vinextScratchConfigRelativePath = ".wrangler/deploy/config.json";
const requiredHelperMutableRootRewrites = Object.freeze([
  "lib/jackyun/run-lock.ts",
  "lib/jd/chromium-run-lock.ts",
  "tools/jackyun-api-export.ts",
  "tools/jackyun-automation-runner.ts",
  "tools/jackyun-browser-controller.ts",
  "tools/jackyun-daily-runner.ts",
  "tools/jackyun-download-runner.ts",
  "tools/jackyun-n8n-pipeline.ts",
  "tools/jd-market-ranking-daily.ts",
  "tools/jd-multi-store-runner.ts",
  "tools/jd-n8n-pipeline.ts",
  "tools/jd-promotion-export.ts",
  "tools/jd-promotion-n8n-pipeline.ts",
  "tools/sales-import-runner.ts",
  "tools/tmall-multi-store-import-runner.ts",
  "tools/tmall-pagewise-product-master-export.ts",
  "tools/tmall-product-master-cadence.ts",
  "tools/tmall-product-master-export.ts",
  "tools/tmall-promotion-export.ts",
  "tools/tmall-sycm-cookie-pipeline.ts",
]);
const requiredHelperImportMetaNeutralizedPaths = Object.freeze([
  "tools/jackyun-automation-runner.ts",
  "tools/jackyun-browser-controller.ts",
  "tools/jackyun-daily-runner.ts",
  "tools/jackyun-download-runner.ts",
  "tools/jd-multi-store-runner.ts",
  "tools/jd-promotion-export.ts",
  "tools/sales-import-runner.ts",
  "tools/tmall-download-receipt.ts",
  "tools/tmall-multi-store-import-runner.ts",
  "tools/tmall-product-master-export.ts",
  "tools/tmall-promotion-export.ts",
]);
const requiredHelperMutableConfigPaths = Object.freeze([
  "config/tmall-store-accounts.json",
]);
const requiredHelperImmutableResourceUrlPaths = Object.freeze([
  "tools/jd-secure-credential.ts",
  "tools/tmall-secure-credential.ts",
]);
const requiredHelperResourceInputPaths = Object.freeze([
  "tools/jd-credential-vault.ps1",
  "tools/tmall-credential-vault.ps1",
]);
const requiredHelperResourceOutputPaths = Object.freeze([
  "jd-credential-vault.ps1",
  "tmall-credential-vault.ps1",
]);
export const workerGuardEntrypointPaths = Object.freeze([
  "package.json",
  "运行项目.bat",
  "tools/operations-system-control.ps1",
  "tools/start-local-worker.mjs",
  "tools/worker-authority-guard.mjs",
  "tools/d1-retirement-proof.mjs",
  "tools/collect-d1-retirement-proof.mjs",
  "tools/worker-local-release.mjs",
  "tools/worker-local-release-rotation.mjs",
  "tools/worker-local-service.ps1",
  activationFenceRelativePath,
]);
export const workerGuardCheckNames = Object.freeze([
  "packageScriptsControlled",
  "batchEntrypointControlled",
  "desktopControlEntrypointControlled",
  "legacySupervisorGuardedAtStartBuildAndRestart",
  "forbiddenLegacyDirectCommandsAbsent",
]);
export const workerGuardForbiddenScans = Object.freeze([
  { scope: "package.json:scripts.dev/start/start:local-worker", pattern: "vinext (dev|start)|wrangler dev|node tools/start-local-worker.mjs", matches: [] },
  { scope: "运行项目.bat", pattern: "npm install|npm run dev|vinext dev|vinext start|wrangler dev", matches: [] },
  { scope: "tools/operations-system-control.ps1", pattern: "start-local-worker.mjs|vinext dev|vinext start|wrangler dev|--build", matches: [] },
]);
export const workerReleaseBundledSourcePaths = Object.freeze([
  ...workerGuardEntrypointPaths,
  "package-lock.json",
  "tools/worker-local-runtime-supervisor.mjs",
]);
export const workerReleaseKeyFilePaths = Object.freeze([
  "dist/server/index.js",
  "dist/server/wrangler.json",
  ...workerGuardEntrypointPaths,
  "tools/worker-local-runtime-supervisor.mjs",
  "helper/tmall-workflow-helper.mjs",
]);
const modulePath = fileURLToPath(import.meta.url);
const hex64 = /^[0-9a-f]{64}$/;
const releaseIdPattern = /^\d{8}T\d{6}Z-[0-9a-f]{16}$/;
const manifestFileName = "deployment-manifest.json";
const currentPointerFileName = "current-deployment.json";
const runtimeMarkerFileName = "runtime-root.json";
const retiredSalesObjectNames = Object.freeze([
  "sales_import_upload_chunks", "sales_import_uploads", "sales_order_lines", "sales_import_batches",
  "sales_overview_response_cache", "sales_overview_cache_state", "sales_projection_outbox",
  "sales_projection_source_state", "sales_write_authority",
]);
const sharedImportRetirementGuardNames = Object.freeze([
  "sales_retired_fingerprints_insert_guard", "sales_retired_fingerprints_update_guard", "sales_retired_fingerprints_delete_guard",
  "sales_retired_attempts_insert_guard", "sales_retired_attempts_update_guard", "sales_retired_attempts_delete_guard",
  "sales_retired_scope_heads_insert_guard", "sales_retired_scope_heads_update_guard", "sales_retired_scope_heads_delete_guard",
]);

const legacySalesSourcePaths = Object.freeze([
  "lib/sales/category-analysis.ts",
  "lib/sales/category-resolution.ts",
  "lib/sales/database.ts",
  "lib/sales/overview-cache-schema.ts",
  "lib/sales/overview-response-cache.ts",
  "lib/sales/period.ts",
  "lib/sales/product-query.ts",
  "lib/sales/summary.ts",
]);

const salesDjangoSourceChecks = Object.freeze([
  ["app/api/imports/sales/route.ts", "@/lib/django/sales-writer"],
  ["app/api/imports/sales/verify/route.ts", "@/lib/django/sales-writer"],
  ["app/api/sales/summary/route.ts", "@/lib/django/sales-gateway"],
  ["app/api/sales/category-analysis/route.ts", "@/lib/django/sales-gateway"],
  ["app/api/sales/category-analysis/detail/route.ts", "@/lib/django/sales-gateway"],
  ["lib/sales/import-service.ts", "@/lib/django/sales-writer"],
  ["lib/sales/chunked-upload.ts", "@/lib/django/sales-writer"],
]);

function fail(message) {
  throw new Error(message);
}

function assertPlainObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${label}必须为 JSON 对象`);
  return value;
}

export function assertExactKeys(value, expected, label) {
  const record = assertPlainObject(value, label);
  const actual = Object.keys(record).sort();
  const wanted = [...expected].sort();
  if (canonicalJson(actual) !== canonicalJson(wanted)) fail(`${label}字段集合无效`);
  return record;
}

export function ordinalCompare(left, right) {
  return left === right ? 0 : left < right ? -1 : 1;
}

export function canonicalJson(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const keys = Object.keys(value).sort(ordinalCompare);
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  fail("canonical JSON 不支持该值");
}

export function sha256Bytes(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function sha256Canonical(value) {
  return sha256Bytes(Buffer.from(canonicalJson(value), "utf8"));
}

export function withPayloadSha256(value, fieldName) {
  if (Object.prototype.hasOwnProperty.call(value, fieldName)) fail(`${fieldName}不能预先存在`);
  return { ...value, [fieldName]: sha256Canonical(value) };
}

export function workerReleaseActivationFence({ createdAt, sourceFingerprint, buildFingerprint }) {
  if (typeof createdAt !== "string" || Number.isNaN(Date.parse(createdAt))
      || ![sourceFingerprint, buildFingerprint].every((value) => hex64.test(value ?? ""))) {
    fail("Worker release activation fence 身份无效");
  }
  return withPayloadSha256({
    version: activationFenceVersion,
    createdAt,
    sourceFingerprint,
    buildFingerprint,
  }, "payloadSha256");
}

export function canonicalWindowsPath(value) {
  if (typeof value !== "string" || !/^[A-Za-z]:[\\/]/.test(value) || /^[A-Za-z]:[^\\/]/.test(value)) {
    fail("路径必须是带盘符的绝对 Windows 路径");
  }
  let normalized = path.win32.normalize(value.replaceAll("/", "\\"));
  if (normalized.length > 3) normalized = normalized.replace(/[\\]+$/, "");
  return normalized.toUpperCase();
}

export function windowsPathSha256(value) {
  return sha256Bytes(Buffer.from(canonicalWindowsPath(value), "utf8"));
}

function isWithinWindowsPath(parent, child) {
  const parentValue = canonicalWindowsPath(parent);
  const childValue = canonicalWindowsPath(child);
  return childValue === parentValue || childValue.startsWith(`${parentValue}\\`);
}

export function assertWorkerManifestProvenance(manifest, { allowTestRuntimeRoot = false } = {}) {
  const source = assertPlainObject(manifest?.source, "manifest source provenance");
  const build = assertPlainObject(manifest?.build, "manifest build provenance");
  const runtime = assertPlainObject(manifest?.runtime, "manifest runtime provenance");
  const devVars = assertPlainObject(runtime.devVars, "manifest devVars provenance");
  const expectedNodeExecutable = path.resolve(process.execPath);
  const expectedNodeRoot = path.dirname(expectedNodeExecutable);
  const expectedNpmPackageRoot = path.join(expectedNodeRoot, ...bundledNpmPackageRootRelativePath.split("/"));
  const expectedNpmCli = path.join(expectedNodeRoot, ...bundledNpmCliRelativePath.split("/"));
  const expectedNpmPackageJson = path.join(expectedNodeRoot, ...bundledNpmPackageJsonRelativePath.split("/"));
  if (typeof source.rootPathSha256 !== "string" || !hex64.test(source.rootPathSha256)) {
    fail("manifest source rootPathSha256 无效");
  }
  if (build.nodeVersion !== process.version || !/^v24\./.test(build.nodeVersion)
    || build.nodeExecutablePathSha256 !== windowsPathSha256(expectedNodeExecutable)
    || build.npmPackageRootRelativePath !== bundledNpmPackageRootRelativePath
    || build.npmPackageRootPathSha256 !== windowsPathSha256(expectedNpmPackageRoot)
    || build.npmCliRelativePath !== bundledNpmCliRelativePath
    || build.npmCliPathSha256 !== windowsPathSha256(expectedNpmCli)
    || build.npmPackageJsonRelativePath !== bundledNpmPackageJsonRelativePath
    || build.npmPackageJsonPathSha256 !== windowsPathSha256(expectedNpmPackageJson)
    || !hex64.test(build.npmCliSha256 ?? "")
    || !hex64.test(build.npmPackageJsonSha256 ?? "")
    || build.npmPackageTree?.algorithm !== treeHashAlgorithm
    || !Number.isSafeInteger(build.npmPackageTree?.fileCount) || build.npmPackageTree.fileCount < 1
    || !hex64.test(build.npmPackageTree?.sha256 ?? "")
    || canonicalJson(build.npmCiArguments) !== canonicalJson(npmCiArguments)
    || typeof build.npmVersion !== "string"
    || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(build.npmVersion)) {
    fail("manifest Node/bundled npm ci provenance 无效");
  }
  if (typeof runtime.protectedSourceRoot !== "string" || typeof devVars.sourcePath !== "string"
    || path.win32.basename(devVars.sourcePath) !== ".dev.vars"
    || canonicalWindowsPath(path.win32.dirname(devVars.sourcePath)) !== canonicalWindowsPath(runtime.protectedSourceRoot)
    || !isWithinWindowsPath(runtime.protectedSourceRoot, devVars.sourcePath)) {
    fail("manifest .dev.vars 未受 protected source root 精确约束");
  }
  const acceptedProductionSourceRoots = new Set([
    windowsPathSha256(workerLegacySourceRoot),
    windowsPathSha256(workerSourceRoot),
  ]);
  if (!allowTestRuntimeRoot && (
    !acceptedProductionSourceRoots.has(source.rootPathSha256)
    || canonicalWindowsPath(runtime.protectedSourceRoot) !== canonicalWindowsPath(workerProtectedSourceRoot)
    || canonicalWindowsPath(devVars.sourcePath) !== canonicalWindowsPath(workerDevVarsSource)
  )) {
    fail("production manifest source/.dev.vars provenance 未绑定固定 legacy/successor/main 根");
  }
}

async function pathExists(value) {
  try {
    await lstat(value);
    return true;
  } catch (error) {
    if (error && typeof error === "object" && error.code === "ENOENT") return false;
    throw error;
  }
}

export async function assertNoReparsePoint(target, { allowMissingLeaf = false, label = "路径" } = {}) {
  const absolute = path.resolve(target);
  const parsed = path.parse(absolute);
  const relativeParts = absolute.slice(parsed.root.length).split(path.sep).filter(Boolean);
  let current = parsed.root;
  for (let index = 0; index < relativeParts.length; index += 1) {
    current = path.join(current, relativeParts[index]);
    let info;
    try {
      info = await lstat(current);
    } catch (error) {
      if (error && typeof error === "object" && error.code === "ENOENT" && allowMissingLeaf) return;
      throw error;
    }
    if (info.isSymbolicLink()) fail(`${label}不得包含重解析点：${current}`);
  }
  const resolved = await realpath(absolute);
  if (canonicalWindowsPath(resolved) !== canonicalWindowsPath(absolute)) fail(`${label}真实路径不一致`);
}

async function assertRegularFile(target, label, { rejectReparse = true } = {}) {
  const info = await lstat(target);
  if (!info.isFile() || info.isSymbolicLink()) fail(`${label}必须是普通文件`);
  if (rejectReparse) await assertNoReparsePoint(target, { label });
  return info;
}

async function readStableRegularFile(target, label) {
  const before = await assertRegularFile(target, label);
  const raw = await readFile(target);
  const after = await assertRegularFile(target, label);
  if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size || before.mtimeMs !== after.mtimeMs) {
    fail(`${label}在读取期间发生变化`);
  }
  return raw;
}

function bundledNpmPaths() {
  const nodeExecutablePath = path.resolve(process.execPath);
  const nodeRoot = path.dirname(nodeExecutablePath);
  return {
    nodeExecutablePath,
    npmPackageRoot: path.join(nodeRoot, ...bundledNpmPackageRootRelativePath.split("/")),
    npmCliPath: path.join(nodeRoot, ...bundledNpmCliRelativePath.split("/")),
    npmPackageJsonPath: path.join(nodeRoot, ...bundledNpmPackageJsonRelativePath.split("/")),
  };
}

function npmToolchainManifestProvenance({
  nodeExecutablePath, npmPackageRoot, npmCliPath, npmPackageJsonPath,
  npmCliRaw, npmPackageJsonRaw, npmPackageTree, npmVersion,
}) {
  return {
    nodeVersion: process.version,
    nodeExecutablePathSha256: windowsPathSha256(nodeExecutablePath),
    npmVersion,
    npmPackageRootRelativePath: bundledNpmPackageRootRelativePath,
    npmPackageRootPathSha256: windowsPathSha256(npmPackageRoot),
    npmCliRelativePath: bundledNpmCliRelativePath,
    npmCliPathSha256: windowsPathSha256(npmCliPath),
    npmCliSha256: sha256Bytes(npmCliRaw),
    npmPackageJsonRelativePath: bundledNpmPackageJsonRelativePath,
    npmPackageJsonPathSha256: windowsPathSha256(npmPackageJsonPath),
    npmPackageJsonSha256: sha256Bytes(npmPackageJsonRaw),
    npmPackageTree,
    npmCiArguments: [...npmCiArguments],
  };
}

export async function resolveBundledNpmToolchain() {
  if (!/^v24\./.test(process.version)) fail("Worker 发布构建固定要求 Node 24.x");
  const paths = bundledNpmPaths();
  await assertRegularFile(paths.nodeExecutablePath, "Node 24 executable");
  await assertNoReparsePoint(paths.npmPackageRoot, { label: "bundled npm package root" });
  const npmPackageTreeBefore = await hashTree(paths.npmPackageRoot);
  const [npmCliRaw, npmPackageJsonRaw] = await Promise.all([
    readStableRegularFile(paths.npmCliPath, "bundled npm CLI"),
    readStableRegularFile(paths.npmPackageJsonPath, "bundled npm package.json"),
  ]);
  let npmPackage;
  try {
    npmPackage = JSON.parse(npmPackageJsonRaw.toString("utf8"));
  } catch {
    fail("bundled npm package.json 不是有效 JSON");
  }
  if (!npmPackage || typeof npmPackage !== "object" || Array.isArray(npmPackage)
    || npmPackage.name !== "npm"
    || typeof npmPackage.version !== "string"
    || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(npmPackage.version)
    || !npmPackage.bin || typeof npmPackage.bin !== "object" || Array.isArray(npmPackage.bin)
    || npmPackage.bin.npm !== "bin/npm-cli.js") {
    fail("bundled npm package identity/name/version/bin 无效");
  }
  const declaredCliPath = path.resolve(path.dirname(paths.npmPackageJsonPath), ...npmPackage.bin.npm.split("/"));
  if (!isWithinWindowsPath(path.dirname(paths.npmPackageJsonPath), declaredCliPath)
    || canonicalWindowsPath(declaredCliPath) !== canonicalWindowsPath(paths.npmCliPath)) {
    fail("bundled npm package bin.npm 越界或未绑定唯一 npm-cli.js");
  }
  const versionResult = await runProcess(paths.nodeExecutablePath, [paths.npmCliPath, "--version"], {
    label: "bundled npm version",
  });
  const npmVersion = versionResult.stdout.trim();
  if (npmVersion !== npmPackage.version
    || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(npmVersion)) {
    fail("bundled npm CLI 输出版本与 package.json 不一致");
  }
  const [npmCliAfter, npmPackageJsonAfter] = await Promise.all([
    readStableRegularFile(paths.npmCliPath, "bundled npm CLI"),
    readStableRegularFile(paths.npmPackageJsonPath, "bundled npm package.json"),
  ]);
  const npmPackageTreeAfter = await hashTree(paths.npmPackageRoot);
  if (sha256Bytes(npmCliAfter) !== sha256Bytes(npmCliRaw)
    || sha256Bytes(npmPackageJsonAfter) !== sha256Bytes(npmPackageJsonRaw)
    || canonicalJson(npmPackageTreeAfter) !== canonicalJson(npmPackageTreeBefore)) {
    fail("bundled npm toolchain 在版本探测期间发生变化");
  }
  return {
    ...paths,
    provenance: npmToolchainManifestProvenance({
      ...paths,
      npmCliRaw,
      npmPackageJsonRaw,
      npmPackageTree: npmPackageTreeAfter,
      npmVersion,
    }),
  };
}

export async function assertBundledNpmToolchainProvenance(expected) {
  const current = await resolveBundledNpmToolchain();
  if (canonicalJson(current.provenance) !== canonicalJson(expected)) {
    fail("bundled npm toolchain 与 manifest provenance 不一致");
  }
  return current;
}

async function readCanonicalJson(target, label) {
  const raw = await readFile(target);
  if (raw.length === 0 || raw[0] === 0xef) fail(`${label}必须是 UTF-8 无 BOM canonical JSON`);
  let value;
  try {
    value = JSON.parse(raw.toString("utf8"));
  } catch {
    fail(`${label}不是有效 JSON`);
  }
  const expected = Buffer.from(`${canonicalJson(value)}\n`, "utf8");
  if (!raw.equals(expected)) fail(`${label}必须使用唯一 canonical JSON 字节表示`);
  return { value, raw, sha256: sha256Bytes(raw) };
}

async function writeCanonicalJson(target, value) {
  await writeFile(target, `${canonicalJson(value)}\n`, { encoding: "utf8", flag: "wx" });
}

function redactProcessDiagnostic(value, cwd, env, command, args) {
  let result = value
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?):\/\/[^\s"']+/gi, "[redacted-connection-url]")
    .replace(/\b([A-Za-z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|DATABASE_URL|CONNECTION_STRING|COOKIE)[A-Za-z0-9_]*)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, "$1=[redacted]")
    .replace(/\S*\.dev\.vars\S*/gi, "[redacted-dev-vars]");
  const sensitiveKey = /(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|DATABASE(?:_URL)?|CONNECTION(?:_STRING)?|COOKIE|CREDENTIAL|PRIVATE[_-]?KEY)/i;
  if (env && typeof env === "object") {
    for (const [key, rawValue] of Object.entries(env)) {
      if (!sensitiveKey.test(key) || typeof rawValue !== "string" || rawValue.length === 0) continue;
      result = result.replaceAll(rawValue, "[redacted-env-value]");
      let encoded = rawValue;
      try { encoded = encodeURIComponent(rawValue); } catch {}
      if (encoded !== rawValue) result = result.replaceAll(encoded, "[redacted-env-value]");
    }
  }
  for (const literal of [command, ...(Array.isArray(args) ? args : [])]) {
    if (typeof literal !== "string" || literal.length < 4) continue;
    result = result.replaceAll(literal, "[redacted-command-value]");
    result = result.replaceAll(literal.replaceAll("\\", "/"), "[redacted-command-value]");
  }
  if (typeof cwd === "string" && cwd.length > 0) {
    const escapeRegExp = (item) => item.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    result = result.replace(new RegExp(escapeRegExp(cwd), "gi"), "[redacted-cwd]");
    result = result.replace(new RegExp(escapeRegExp(cwd.replaceAll("\\", "/")), "gi"), "[redacted-cwd]");
  }
  return result.replace(/\r\n/g, "\n").trim();
}

function processDiagnosticExcerpt(buffer, cwd, env, command, args) {
  const sanitized = redactProcessDiagnostic(buffer.toString("utf8"), cwd, env, command, args)
    .replace(/[\u0000-\u001f\u007f]/g, (character) => {
      if (character === "\n") return "\\n";
      if (character === "\r") return "\\r";
      if (character === "\t") return "\\t";
      return `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`;
    });
  const maxCharacters = 1_200;
  if (sanitized.length <= maxCharacters) return sanitized;
  const side = 520;
  return `${sanitized.slice(0, side)}...[redacted/truncated]...${sanitized.slice(-side)}`;
}

export async function runProcess(command, args, {
  cwd,
  env = process.env,
  label = "进程",
  maxOutputBytes = 16 * 1024 * 1024,
  timeoutMs = 10 * 60 * 1000,
} = {}) {
  if (!Number.isSafeInteger(maxOutputBytes) || maxOutputBytes < 1 || maxOutputBytes > 64 * 1024 * 1024) {
    fail("子进程输出上限无效");
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30 * 60 * 1000) {
    fail("子进程时限无效");
  }
  return new Promise((resolveRun, rejectRun) => {
    const stdout = [];
    const stderr = [];
    let outputBytes = 0;
    let outputChunks = 0;
    let forcedFailure = null;
    let settled = false;
    let child;
    let timeout;
    let killGrace;
    const clearTimers = () => {
      if (timeout) clearTimeout(timeout);
      if (killGrace) clearTimeout(killGrace);
    };
    const failureError = (reason) => {
      const stdoutBuffer = Buffer.concat(stdout);
      const stderrBuffer = Buffer.concat(stderr);
      const diagnostics = [];
      if (stderrBuffer.length > 0) diagnostics.push(`stderr=${processDiagnosticExcerpt(stderrBuffer, cwd, env, command, args)}`);
      if (stdoutBuffer.length > 0) diagnostics.push(`stdout=${processDiagnosticExcerpt(stdoutBuffer, cwd, env, command, args)}`);
      return new Error(`${label}失败：${reason}${diagnostics.length > 0 ? ` (${diagnostics.join("; ")})` : ""}`);
    };
    const rejectOnce = (error) => {
      if (settled) return;
      settled = true;
      clearTimers();
      rejectRun(error);
    };
    const resolveOnce = (value) => {
      if (settled) return;
      settled = true;
      clearTimers();
      resolveRun(value);
    };
    try {
      child = spawn(command, args, {
        cwd,
        env,
        shell: false,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      const code = error && typeof error === "object" && typeof error.code === "string" ? error.code : "unknown";
      rejectOnce(new Error(`${label}启动失败：code=${code}`));
      return;
    }
    const terminateControlledTree = (reason) => {
      if (!forcedFailure) forcedFailure = reason;
      if (process.platform === "win32" && Number.isSafeInteger(child.pid) && child.pid > 0) {
        try {
          const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
            shell: false,
            windowsHide: true,
            stdio: "ignore",
          });
          killer.once("error", () => {
            try { child.kill("SIGKILL"); } catch {}
          });
          killer.once("close", () => {
            try { child.kill("SIGKILL"); } catch {}
          });
        } catch {
          try { child.kill("SIGKILL"); } catch {}
        }
      } else {
        try { child.kill("SIGKILL"); } catch {}
      }
      if (!killGrace) {
        killGrace = setTimeout(() => {
          child.stdout.destroy();
          child.stderr.destroy();
          rejectOnce(failureError(forcedFailure));
        }, 5_000);
        killGrace.unref();
      }
    };
    const collect = (chunks, chunk) => {
      outputBytes += chunk.length;
      outputChunks += 1;
      if (outputBytes <= maxOutputBytes && outputChunks <= 8_192) chunks.push(chunk);
      if (!forcedFailure && (outputBytes > maxOutputBytes || outputChunks > 8_192)) {
        terminateControlledTree(outputBytes > maxOutputBytes
          ? `输出超过 ${maxOutputBytes} 字节上限`
          : "输出 chunk 数超过 8192 上限");
      }
    };
    child.stdout.on("data", (chunk) => collect(stdout, chunk));
    child.stderr.on("data", (chunk) => collect(stderr, chunk));
    timeout = setTimeout(() => {
      if (settled) return;
      terminateControlledTree(forcedFailure ?? `超过 ${timeoutMs}ms 时限`);
    }, timeoutMs);
    timeout.unref();
    child.once("error", (error) => {
      const code = error && typeof error === "object" && typeof error.code === "string" ? error.code : "unknown";
      rejectOnce(new Error(`${label}启动失败：code=${code}`));
    });
    child.once("close", (code, signal) => {
      const stdoutBuffer = Buffer.concat(stdout);
      const stderrBuffer = Buffer.concat(stderr);
      if (forcedFailure || code !== 0 || signal) {
        const reason = forcedFailure ?? (signal ? `signal=${signal}` : `exit=${code ?? "unknown"}`);
        rejectOnce(failureError(reason));
        return;
      }
      resolveOnce({ stdout: stdoutBuffer.toString("utf8"), stderr: stderrBuffer.toString("utf8") });
    });
  });
}

async function listGitSourceFiles(sourceRoot) {
  const { stdout } = await runProcess("git.exe", ["-C", sourceRoot, "ls-files", "-z", "--cached", "--others", "--exclude-standard"], {
    label: "Git 源文件盘点",
  });
  const candidates = stdout.split("\0").filter(Boolean).map((value) => value.replaceAll("\\", "/"));
  const excludedPrefixes = ["dist/", "node_modules/", ".git/", ".runtime/", "outputs/", "tmp/"];
  const files = [];
  for (const relative of candidates) {
    if (relative === ".dev.vars" || excludedPrefixes.some((prefix) => relative.startsWith(prefix))) continue;
    if (relative.startsWith("../") || path.posix.isAbsolute(relative)) fail("Git 返回了越界路径");
    const absolute = path.join(sourceRoot, ...relative.split("/"));
    if (!(await pathExists(absolute))) continue;
    const info = await lstat(absolute);
    if (!info.isFile() || info.isSymbolicLink()) fail(`源文件必须是普通文件：${relative}`);
    files.push(relative);
  }
  files.sort(ordinalCompare);
  if (files.length === 0 || !files.includes("package-lock.json") || !files.includes("package.json")) {
    fail("源树缺少 package.json/package-lock.json");
  }
  return files;
}

export async function hashRelativeFiles(root, relativeFiles) {
  const digest = createHash("sha256");
  const ordered = [...relativeFiles].sort(ordinalCompare);
  const metadata = await mapBounded(ordered, treeHashMetadataConcurrency, async (relative) => {
    const normalized = relative.replaceAll("\\", "/");
    if (normalized.startsWith("../") || path.posix.isAbsolute(normalized)) fail("哈希文件路径越界");
    const absolute = path.join(root, ...normalized.split("/"));
    const info = await lstat(absolute);
    if (!info.isFile() || info.isSymbolicLink()) fail(`哈希对象不是普通文件：${normalized}`);
    return { normalized, absolute, size: info.size };
  });
  let start = 0;
  while (start < metadata.length) {
    let end = start;
    let batchBytes = 0;
    while (end < metadata.length && end - start < treeHashReadConcurrency) {
      const nextBytes = metadata[end].size;
      if (end > start && batchBytes + nextBytes > treeHashReadBatchBytes) break;
      batchBytes += nextBytes;
      end += 1;
    }
    const batch = metadata.slice(start, end);
    const contents = await Promise.all(batch.map((item) => readFile(item.absolute)));
    for (let index = 0; index < batch.length; index += 1) {
      const name = Buffer.from(batch[index].normalized, "utf8");
      const content = contents[index];
      const nameLength = Buffer.alloc(4);
      nameLength.writeUInt32BE(name.length);
      const contentLength = Buffer.alloc(8);
      contentLength.writeBigUInt64BE(BigInt(content.length));
      digest.update(nameLength).update(name).update(contentLength).update(content);
    }
    start = end;
  }
  return { algorithm: treeHashAlgorithm, fileCount: metadata.length, sha256: digest.digest("hex") };
}

async function mapBounded(items, concurrency, operation) {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1) fail("有界并发参数无效");
  const results = new Array(items.length);
  const failures = new Array(items.length);
  let nextIndex = 0;
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (true) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= items.length) return;
      try {
        results[index] = await operation(items[index], index);
      } catch (error) {
        failures[index] = error;
      }
    }
  });
  await Promise.all(workers);
  const firstFailure = failures.find((error) => error !== undefined);
  if (firstFailure !== undefined) throw firstFailure;
  return results;
}

async function listRegularTreeFiles(root, { excluded = new Set() } = {}) {
  const files = [];
  async function visit(directory, prefix) {
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => ordinalCompare(left.name, right.name));
    const inspected = await mapBounded(entries, treeHashMetadataConcurrency, async (entry) => ({
      entry,
      info: await lstat(path.join(directory, entry.name)),
    }));
    for (const { entry, info } of inspected) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (excluded.has(relative)) continue;
      const absolute = path.join(directory, entry.name);
      if (info.isSymbolicLink()) fail(`发布树不得包含重解析点：${relative}`);
      if (info.isDirectory()) await visit(absolute, relative);
      else if (info.isFile()) files.push(relative);
      else fail(`发布树包含非普通对象：${relative}`);
    }
  }
  await visit(root, "");
  return files;
}

export async function hashTree(root, options = {}) {
  return hashRelativeFiles(root, await listRegularTreeFiles(root, options));
}

async function copySourceSnapshot(sourceRoot, destination, files) {
  for (const relative of files) {
    const source = path.join(sourceRoot, ...relative.split("/"));
    const target = path.join(destination, ...relative.split("/"));
    await mkdir(path.dirname(target), { recursive: true });
    await copyFile(source, target);
  }
}

async function listIsolatedSourceFiles(root) {
  const files = [];
  async function visit(directory, prefix) {
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => ordinalCompare(left.name, right.name));
    for (const entry of entries) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (!prefix && (entry.name === "node_modules" || entry.name === "dist")) continue;
      const absolute = path.join(directory, entry.name);
      const info = await lstat(absolute);
      if (info.isSymbolicLink()) fail(`隔离 source closure 不得包含重解析点：${relative}`);
      if (info.isDirectory()) await visit(absolute, relative);
      else if (info.isFile()) files.push(relative);
      else fail(`隔离 source closure 包含非普通对象：${relative}`);
    }
  }
  await visit(root, "");
  // Directory DFS order is not the tree-hash protocol order.  A directory
  // such as `import/` is visited before its `import-history/` sibling even
  // though the full relative paths sort in the opposite order (`-` < `/`).
  // Normalize the complete relative paths only after enumeration.
  return files.sort(ordinalCompare);
}

function summarizeSortedFileDifferences(actual, expected, { itemLimit = 4, characterLimit = 64 } = {}) {
  const added = { count: 0, examples: [] };
  const removed = { count: 0, examples: [] };
  const record = (summary, relativePath) => {
    summary.count += 1;
    if (summary.examples.length < itemLimit) {
      const safeRelativePath = relativePath.replace(/[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/g, "?");
      summary.examples.push(safeRelativePath.length <= characterLimit
        ? safeRelativePath
        : `${safeRelativePath.slice(0, characterLimit - 3)}...`);
    }
  };
  let actualIndex = 0;
  let expectedIndex = 0;
  while (actualIndex < actual.length || expectedIndex < expected.length) {
    if (actualIndex >= actual.length) {
      record(removed, expected[expectedIndex]);
      expectedIndex += 1;
    } else if (expectedIndex >= expected.length) {
      record(added, actual[actualIndex]);
      actualIndex += 1;
    } else {
      const order = ordinalCompare(actual[actualIndex], expected[expectedIndex]);
      if (order === 0) {
        actualIndex += 1;
        expectedIndex += 1;
      } else if (order < 0) {
        record(added, actual[actualIndex]);
        actualIndex += 1;
      } else {
        record(removed, expected[expectedIndex]);
        expectedIndex += 1;
      }
    }
  }
  return {
    added: { ...added, omitted: added.count - added.examples.length },
    removed: { ...removed, omitted: removed.count - removed.examples.length },
  };
}

export async function assertIsolatedSourceClosure(root, expectedFiles, expectedTree, label = "隔离 source closure") {
  const actualFiles = (await listIsolatedSourceFiles(root)).sort(ordinalCompare);
  const expected = [...expectedFiles].sort(ordinalCompare);
  const differences = summarizeSortedFileDifferences(actualFiles, expected);
  if (differences.added.count !== 0 || differences.removed.count !== 0) {
    fail(`${label}文件集合发生增删：${canonicalJson(differences)}`);
  }
  const actualTree = await hashRelativeFiles(root, actualFiles);
  if (canonicalJson(actualTree) !== canonicalJson(expectedTree)) fail(`${label}内容发生变化`);
  return actualTree;
}

export function assertNoWranglerSourceConflict(sourceFiles) {
  if (!Array.isArray(sourceFiles)) fail("sourceFiles 必须为数组");
  for (const relativePath of sourceFiles) {
    if (typeof relativePath !== "string") fail("sourceFiles 路径必须为字符串");
    const normalized = relativePath.replaceAll("\\", "/").toLowerCase();
    if (normalized === ".wrangler" || normalized.startsWith(".wrangler/")) {
      fail("受控 source snapshot 不得包含 .wrangler 构建 scratch");
    }
  }
}

async function assertExactScratchDirectory(directory, expectedEntries, label) {
  await assertNoReparsePoint(directory, { label });
  const directoryInfo = await lstat(directory);
  if (!directoryInfo.isDirectory() || directoryInfo.isSymbolicLink()) fail(`${label}必须是实体目录`);
  const entries = await readdir(directory, { withFileTypes: true });
  entries.sort((left, right) => ordinalCompare(left.name, right.name));
  const actualNames = entries.map((entry) => entry.name);
  const expectedNames = expectedEntries.map((entry) => entry.name).sort(ordinalCompare);
  if (canonicalJson(actualNames) !== canonicalJson(expectedNames)) fail(`${label}对象集合无效`);
  for (const expected of expectedEntries) {
    const absolute = path.join(directory, expected.name);
    const info = await lstat(absolute);
    if (info.isSymbolicLink()) fail(`${label}不得包含重解析点：${expected.name}`);
    if (expected.kind === "directory" && !info.isDirectory()) fail(`${label}对象类型无效：${expected.name}`);
    if (expected.kind === "file" && !info.isFile()) fail(`${label}对象类型无效：${expected.name}`);
  }
}

export async function assertVinextScratchAbsent(buildRoot, sourceFiles) {
  assertNoWranglerSourceConflict(sourceFiles);
  if (await pathExists(path.join(buildRoot, ".wrangler"))) {
    fail("Vinext 构建前隔离 staging 已存在 .wrangler scratch");
  }
}

export async function consumeVinextBuildScratch(buildRoot, sourceFiles) {
  assertNoWranglerSourceConflict(sourceFiles);
  const scratchRoot = path.join(buildRoot, ".wrangler");
  const deployRoot = path.join(scratchRoot, "deploy");
  const configPath = path.join(deployRoot, "config.json");
  const expectedWranglerConfigPath = path.join(buildRoot, "dist", "server", "wrangler.json");
  const expectedConfigPathValue = path.relative(deployRoot, expectedWranglerConfigPath);
  const expectedRaw = Buffer.from(JSON.stringify({
    configPath: expectedConfigPathValue,
    auxiliaryWorkers: [],
  }), "utf8");
  await assertExactScratchDirectory(scratchRoot, [{ name: "deploy", kind: "directory" }], "Vinext .wrangler scratch");
  await assertExactScratchDirectory(deployRoot, [{ name: "config.json", kind: "file" }], "Vinext deploy scratch");
  const configInfo = await assertRegularFile(configPath, "Vinext deploy scratch config.json");
  if (configInfo.size !== expectedRaw.length || configInfo.nlink !== 1) {
    fail("Vinext deploy scratch config.json 必须是精确大小且无硬链接的普通文件");
  }
  const raw = await readStableRegularFile(configPath, "Vinext deploy scratch config.json");
  if (!raw.equals(expectedRaw)) fail("Vinext deploy scratch config.json 不是唯一受控字节表示");
  let config;
  try {
    config = JSON.parse(raw.toString("utf8"));
  } catch {
    fail("Vinext deploy scratch config.json 不是合法 JSON");
  }
  assertExactKeys(config, ["configPath", "auxiliaryWorkers"], "Vinext deploy scratch config.json");
  if (config.configPath !== expectedConfigPathValue
    || !Array.isArray(config.auxiliaryWorkers) || config.auxiliaryWorkers.length !== 0) {
    fail("Vinext deploy scratch config.json 契约无效");
  }
  const resolvedConfigPath = path.resolve(deployRoot, config.configPath);
  if (canonicalWindowsPath(resolvedConfigPath) !== canonicalWindowsPath(expectedWranglerConfigPath)) {
    fail("Vinext deploy scratch configPath 未精确指向隔离 dist/server/wrangler.json");
  }
  await assertRegularFile(expectedWranglerConfigPath, "Vinext Worker wrangler.json");

  // All unexpected siblings are rejected above before the first destructive
  // operation.  Remove only the verified scratch leaf and then its two empty
  // parents; recursive cleanup would make a future Vinext shape change unsafe.
  await unlink(configPath);
  if ((await readdir(deployRoot)).length !== 0) fail("Vinext deploy scratch 清理时出现未知对象");
  await assertNoReparsePoint(deployRoot, { label: "Vinext deploy scratch" });
  await rmdir(deployRoot);
  if ((await readdir(scratchRoot)).length !== 0) fail("Vinext .wrangler scratch 清理时出现未知对象");
  await assertNoReparsePoint(scratchRoot, { label: "Vinext .wrangler scratch" });
  await rmdir(scratchRoot);
  if (await pathExists(scratchRoot)) fail("Vinext .wrangler scratch 未完整清除");
  return {
    relativePath: vinextScratchConfigRelativePath,
    byteLength: raw.length,
    sha256: sha256Bytes(raw),
  };
}

function validatePackageLock(packageJson, packageLock) {
  assertPlainObject(packageJson, "package.json");
  const lock = assertPlainObject(packageLock, "package-lock.json");
  if (lock.lockfileVersion !== 3 || lock.requires !== true) fail("package-lock.json 必须是 lockfileVersion 3");
  const packages = assertPlainObject(lock.packages, "package-lock packages");
  const root = assertPlainObject(packages[""], "package-lock root");
  if (root.name !== packageJson.name || root.version !== packageJson.version) fail("package-lock 根身份与 package.json 不一致");
  if (packages["node_modules/wrangler"]?.version !== "4.92.0") fail("Worker 发布固定要求 Wrangler 4.92.0");
  if (!packages["node_modules/vinext"]?.version) fail("package-lock 缺少 Vinext");
}

async function buildSalesRetiredCodeReceipt(sourceRoot, sourceFiles, sourceFingerprint, buildFingerprint, contractTests, createdAt) {
  const sourceSet = new Set(sourceFiles);
  const legacyPathsAbsent = legacySalesSourcePaths.map((relativePath) => ({
    relativePath,
    absent: !sourceSet.has(relativePath),
  }));
  if (legacyPathsAbsent.some((item) => !item.absent)) fail("源树仍包含 D1 sales 领域实现");

  const djangoRoutes = [];
  for (const [relativePath, requiredImport] of salesDjangoSourceChecks) {
    if (!sourceSet.has(relativePath)) fail(`源树缺少 Django sales 契约文件：${relativePath}`);
    const content = await readFile(path.join(sourceRoot, ...relativePath.split("/")));
    const text = content.toString("utf8");
    if (!text.includes(requiredImport)) fail(`${relativePath} 未使用 ${requiredImport}`);
    djangoRoutes.push({ relativePath, requiredImport, sha256: sha256Bytes(content) });
  }

  const migrationPath = "drizzle/0092_sales_domain_retirement.sql";
  if (!sourceSet.has(migrationPath)) fail("源树缺少 0092 sales retirement 迁移");
  const migration = await readFile(path.join(sourceRoot, ...migrationPath.split("/")));
  if (sha256Bytes(migration) !== salesRetirementMigrationSha256) {
    fail("0092 sales retirement migration 未匹配 trusted final SHA-256");
  }
  const migrationText = migration.toString("utf8");
  for (const required of [
    "DROP TABLE IF EXISTS `sales_order_lines`",
    "DROP TABLE IF EXISTS `sales_import_batches`",
    "DROP TABLE IF EXISTS `sales_write_authority`",
    ...[
      "sales_import_upload_chunks", "sales_import_uploads", "sales_order_lines", "sales_import_batches",
      "sales_overview_response_cache", "sales_overview_cache_state", "sales_projection_outbox",
      "sales_projection_source_state", "sales_write_authority",
    ].map((name) => `CREATE VIEW \`${name}\` AS`),
    ...[
      "sales_retired_fingerprints_insert_guard", "sales_retired_fingerprints_update_guard", "sales_retired_fingerprints_delete_guard",
      "sales_retired_attempts_insert_guard", "sales_retired_attempts_update_guard", "sales_retired_attempts_delete_guard",
      "sales_retired_scope_heads_insert_guard", "sales_retired_scope_heads_update_guard", "sales_retired_scope_heads_delete_guard",
    ].map((name) => `CREATE TRIGGER \`${name}\``),
  ]) {
    if (!migrationText.includes(required)) fail(`0092 缺少退役契约：${required}`);
  }
  const journal = await readFile(path.join(sourceRoot, "drizzle", "meta", "_journal.json"), "utf8");
  if (journal.includes("0092_sales_domain_retirement")) fail("0092 operator-only 迁移不得进入普通 Drizzle journal");
  const operatorPath = "tools/sales-d1-retirement.ts";
  if (!sourceSet.has(operatorPath)) fail("源树缺少 sales retirement operator");
  const operator = await readFile(path.join(sourceRoot, ...operatorPath.split("/")));
  if (!operator.toString("utf8").includes('const RETIREMENT_VERSION = "sales-d1-retirement-v4"')) {
    fail("sales retirement operator audit 必须固定为 v4");
  }

  const core = {
    version: contractReceiptVersion,
    generatedAt: createdAt,
    sourceFingerprint,
    buildFingerprint,
    status: "passed",
    checks: {
      legacySalesSourcePathsAbsent: true,
      salesRoutesUseDjango: true,
      operatorOnlyRetirementMigrationPresent: true,
      operatorOnlyRetirementMigrationExcludedFromJournal: true,
      sharedImportRetirementGuardsPresent: true,
      retirementOperatorAuditV4: true,
    },
    evidence: {
      legacyPathsAbsent,
      djangoRoutes,
      retirementMigration: { relativePath: migrationPath, sha256: sha256Bytes(migration) },
      retirementOperator: { relativePath: operatorPath, sha256: sha256Bytes(operator), auditVersion: "sales-d1-retirement-v4" },
    },
    contractTests,
  };
  return withPayloadSha256(core, "receiptPayloadSha256");
}

async function runSalesContractTests(buildRoot, sourceFingerprint, buildFingerprint) {
  const testFiles = [];
  for (const relativePath of salesContractTestFiles) {
    const content = await readFile(path.join(buildRoot, ...relativePath.split("/")));
    testFiles.push({ relativePath, sha256: sha256Bytes(content) });
  }
  const args = ["--import", "tsx", "--test", "--test-reporter=tap", ...salesContractTestFiles];
  const startedAt = Date.now();
  const result = await new Promise((resolveRun, rejectRun) => {
    const child = spawn(process.execPath, args, {
      cwd: buildRoot,
      env: { ...process.env },
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    const maxOutputBytes = 16 * 1024 * 1024;
    let outputBytes = 0;
    let forcedFailure = null;
    const capture = (chunks, chunk) => {
      outputBytes += chunk.length;
      if (outputBytes > maxOutputBytes && !forcedFailure) {
        forcedFailure = "sales contract test 输出超过 16 MiB 上限";
        child.kill("SIGKILL");
        return;
      }
      if (!forcedFailure) chunks.push(chunk);
    };
    child.stdout.on("data", (chunk) => capture(stdout, chunk));
    child.stderr.on("data", (chunk) => capture(stderr, chunk));
    const timeout = setTimeout(() => {
      forcedFailure = "sales contract test 超过 10 分钟时限";
      child.kill("SIGKILL");
    }, 10 * 60 * 1000);
    timeout.unref();
    child.once("error", rejectRun);
    child.once("exit", (code, signal) => {
      clearTimeout(timeout);
      resolveRun({ code, signal, stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr), forcedFailure });
    });
  });
  if (result.forcedFailure) fail(result.forcedFailure);
  const tap = result.stdout.toString("utf8");
  const summaryValue = (name) => {
    const match = tap.match(new RegExp(`^# ${name} (\\d+(?:\\.\\d+)?)$`, "m"));
    if (!match) fail(`sales contract test TAP 缺少 ${name} 汇总`);
    return name === "duration_ms" ? Number(match[1]) : Number.parseInt(match[1], 10);
  };
  const summary = {
    tests: summaryValue("tests"),
    pass: summaryValue("pass"),
    fail: summaryValue("fail"),
    cancelled: summaryValue("cancelled"),
    skipped: summaryValue("skipped"),
    todo: summaryValue("todo"),
    durationMs: summaryValue("duration_ms"),
  };
  if (result.code !== 0 || result.signal || summary.tests < 1 || summary.pass !== summary.tests
    || summary.fail !== 0 || summary.cancelled !== 0 || summary.skipped !== 0 || summary.todo !== 0) {
    const detail = result.stderr.toString("utf8").trim().slice(0, 500);
    fail(`sales retired-code contract tests 失败${detail ? `：${detail}` : ""}`);
  }
  return {
    runner: { executable: "node", nodeVersion: process.version, arguments: args },
    testFiles,
    summary,
    tapSha256: sha256Bytes(result.stdout),
    stderrSha256: sha256Bytes(result.stderr),
    wallDurationMs: Math.max(0, Date.now() - startedAt),
    sourceFingerprint,
    buildFingerprint,
  };
}

function assertSafeSourceRelativePath(relativePath, label) {
  if (typeof relativePath !== "string" || relativePath.length < 1 || relativePath.includes("\\")
    || path.posix.isAbsolute(relativePath) || path.posix.normalize(relativePath) !== relativePath
    || relativePath === ".." || relativePath.startsWith("../")) fail(`${label}包含不安全相对路径`);
}

export function helperPathContainsMutableState(relativePath) {
  return typeof relativePath === "string" && /(?:^|\/)(?:\.runtime|outputs|tmp)(?:\/|$)/.test(relativePath);
}

export async function validateHelperBuilderEvidence(evidence, sourceRoot, helperRoot) {
  assertExactKeys(evidence, [
    "version", "entryRelativePath", "inputFiles", "mutableRootRewritePaths", "importMetaNeutralizedPaths",
    "immutableResourceUrlPaths", "mutableConfigPaths", "resourceInputFiles", "resourceOutputFiles", "outputSha256",
  ], "helper builder evidence");
  if (evidence.version !== "teruisi-worker-helper-build-v1"
    || evidence.entryRelativePath !== "tools/tmall-sycm-cookie-pipeline.ts"
    || !hex64.test(evidence.outputSha256 ?? "") || !Array.isArray(evidence.inputFiles)
    || evidence.inputFiles.length < 1 || !Array.isArray(evidence.mutableRootRewritePaths)
    || !Array.isArray(evidence.importMetaNeutralizedPaths) || !Array.isArray(evidence.immutableResourceUrlPaths)
    || !Array.isArray(evidence.mutableConfigPaths) || !Array.isArray(evidence.resourceInputFiles)
    || !Array.isArray(evidence.resourceOutputFiles)) fail("helper builder evidence 身份无效");
  const inputPaths = [];
  const sourceProjectRootPaths = [];
  for (const item of evidence.inputFiles) {
    assertExactKeys(item, ["relativePath", "sha256"], "helper builder input");
    assertSafeSourceRelativePath(item.relativePath, "helper builder input");
    if (!hex64.test(item.sha256 ?? "") || helperPathContainsMutableState(item.relativePath)) {
      fail("helper builder input 不得包含 mutable state/无效哈希");
    }
    const raw = await readFile(path.join(sourceRoot, ...item.relativePath.split("/")));
    if (sha256Bytes(raw) !== item.sha256) fail(`helper builder input hash 不一致：${item.relativePath}`);
    if (/\b(?:const|let|var)\s+[A-Za-z_$][\w$]*root[\w$]*\s*=[^;]*import\.meta\.url[^;]*;/is.test(raw.toString("utf8"))) {
      sourceProjectRootPaths.push(item.relativePath);
    }
    inputPaths.push(item.relativePath);
  }
  if (new Set(inputPaths).size !== inputPaths.length
    || canonicalJson(inputPaths) !== canonicalJson([...inputPaths].sort(ordinalCompare))
    || !inputPaths.includes(evidence.entryRelativePath)) fail("helper builder input closure 顺序/集合无效");
  for (const [label, values] of [
    ["mutable root rewrite", evidence.mutableRootRewritePaths],
    ["import.meta neutralization", evidence.importMetaNeutralizedPaths],
  ]) {
    for (const relativePath of values) {
      assertSafeSourceRelativePath(relativePath, `helper ${label}`);
      if (!inputPaths.includes(relativePath)) fail(`helper ${label} 不在 input closure`);
    }
    if (new Set(values).size !== values.length
      || canonicalJson(values) !== canonicalJson([...values].sort(ordinalCompare))) fail(`helper ${label} 集合无效`);
  }
  for (const required of requiredHelperMutableRootRewrites) {
    if (!evidence.mutableRootRewritePaths.includes(required)) fail(`helper mutable root rewrite 缺少 ${required}`);
  }
  for (const relativePath of sourceProjectRootPaths) {
    if (!evidence.mutableRootRewritePaths.includes(relativePath)) {
      fail(`helper source projectRoot 未由 protected mutable root 接管：${relativePath}`);
    }
  }
  const assertExactSortedPaths = (values, expected, label, prefixPattern) => {
    for (const relativePath of values) {
      assertSafeSourceRelativePath(relativePath, label);
      if (prefixPattern && !prefixPattern.test(relativePath)) fail(`${label}路径范围无效`);
    }
    if (new Set(values).size !== values.length
      || canonicalJson(values) !== canonicalJson([...values].sort(ordinalCompare))
      || canonicalJson(values) !== canonicalJson([...expected])) fail(`${label}集合无效`);
  };
  assertExactSortedPaths(
    evidence.mutableRootRewritePaths,
    requiredHelperMutableRootRewrites,
    "helper mutable root rewrite",
  );
  assertExactSortedPaths(
    evidence.importMetaNeutralizedPaths,
    requiredHelperImportMetaNeutralizedPaths,
    "helper import.meta neutralization",
  );
  assertExactSortedPaths(
    evidence.immutableResourceUrlPaths,
    requiredHelperImmutableResourceUrlPaths,
    "helper immutable resource URL",
  );
  assertExactSortedPaths(
    evidence.mutableConfigPaths,
    requiredHelperMutableConfigPaths,
    "helper mutable config",
    /^config\/.+\.json$/,
  );
  const validateResourceFiles = async (items, expectedPaths, root, label) => {
    const paths = [];
    for (const item of items) {
      assertExactKeys(item, ["relativePath", "sha256"], label);
      assertSafeSourceRelativePath(item.relativePath, label);
      if (!hex64.test(item.sha256 ?? "")) fail(`${label} hash 无效`);
      const raw = await readFile(path.join(root, ...item.relativePath.split("/")));
      if (sha256Bytes(raw) !== item.sha256) fail(`${label} hash 不一致：${item.relativePath}`);
      paths.push(item.relativePath);
    }
    if (canonicalJson(paths) !== canonicalJson([...expectedPaths])) fail(`${label}集合无效`);
  };
  await validateResourceFiles(
    evidence.resourceInputFiles,
    requiredHelperResourceInputPaths,
    sourceRoot,
    "helper immutable resource input",
  );
  await validateResourceFiles(
    evidence.resourceOutputFiles,
    requiredHelperResourceOutputPaths,
    helperRoot,
    "helper immutable resource output",
  );
  if (canonicalJson(inputPaths.filter((relativePath) => relativePath.startsWith("config/")))
    !== canonicalJson(["config/jackyun-api-templates.json"])) {
    fail("helper immutable JS bundle 不得嵌入 mutable config state");
  }
  const helperEntrypoint = path.join(helperRoot, "tmall-workflow-helper.mjs");
  await assertRegularFile(helperEntrypoint, "immutable helper bundle");
  if (sha256Bytes(await readFile(helperEntrypoint)) !== evidence.outputSha256) fail("immutable helper bundle 输出哈希无效");
}

async function buildWorkerHelperBundle(buildRoot, stageRoot) {
  const helperRoot = path.join(stageRoot, "helper-build");
  await mkdir(helperRoot);
  const outputPath = path.join(helperRoot, "tmall-workflow-helper.mjs");
  const result = await runProcess(process.execPath, [
    path.join(buildRoot, "tools", "build-worker-helper.mjs"),
    "--source-root", buildRoot,
    "--output", outputPath,
  ], { cwd: buildRoot, label: "immutable 5791 helper bundle 构建" });
  let evidence;
  try {
    evidence = JSON.parse(result.stdout);
  } catch {
    fail("immutable helper builder 未返回有效 JSON receipt");
  }
  if (result.stdout !== `${canonicalJson(evidence)}\n`) fail("immutable helper builder receipt 不是 canonical JSON");
  await validateHelperBuilderEvidence(evidence, buildRoot, helperRoot);
  const helperTree = await hashTree(helperRoot);
  return { helperRoot, evidence, helperTree, evidenceSha256: sha256Canonical(evidence) };
}

async function buildLegacyGuardReceipt({
  sourceRoot,
  sourceFingerprint,
  createdAt,
  protectedSourceRoot,
  persistRoot,
  sourceD1Path,
}) {
  const entrypoints = [];
  const texts = new Map();
  for (const relativePath of workerGuardEntrypointPaths) {
    const content = await readFile(path.join(sourceRoot, ...relativePath.split("/")));
    entrypoints.push({ relativePath, sha256: sha256Bytes(content) });
    texts.set(relativePath, content.toString("utf8"));
  }
  const packageJson = JSON.parse(texts.get("package.json"));
  for (const scriptName of ["dev", "start", "start:local-worker"]) {
    const command = packageJson.scripts?.[scriptName];
    if (typeof command !== "string" || !/worker-local-service\.ps1.+-Action Start/i.test(command)) {
      fail(`package.json ${scriptName} 未收口到不可变 Worker launcher`);
    }
  }
  const batchText = texts.get("运行项目.bat");
  if (!/operations-system-control\.ps1.+-Action Start.+-Open/i.test(batchText)
      || /worker-local-service\.ps1|npm\s+(?:install|run\s+dev)|vinext\s+(?:dev|start)|wrangler\s+dev/i.test(batchText)) {
    fail("运行项目.bat 未收口到唯一系统启动总控");
  }
  const controlText = texts.get("tools/operations-system-control.ps1");
  if (!/worker-local-service\.ps1/i.test(controlText) || /start-local-worker\.mjs/i.test(controlText) || /--build/i.test(controlText)) {
    fail("桌面控制面板仍可直接启动旧 Worker");
  }
  const starterText = texts.get("tools/start-local-worker.mjs");
  if ((starterText.match(/assertLegacyWorkerLaunchAllowed/g) ?? []).length < 3) {
    fail("旧 start-local-worker 未在构建、启动和 supervisor restart 入口全部安装 authority guard");
  }

  const forbiddenLegacyDirectCommands = workerGuardForbiddenScans.map((item) => ({ ...item, matches: [] }));
  const core = {
    version: "teruisi-legacy-worker-guard-receipt-v1",
    generatedAt: createdAt,
    sourceFingerprint,
    status: "passed",
    bindings: {
      protectedSourceRoot,
      protectedSourceRootPathSha256: windowsPathSha256(protectedSourceRoot),
      persistRoot,
      persistRootPathSha256: windowsPathSha256(persistRoot),
      sourceD1Path,
      sourceD1PathSha256: windowsPathSha256(sourceD1Path),
      authorityRelativePath: "state/sales-postgresql-authority.json",
      authoritySidecarRelativePath: "state/sales-postgresql-authority.json.sha256",
    },
    checks: {
      packageScriptsControlled: true,
      batchEntrypointControlled: true,
      desktopControlEntrypointControlled: true,
      legacySupervisorGuardedAtStartBuildAndRestart: true,
      forbiddenLegacyDirectCommandsAbsent: true,
    },
    entrypoints,
    forbiddenLegacyDirectCommands,
  };
  return withPayloadSha256(core, "receiptPayloadSha256");
}

async function ensureRuntimeMarker(runtimeRoot, { allowTestRuntimeRoot = false } = {}) {
  if (!allowTestRuntimeRoot && canonicalWindowsPath(runtimeRoot) !== canonicalWindowsPath(workerRuntimeRoot)) {
    fail(`runtime 根必须固定为 ${workerRuntimeRoot}`);
  }
  await assertNoReparsePoint(runtimeRoot, { allowMissingLeaf: true, label: "Worker runtime 根" });
  if (!(await pathExists(runtimeRoot))) await mkdir(runtimeRoot, { recursive: false });
  await assertNoReparsePoint(runtimeRoot, { label: "Worker runtime 根" });
  const markerPath = path.join(runtimeRoot, runtimeMarkerFileName);
  if (await pathExists(markerPath)) {
    const { value } = await readCanonicalJson(markerPath, "Worker runtime marker");
    assertExactKeys(value, ["version", "runtimeRootPathSha256", "markerPayloadSha256"], "Worker runtime marker");
    const { markerPayloadSha256, ...core } = value;
    if (markerPayloadSha256 !== sha256Canonical(core)) fail("Worker runtime marker 自哈希无效");
    if (value.version !== releaseVersion || value.runtimeRootPathSha256 !== windowsPathSha256(runtimeRoot)) fail("Worker runtime marker 身份不一致");
  } else {
    const entries = await readdir(runtimeRoot);
    if (entries.length !== 0) fail("Worker runtime 根非空且缺少受控 marker，拒绝接管");
    await writeCanonicalJson(markerPath, withPayloadSha256({
      version: releaseVersion,
      runtimeRootPathSha256: windowsPathSha256(runtimeRoot),
    }, "markerPayloadSha256"));
  }
  for (const name of ["releases", ".staging", "state", "logs", "cache"] ) {
    const target = path.join(runtimeRoot, name);
    if (!(await pathExists(target))) await mkdir(target);
    const info = await lstat(target);
    if (!info.isDirectory() || info.isSymbolicLink()) fail(`runtime ${name} 必须是受控实体目录`);
  }
}

async function readDeployCurrentPointer(runtimeRoot, { allowTestRuntimeRoot = false } = {}) {
  const pointerPath = path.join(runtimeRoot, currentPointerFileName);
  if (!(await pathExists(pointerPath))) return null;
  await assertRegularFile(pointerPath, "Worker current pointer");
  const pointerRead = await readCanonicalJson(pointerPath, "Worker current pointer");
  const pointer = pointerRead.value;
  assertExactKeys(pointer, [
    "version", "releaseId", "manifestRelativePath", "manifestSha256", "pointerPayloadSha256",
  ], "Worker current pointer");
  validatePayloadSha(pointer, "pointerPayloadSha256", "Worker current pointer");
  if (pointer.version !== "teruisi-local-worker-current-v1" || !releaseIdPattern.test(pointer.releaseId ?? "")
    || !hex64.test(pointer.manifestSha256 ?? "")) fail("Worker current pointer 身份无效");
  const expectedRelativePath = `releases/${pointer.releaseId}/${manifestFileName}`;
  if (pointer.manifestRelativePath !== expectedRelativePath) fail("Worker current pointer manifest 路径无效");
  const manifestPath = path.join(runtimeRoot, ...expectedRelativePath.split("/"));
  const context = await manifestContext(manifestPath, pointer.manifestSha256, { allowTestRuntimeRoot });
  if (context.manifest.releaseId !== pointer.releaseId) fail("Worker current pointer 与 manifest releaseId 不一致");
  return {
    pointerSha256: pointerRead.sha256,
    releaseId: pointer.releaseId,
    manifestSha256: pointer.manifestSha256,
  };
}

export async function assertAuthorityTargetsCurrent({
  runtimeRoot,
  releaseId,
  manifestSha256,
  expectedPointerSha256,
  allowTestRuntimeRoot = false,
} = {}) {
  const current = await readDeployCurrentPointer(runtimeRoot, { allowTestRuntimeRoot });
  if (!current || current.releaseId !== releaseId || current.manifestSha256 !== manifestSha256
    || (expectedPointerSha256 !== undefined && current.pointerSha256 !== expectedPointerSha256)) {
    fail("authority 只能绑定已安装的 exact current Worker release");
  }
  return current;
}

export async function publishFirstCurrentPointer({
  runtimeRoot,
  releaseRoot,
  currentPointer,
  allowTestRuntimeRoot = false,
} = {}) {
  assertExactKeys(currentPointer, [
    "version", "releaseId", "manifestRelativePath", "manifestSha256", "pointerPayloadSha256",
  ], "待发布 Worker current pointer");
  validatePayloadSha(currentPointer, "pointerPayloadSha256", "待发布 Worker current pointer");
  const expectedReleaseRoot = path.join(runtimeRoot, "releases", currentPointer.releaseId);
  if (currentPointer.version !== "teruisi-local-worker-current-v1"
    || !releaseIdPattern.test(currentPointer.releaseId ?? "") || !hex64.test(currentPointer.manifestSha256 ?? "")
    || currentPointer.manifestRelativePath !== `releases/${currentPointer.releaseId}/${manifestFileName}`
    || canonicalWindowsPath(releaseRoot) !== canonicalWindowsPath(expectedReleaseRoot)) {
    fail("待发布 Worker current pointer 身份无效");
  }
  const candidateContext = await manifestContext(
    path.join(releaseRoot, manifestFileName),
    currentPointer.manifestSha256,
    { allowTestRuntimeRoot },
  );
  if (candidateContext.manifest.releaseId !== currentPointer.releaseId) {
    fail("待发布 Worker current pointer 未绑定 exact candidate manifest");
  }
  const currentPointerPath = path.join(runtimeRoot, currentPointerFileName);
  try {
    await writeFileAtomicCreateOnly(
      currentPointerPath,
      Buffer.from(`${canonicalJson(currentPointer)}\n`, "utf8"),
    );
  } catch (error) {
    if (error && typeof error === "object" && error.code === "EEXIST") {
      let winner;
      try {
        winner = await readDeployCurrentPointer(runtimeRoot, { allowTestRuntimeRoot });
      } catch {
        fail("Worker current pointer 已存在但无法验证；保留候选 release 并永久拒绝普通 Deploy");
      }
      if (winner.releaseId !== currentPointer.releaseId) {
        await rm(releaseRoot, { recursive: true, force: true }).catch(() => {});
      }
      fail("Worker current pointer 已由另一首次 Deploy 原子发布；失败方未改动赢家 current/release");
    }
    await rm(releaseRoot, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
  const published = await readDeployCurrentPointer(runtimeRoot, { allowTestRuntimeRoot });
  if (!published || published.releaseId !== currentPointer.releaseId
    || published.manifestSha256 !== currentPointer.manifestSha256) {
    fail("Worker current pointer 首次发布后回读身份不一致");
  }
  return published;
}

async function deploymentRetirementDetected(sourceD1Path) {
  await assertRegularFile(sourceD1Path, "Deploy D1 retirement 检查源");
  const database = new DatabaseSync(sourceD1Path, { readOnly: true });
  try {
    database.exec("BEGIN");
    const receiptObject = database.prepare(
      "SELECT type FROM sqlite_master WHERE name = 'domain_retirement_receipts' LIMIT 1",
    ).get();
    const retiredPlaceholders = retiredSalesObjectNames.map(() => "?").join(",");
    const retiredObjects = database.prepare(
      `SELECT type, name FROM sqlite_master WHERE name IN (${retiredPlaceholders}) ORDER BY name`,
    ).all(...retiredSalesObjectNames);
    const guardPlaceholders = sharedImportRetirementGuardNames.map(() => "?").join(",");
    const guardObjects = database.prepare(
      `SELECT type, name FROM sqlite_master WHERE name IN (${guardPlaceholders}) ORDER BY name`,
    ).all(...sharedImportRetirementGuardNames);
    database.exec("COMMIT");
    return Boolean(receiptObject)
      || retiredObjects.some((item) => item.type !== "table")
      || guardObjects.length > 0;
  } catch (error) {
    try { database.exec("ROLLBACK"); } catch {}
    throw error;
  } finally {
    database.close();
  }
}

export async function assertOrdinaryDeployAllowed({
  runtimeRoot,
  sourceD1Path,
  allowTestRuntimeRoot = false,
} = {}) {
  await assertNoReparsePoint(runtimeRoot, { label: "Worker runtime 根" });
  const stateRoot = path.join(runtimeRoot, "state");
  await assertNoReparsePoint(stateRoot, { label: "Worker runtime state 根" });
  const current = await readDeployCurrentPointer(runtimeRoot, { allowTestRuntimeRoot });
  if (current) {
    fail("Worker current pointer 已首次发布；普通 Worker Deploy 永久禁用，必须使用未来显式升级协议");
  }
  for (const fileName of ["sales-postgresql-authority.json", "sales-postgresql-authority.json.sha256"]) {
    if (await pathExists(path.join(stateRoot, fileName))) {
      fail("sales PostgreSQL authority 已开始发布；普通 Worker Deploy 永久禁用，必须使用未来显式升级协议");
    }
  }
  if (await deploymentRetirementDetected(sourceD1Path)) {
    fail("D1 sales retirement 已开始；普通 Worker Deploy 永久禁用，必须使用未来显式升级协议");
  }
  return {
    currentPointerSha256: null,
    currentReleaseId: null,
    updatePolicy: "first-deploy-create-only",
  };
}

async function createHardLinkOnly(source, target) {
  await assertRegularFile(source, ".dev.vars 源文件");
  if (await pathExists(target)) fail(`拒绝覆盖已存在的密钥链接：${target}`);
  await mkdir(path.dirname(target), { recursive: true });
  await link(source, target);
  const [sourceInfo, targetInfo] = await Promise.all([stat(source, { bigint: true }), stat(target, { bigint: true })]);
  if (sourceInfo.dev !== targetInfo.dev || sourceInfo.ino !== targetInfo.ino) fail(".dev.vars 未建立为同一文件的硬链接");
}

function timestampId(date = new Date()) {
  return date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

export async function deploymentKeyFiles(releaseRoot) {
  const files = [];
  for (const relativePath of workerReleaseKeyFilePaths) {
    const absolute = path.join(releaseRoot, ...relativePath.split("/"));
    await assertRegularFile(absolute, `发布关键文件 ${relativePath}`);
    files.push({ relativePath, sha256: sha256Bytes(await readFile(absolute)) });
  }
  return files;
}

export async function copyWorkerReleaseRuntimeArtifacts(sourceRoot, releaseRoot) {
  for (const relativePath of workerReleaseBundledSourcePaths) {
    const source = path.join(sourceRoot, ...relativePath.split("/"));
    const target = path.join(releaseRoot, ...relativePath.split("/"));
    await assertRegularFile(source, `Worker release source artifact ${relativePath}`);
    await mkdir(path.dirname(target), { recursive: true });
    await copyFile(source, target);
  }
}

export async function assertTrustedVerifierMatches(candidatePath, trustedPath = modulePath) {
  await assertRegularFile(candidatePath, "candidate Worker verifier");
  await assertRegularFile(trustedPath, "Django protected trusted Worker verifier");
  const [candidate, trusted] = await Promise.all([readFile(candidatePath), readFile(trustedPath)]);
  if (sha256Bytes(candidate) !== sha256Bytes(trusted)) {
    fail("candidate Worker verifier 与 Django protected trusted copy 不一致");
  }
  return sha256Bytes(candidate);
}

async function buildWorkerReleaseInternal({
  sourceRoot,
  runtimeRoot = workerRuntimeRoot,
  devVarsSource,
  persistRoot,
  sourceD1Path,
  now = new Date(),
  allowTestRuntimeRoot = false,
  publicationMode,
  retirementProof,
  verifyPreparationPredecessor,
} = {}) {
  if (!["first-deploy", "rotation-candidate"].includes(publicationMode)) fail("Worker release publication mode 无效");
  if (verifyPreparationPredecessor !== undefined &&
      (publicationMode !== "rotation-candidate" || typeof verifyPreparationPredecessor !== "function")) {
    fail("Online preparation is restricted to a rotation candidate with a predecessor verifier");
  }
  if (!/^v24\./.test(process.version)) fail("Worker 发布构建固定要求 Node 24.x");
  for (const [label, value] of Object.entries({ sourceRoot, devVarsSource, persistRoot, sourceD1Path })) {
    if (typeof value !== "string" || !path.win32.isAbsolute(value)) fail(`${label}必须是绝对路径`);
  }
  sourceRoot = path.resolve(sourceRoot);
  runtimeRoot = path.resolve(runtimeRoot);
  devVarsSource = path.resolve(devVarsSource);
  persistRoot = path.resolve(persistRoot);
  sourceD1Path = path.resolve(sourceD1Path);
  if (!allowTestRuntimeRoot && canonicalWindowsPath(sourceRoot) !== canonicalWindowsPath(workerSourceRoot)) {
    fail(`Worker 新发布源必须固定为隔离集成树 ${workerSourceRoot}`);
  }
  if (!allowTestRuntimeRoot && (canonicalWindowsPath(devVarsSource) !== canonicalWindowsPath(workerDevVarsSource)
    || canonicalWindowsPath(persistRoot) !== canonicalWindowsPath(workerPersistRoot))) {
    fail("Worker 发布必须硬链接主仓库 .dev.vars 并复用主仓库精确 Wrangler persist root");
  }
  if (!isWithinWindowsPath(persistRoot, sourceD1Path) || canonicalWindowsPath(persistRoot) === canonicalWindowsPath(sourceD1Path)) {
    fail("D1 源文件必须在固定 persist root 内");
  }
  await assertNoReparsePoint(sourceRoot, { label: "源仓库根" });
  await assertNoReparsePoint(persistRoot, { label: "Wrangler persist root" });
  if (publicationMode === "first-deploy") {
    await assertRegularFile(sourceD1Path, "D1 源文件");
  } else {
    validateD1RetirementProof(retirementProof, {
      runtimeRootPathSha256: windowsPathSha256(runtimeRoot),
      sourceD1PathSha256: windowsPathSha256(sourceD1Path),
      persistRootPathSha256: windowsPathSha256(persistRoot),
    });
  }
  await assertRegularFile(devVarsSource, ".dev.vars 源文件");
  await ensureRuntimeMarker(runtimeRoot, { allowTestRuntimeRoot });
  if (verifyPreparationPredecessor) {
    await verifyPreparationPredecessor();
  } else {
    if (await pathExists(path.join(runtimeRoot, "state", "worker-process.json"))) {
      fail("Worker process receipt 在 Deploy 开始前仍存在");
    }
    if (await probeAnyLocalPort(workerPort) || await probeAnyLocalPort(workerHelperPort)) {
      fail("3000/5791 端口在 Deploy 开始前被占用");
    }
  }
  if (publicationMode === "first-deploy") {
    await assertOrdinaryDeployAllowed({ runtimeRoot, sourceD1Path, allowTestRuntimeRoot });
  }
  const npmToolchain = await resolveBundledNpmToolchain();

  const stageRoot = path.join(runtimeRoot, ".staging", `deploy-${randomUUID()}`);
  const buildRoot = path.join(stageRoot, "build");
  const releaseStage = path.join(stageRoot, "release");
  try {
    await mkdir(buildRoot, { recursive: true });
    const sourceFiles = await listGitSourceFiles(sourceRoot);
    assertNoWranglerSourceConflict(sourceFiles);
    const sourceTree = await hashRelativeFiles(sourceRoot, sourceFiles);
    const sourceFingerprint = sourceTree.sha256;
    await copySourceSnapshot(sourceRoot, buildRoot, sourceFiles);
    const copiedSourceTree = await hashRelativeFiles(buildRoot, sourceFiles);
    if (canonicalJson(copiedSourceTree) !== canonicalJson(sourceTree)) fail("隔离源快照在复制后发生变化");
    await mkdir(path.join(releaseStage, "source-snapshot"), { recursive: true });
    await copySourceSnapshot(buildRoot, path.join(releaseStage, "source-snapshot"), sourceFiles);

    const projectPackageJsonPath = path.join(buildRoot, "package.json");
    const projectPackageLockPath = path.join(buildRoot, "package-lock.json");
    const packageJsonRaw = await readStableRegularFile(projectPackageJsonPath, "isolated project package.json");
    const packageLockRaw = await readStableRegularFile(projectPackageLockPath, "isolated project package-lock.json");
    const packageJson = JSON.parse(packageJsonRaw.toString("utf8"));
    const packageLock = JSON.parse(packageLockRaw.toString("utf8"));
    validatePackageLock(packageJson, packageLock);
    const packageLockSha256 = sha256Bytes(packageLockRaw);

    await runProcess(npmToolchain.nodeExecutablePath, [npmToolchain.npmCliPath, ...npmCiArguments], {
      cwd: buildRoot,
      label: "隔离 node_modules 闭包安装",
    });
    await assertBundledNpmToolchainProvenance(npmToolchain.provenance);
    await assertNoReparsePoint(path.join(buildRoot, "node_modules"), { label: "构建 node_modules" });
    await assertVinextScratchAbsent(buildRoot, sourceFiles);
    await runProcess(process.execPath, [path.join(buildRoot, "node_modules", "vinext", "dist", "cli.js"), "build"], {
      cwd: buildRoot,
      env: { ...process.env, VITE_TERUISI_LOCAL_BUILD: "true" },
      label: "Vinext Worker 构建",
    });
    await consumeVinextBuildScratch(buildRoot, sourceFiles);
    const postBuildSourceTree = await assertIsolatedSourceClosure(
      buildRoot, sourceFiles, sourceTree, "npm ci/Vinext 构建后的 source closure",
    );
    if (canonicalJson(postBuildSourceTree) !== canonicalJson(sourceTree)) {
      fail("npm ci/Vinext 构建修改了隔离 source snapshot，拒绝生成证据");
    }
    const helperBuild = await buildWorkerHelperBundle(buildRoot, stageRoot);
    await assertIsolatedSourceClosure(buildRoot, sourceFiles, sourceTree, "immutable helper build 后的 source closure");

    const distTree = await hashTree(path.join(buildRoot, "dist"), { excluded: new Set(["server/.dev.vars"]) });
    const nodeModulesTree = await hashTree(path.join(buildRoot, "node_modules"));
    const buildFingerprint = sha256Canonical({
      packageLockSha256,
      distTree,
      nodeModulesTree,
      helperTree: helperBuild.helperTree,
      helperBuildEvidenceSha256: helperBuild.evidenceSha256,
      npmToolchain: npmToolchain.provenance,
    });
    const contractTests = await runSalesContractTests(buildRoot, sourceFingerprint, buildFingerprint);
    await assertIsolatedSourceClosure(buildRoot, sourceFiles, sourceTree, "sales contract tests 后的 source closure");
    const createdAt = now.toISOString();
    const contractReceipt = await buildSalesRetiredCodeReceipt(
      buildRoot, sourceFiles, sourceFingerprint, buildFingerprint, contractTests, createdAt,
    );
    const activationFence = workerReleaseActivationFence({ createdAt, sourceFingerprint, buildFingerprint });
    await mkdir(path.join(buildRoot, path.dirname(activationFenceRelativePath)), { recursive: true });
    await writeCanonicalJson(
      path.join(buildRoot, ...activationFenceRelativePath.split("/")),
      activationFence,
    );
    // The immutable build source is the combined integration tree, while the
    // protected desktop entrypoints and .dev.vars remain in the main checkout.
    // They are deliberately allowed to be different roots; the guard receipt
    // binds the exact protected-entrypoint bytes installed in the protected root.
    const protectedSourceRoot = path.dirname(devVarsSource);
    if (!allowTestRuntimeRoot && canonicalWindowsPath(protectedSourceRoot) !== canonicalWindowsPath(workerProtectedSourceRoot)) {
      fail("legacy guard protected root 必须固定为主仓库");
    }
    const guardReceipt = await buildLegacyGuardReceipt({
      sourceRoot: buildRoot,
      sourceFingerprint,
      createdAt,
      protectedSourceRoot,
      persistRoot,
      sourceD1Path,
    });
    const helperReceipt = withPayloadSha256({
      version: helperReceiptVersion,
      sourceFingerprint,
      buildFingerprint,
      status: "passed",
      mutableRootMode: "manifest-protected-source-root",
      evidence: helperBuild.evidence,
      helperTree: helperBuild.helperTree,
    }, "receiptPayloadSha256");
    await mkdir(path.join(releaseStage, "tools"), { recursive: true });
    await mkdir(path.join(releaseStage, "audit"), { recursive: true });
    await cp(path.join(buildRoot, "dist"), path.join(releaseStage, "dist"), { recursive: true, errorOnExist: true, force: false });
    await cp(path.join(buildRoot, "node_modules"), path.join(releaseStage, "node_modules"), { recursive: true, errorOnExist: true, force: false });
    await cp(helperBuild.helperRoot, path.join(releaseStage, "helper"), { recursive: true, errorOnExist: true, force: false });
    await copyWorkerReleaseRuntimeArtifacts(buildRoot, releaseStage);
    const contractRelativePath = "audit/sales-retired-code-receipt.json";
    const contractPath = path.join(releaseStage, ...contractRelativePath.split("/"));
    await writeCanonicalJson(contractPath, contractReceipt);
    const contractReceiptSha256 = sha256Bytes(await readFile(contractPath));
    const guardRelativePath = "audit/legacy-worker-guard-receipt.json";
    const guardPath = path.join(releaseStage, ...guardRelativePath.split("/"));
    await writeCanonicalJson(guardPath, guardReceipt);
    const guardReceiptSha256 = sha256Bytes(await readFile(guardPath));
    const helperReceiptRelativePath = "audit/helper-build-receipt.json";
    const helperReceiptPath = path.join(releaseStage, ...helperReceiptRelativePath.split("/"));
    await writeCanonicalJson(helperReceiptPath, helperReceipt);
    const helperReceiptSha256 = sha256Bytes(await readFile(helperReceiptPath));
    await validateTree(path.join(releaseStage, "dist"), distTree, "copied dist", { excluded: new Set(["server/.dev.vars"]) });
    await validateTree(path.join(releaseStage, "node_modules"), nodeModulesTree, "copied node_modules");
    await validateTree(path.join(releaseStage, "helper"), helperBuild.helperTree, "copied helper bundle");
    const releaseId = `${timestampId(now)}-${sha256Canonical({
      sourceFingerprint, buildFingerprint, contractReceiptSha256, guardReceiptSha256,
      ...(retirementProof ? { retirementProofSha256: retirementProof.proofSha256 } : {}),
    }).slice(0, 16)}`;
    if (!releaseIdPattern.test(releaseId)) fail("内部 releaseId 格式无效");
    const releaseRoot = path.join(runtimeRoot, "releases", releaseId);
    if (await pathExists(releaseRoot)) fail("目标 release 已存在，拒绝覆盖");

    const keyFiles = await deploymentKeyFiles(releaseStage);
    await assertBundledNpmToolchainProvenance(npmToolchain.provenance);
    const manifestCore = {
      version: releaseVersion,
      releaseId,
      createdAt,
      source: {
        rootPathSha256: windowsPathSha256(sourceRoot),
        packageLockSha256,
        sourceFingerprint,
        snapshotRoot: "source-snapshot",
        tree: sourceTree,
      },
      build: {
        ...npmToolchain.provenance,
        buildRoot: "dist",
        distTree,
        nodeModulesRoot: "node_modules",
        nodeModulesTree,
        helperRoot: "helper",
        helperTree: helperBuild.helperTree,
        helperBuildEvidenceSha256: helperBuild.evidenceSha256,
        buildFingerprint,
      },
      runtime: {
        runtimeRootPathSha256: windowsPathSha256(runtimeRoot),
        releaseRootPathSha256: windowsPathSha256(releaseRoot),
        persistRootPathSha256: windowsPathSha256(persistRoot),
        sourceD1PathSha256: windowsPathSha256(sourceD1Path),
        protectedSourceRoot,
        protectedSourceRootPathSha256: windowsPathSha256(protectedSourceRoot),
        host: workerHost,
        port: workerPort,
        persistRoot,
        sourceD1Path,
        cliOverridesAllowed: false,
        helperMode: "supervisor_managed_immutable_bundle",
        helperHost: workerHelperHost,
        helperPort: workerHelperPort,
        helperMutableRoot: protectedSourceRoot,
        helperMutableRootPathSha256: windowsPathSha256(protectedSourceRoot),
        devVars: {
          sourcePath: devVarsSource,
          releaseLink: ".dev.vars",
          workerLink: "dist/server/.dev.vars",
          mode: "hard-link-only",
        },
      },
      artifacts: {
        keyFiles,
        contractReceipt: {
          version: contractReceiptVersion,
          relativePath: contractRelativePath,
          sha256: contractReceiptSha256,
        },
        guardReceipt: {
          version: "teruisi-legacy-worker-guard-receipt-v1",
          relativePath: guardRelativePath,
          sha256: guardReceiptSha256,
        },
        helperReceipt: {
          version: helperReceiptVersion,
          relativePath: helperReceiptRelativePath,
          sha256: helperReceiptSha256,
        },
      },
      processIdentity: {
        supervisorEntrypoint: "tools/worker-local-runtime-supervisor.mjs",
        serviceControl: "tools/worker-local-service.ps1",
        manifestFile: manifestFileName,
        processReceipt: "state/worker-process.json",
        processReceiptVersion,
        wranglerEntrypoint: "node_modules/wrangler/bin/wrangler.js",
        wranglerCliEntrypoint: "node_modules/wrangler/wrangler-dist/cli.js",
        helperEntrypoint: "helper/tmall-workflow-helper.mjs",
        fixedHelperArguments: ["serve", "--port", String(workerHelperPort)],
        fixedWranglerArguments: [
          "dev", "--config", "dist/server/wrangler.json", "--port", String(workerPort),
          "--ip", workerHost, "--persist-to", persistRoot,
        ],
      },
    };
    if (publicationMode === "rotation-candidate") {
      const retirementReceipt = createD1RetirementReceipt(retirementProof, manifestCore);
      await writeCanonicalJson(path.join(releaseStage, ...d1ReceiptRelativePath.split("/")), retirementReceipt);
      const retirementReceiptSha256 = sha256Bytes(Buffer.from(`${canonicalJson(retirementReceipt)}\n`));
      manifestCore.artifacts.d1RetirementReceipt = {
        version: d1ReceiptVersion, relativePath: d1ReceiptRelativePath, sha256: retirementReceiptSha256,
      };
    }
    const manifest = withPayloadSha256(manifestCore, "manifestPayloadSha256");
    await writeCanonicalJson(path.join(releaseStage, manifestFileName), manifest);
    await createHardLinkOnly(devVarsSource, path.join(releaseStage, ".dev.vars"));
    await createHardLinkOnly(devVarsSource, path.join(releaseStage, "dist", "server", ".dev.vars"));

    await rename(releaseStage, releaseRoot);
    const manifestPath = path.join(releaseRoot, manifestFileName);
    const manifestSha256 = sha256Bytes(await readFile(manifestPath));
    try {
      await verifyWorkerReleaseInternal({
        manifestPath,
        approvedManifestSha256: manifestSha256,
        expectedSourceD1PathSha256: windowsPathSha256(sourceD1Path),
        expectedPersistRootPathSha256: windowsPathSha256(persistRoot),
        expectedHost: workerHost,
        expectedPort: workerPort,
        requireSalesRetiredCodeReceipt: true,
        processPolicy: "stopped",
        allowTestRuntimeRoot,
      }, verifyPreparationPredecessor);
    } catch (error) {
      await rm(releaseRoot, { recursive: true, force: true }).catch(() => {});
      throw error;
    }
    if (verifyPreparationPredecessor) {
      await verifyPreparationPredecessor();
    } else {
      const processReceiptPath = path.join(runtimeRoot, "state", "worker-process.json");
      if (await pathExists(processReceiptPath)) {
        await rm(releaseRoot, { recursive: true, force: true }).catch(() => {});
        fail("Worker process receipt 仍存在，拒绝切换 current release");
      }
      if (await probeAnyLocalPort(workerPort) || await probeAnyLocalPort(workerHelperPort)) {
        await rm(releaseRoot, { recursive: true, force: true }).catch(() => {});
        fail("3000/5791 端口在 current pointer 发布前被占用，拒绝切换 release");
      }
    }
    if (publicationMode === "first-deploy") {
      try {
        await assertOrdinaryDeployAllowed({
          runtimeRoot,
          sourceD1Path,
          allowTestRuntimeRoot,
        });
      } catch (error) {
        await rm(releaseRoot, { recursive: true, force: true }).catch(() => {});
        throw error;
      }
      const currentPointer = withPayloadSha256({
        version: "teruisi-local-worker-current-v1",
        releaseId,
        manifestRelativePath: `releases/${releaseId}/${manifestFileName}`,
        manifestSha256,
      }, "pointerPayloadSha256");
      await publishFirstCurrentPointer({ runtimeRoot, releaseRoot, currentPointer, allowTestRuntimeRoot });
    }

    return {
      status: publicationMode === "first-deploy" ? "deployed" : "candidate_built",
      version: releaseVersion,
      releaseId,
      manifestPath,
      manifestSha256,
      sourceFingerprint,
      buildFingerprint,
    };
  } finally {
    await rm(stageRoot, { recursive: true, force: true }).catch(() => {});
  }
}

export async function deployWorkerRelease(options = {}) {
  return buildWorkerReleaseInternal({ ...options, publicationMode: "first-deploy" });
}

export async function buildWorkerReleaseCandidate(options = {}) {
  return buildWorkerReleaseInternal({ ...options, publicationMode: "rotation-candidate" });
}

function validatePayloadSha(record, fieldName, label) {
  const value = record[fieldName];
  if (typeof value !== "string" || !hex64.test(value)) fail(`${label} ${fieldName}无效`);
  const core = { ...record };
  delete core[fieldName];
  if (sha256Canonical(core) !== value) fail(`${label}自哈希无效`);
}

async function validateTree(root, expected, label, options) {
  assertExactKeys(expected, ["algorithm", "fileCount", "sha256"], `${label} tree`);
  if (expected.algorithm !== treeHashAlgorithm || !Number.isSafeInteger(expected.fileCount) || expected.fileCount < 1 || !hex64.test(expected.sha256)) {
    fail(`${label} tree 契约无效`);
  }
  await assertNoReparsePoint(root, { label: `${label} root` });
  const actual = await hashTree(root, options);
  if (canonicalJson(actual) !== canonicalJson(expected)) fail(`${label} tree 内容与 manifest 不一致`);
}

async function verifyHardLinks(manifest, releaseRoot) {
  const devVars = manifest.runtime.devVars;
  assertExactKeys(devVars, ["sourcePath", "releaseLink", "workerLink", "mode"], "devVars 契约");
  if (devVars.mode !== "hard-link-only" || devVars.releaseLink !== ".dev.vars" || devVars.workerLink !== "dist/server/.dev.vars") {
    fail("devVars 硬链接契约无效");
  }
  const source = devVars.sourcePath;
  const releaseLink = path.join(releaseRoot, devVars.releaseLink);
  const workerLink = path.join(releaseRoot, ...devVars.workerLink.split("/"));
  await assertRegularFile(source, ".dev.vars 源");
  await assertRegularFile(releaseLink, "release .dev.vars 硬链接");
  await assertRegularFile(workerLink, "Worker .dev.vars 硬链接");
  const [sourceInfo, releaseInfo, workerInfo] = await Promise.all([
    stat(source, { bigint: true }), stat(releaseLink, { bigint: true }), stat(workerLink, { bigint: true }),
  ]);
  if ([releaseInfo, workerInfo].some((item) => item.dev !== sourceInfo.dev || item.ino !== sourceInfo.ino)) {
    fail(".dev.vars 必须只以同一文件的硬链接存在");
  }
}

export async function probeAnyLocalPort(port) {
  const canConnect = (host) => new Promise((resolveProbe, rejectProbe) => {
    const socket = createConnection({ host, port });
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolveProbe(value);
    };
    socket.setTimeout(500);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", (error) => {
      if (["ECONNREFUSED", "EHOSTUNREACH", "ENETUNREACH", "EADDRNOTAVAIL"].includes(error?.code)) {
        finish(false);
      } else {
        settled = true;
        socket.destroy();
        rejectProbe(error);
      }
    });
  });
  const cannotBind = (host, options = {}) => new Promise((resolveProbe, rejectProbe) => {
    const server = createServer();
    server.unref();
    server.once("error", (error) => {
      if (error && typeof error === "object" && error.code === "EADDRINUSE") resolveProbe(true);
      else rejectProbe(error);
    });
    server.listen({ host, port, exclusive: true, ...options }, () => {
      server.close((error) => (error ? rejectProbe(error) : resolveProbe(false)));
    });
  });
  if (await canConnect("127.0.0.1") || await canConnect("::1")) return true;
  if (await cannotBind("0.0.0.0")) return true;
  return cannotBind("::", { ipv6Only: true });
}

function waitForProcessStateRetry(delayMs) {
  return new Promise((resolveWait) => setTimeout(resolveWait, delayMs));
}

export const supervisorPrelaunchReceiptWaitBudgetMs = 15_000;
export const supervisorPrelaunchReceiptRetryDelayMs = 250;

export function isAcceptedExactWorkerProcessStatus(result, {
  acceptedStates = ["exact_release"],
  expectedSupervisorPid,
} = {}) {
  return result?.version === "teruisi-local-worker-status-v1"
    && acceptedStates.includes(result.state)
    && (expectedSupervisorPid === undefined || result.supervisorProcessId === expectedSupervisorPid);
}

async function exactReleaseProcessState(manifestPath, releaseRoot, {
  acceptedStates = ["exact_release"],
  expectedSupervisorPid,
  attempts = 1,
  retryDelayMs = 100,
} = {}) {
  const service = path.join(releaseRoot, ..."tools/worker-local-service.ps1".split("/"));
  let lastState = "unknown";
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const { stdout } = await runProcess("powershell.exe", [
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", service,
      "-Action", "Status", "-ManifestPath", manifestPath, "-Json",
    ], { label: "Worker 精确进程状态核验" });
    const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length !== 1) fail("Worker 状态输出不唯一");
    const result = JSON.parse(lines[0]);
    lastState = typeof result.state === "string" ? result.state : "unknown";
    if (isAcceptedExactWorkerProcessStatus(result, { acceptedStates, expectedSupervisorPid })) {
      return result.state;
    }
    if (attempt < attempts) await waitForProcessStateRetry(retryDelayMs);
  }
  if (expectedSupervisorPid !== undefined) {
    fail(`不可变 Worker supervisor 未建立当前 PID 的精确启动身份（state=${lastState}）`);
  }
  fail("3000/5791 端口进程不是该不可变 release");
}

async function assertTrustedStoppedProcessState(runtimeRoot) {
  const stateRoot = path.join(runtimeRoot, "state");
  await assertNoReparsePoint(stateRoot, { label: "Worker runtime state 根" });
  const processReceiptPath = path.join(stateRoot, "worker-process.json");
  if (await pathExists(processReceiptPath)) {
    await assertRegularFile(processReceiptPath, "Worker process receipt");
    fail("stopped policy 下不得残留 Worker process receipt");
  }
  const script = [
    "$ErrorActionPreference = 'Stop'",
    "$runtime = [System.IO.Path]::GetFullPath($env:TERUISI_TRUSTED_WORKER_RUNTIME).TrimEnd('\\') + '\\releases\\'",
    "$suffix = '\\tools\\worker-local-runtime-supervisor.mjs'",
    "$matches = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {",
    "  $name = ([string]$_.Name).ToLowerInvariant()",
    "  $command = [string]$_.CommandLine",
    "  $name -in @('node','node.exe') -and -not [string]::IsNullOrWhiteSpace($command) -and",
    "    $command.IndexOf($runtime, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and",
    "    $command.IndexOf($suffix, [System.StringComparison]::OrdinalIgnoreCase) -ge 0",
    "})",
    "[Console]::Out.WriteLine($matches.Count)",
  ].join("\n");
  const { stdout } = await runProcess("powershell.exe", [
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script,
  ], {
    label: "trusted Worker supervisor process fence",
    env: { ...process.env, TERUISI_TRUSTED_WORKER_RUNTIME: runtimeRoot },
  });
  if (!/^0\r?\n?$/.test(stdout)) fail("stopped policy 下仍存在 Worker runtime supervisor");
}

export async function assertSupervisorPrelaunchProcessState({
  manifestPath,
  releaseRoot,
  expectedSupervisorPid,
  waitBudgetMs = supervisorPrelaunchReceiptWaitBudgetMs,
  retryDelayMs = supervisorPrelaunchReceiptRetryDelayMs,
} = {}) {
  if (!Number.isSafeInteger(expectedSupervisorPid) || expectedSupervisorPid <= 0) {
    fail("supervisor-prelaunch policy 要求当前 supervisor PID");
  }
  if (!Number.isSafeInteger(waitBudgetMs) || waitBudgetMs < 100 || waitBudgetMs > 30_000) {
    fail("supervisor prelaunch receipt 等待预算无效");
  }
  if (!Number.isSafeInteger(retryDelayMs) || retryDelayMs < 10 || retryDelayMs > 1_000) {
    fail("supervisor prelaunch receipt 重试间隔无效");
  }
  const absoluteManifestPath = path.resolve(manifestPath);
  const absoluteReleaseRoot = path.resolve(releaseRoot);
  if (path.dirname(absoluteManifestPath) !== absoluteReleaseRoot
    || path.basename(absoluteManifestPath) !== "deployment-manifest.json"
    || path.basename(path.dirname(absoluteReleaseRoot)).toLowerCase() !== "releases") {
    fail("supervisor prelaunch manifest/release 路径无效");
  }
  const runtimeRoot = path.resolve(absoluteReleaseRoot, "..", "..");
  await assertNoReparsePoint(runtimeRoot, { label: "Worker runtime 根" });
  await assertNoReparsePoint(absoluteReleaseRoot, { label: "Worker release 根" });
  const manifestRaw = await readStableRegularFile(absoluteManifestPath, "Worker release manifest");
  let manifest;
  try {
    manifest = JSON.parse(manifestRaw.toString("utf8"));
  } catch {
    fail("Worker release manifest 不是有效 JSON");
  }
  assertExactKeys(manifest, [
    "version", "releaseId", "createdAt", "source", "build", "runtime", "artifacts", "processIdentity", "manifestPayloadSha256",
  ], "Worker release manifest");
  validatePayloadSha(manifest, "manifestPayloadSha256", "Worker release manifest");
  if (!manifestRaw.equals(Buffer.from(`${canonicalJson(manifest)}\n`, "utf8"))
    || manifest.version !== "teruisi-local-worker-release-v1"
    || manifest.releaseId !== path.basename(absoluteReleaseRoot)
    || manifest.runtime?.runtimeRootPathSha256 !== windowsPathSha256(runtimeRoot)
    || manifest.runtime?.releaseRootPathSha256 !== windowsPathSha256(absoluteReleaseRoot)
    || manifest.processIdentity?.supervisorEntrypoint !== "tools/worker-local-runtime-supervisor.mjs"
    || manifest.processIdentity?.processReceipt !== "state/worker-process.json"
    || manifest.processIdentity?.processReceiptVersion !== processReceiptVersion) {
    fail("supervisor prelaunch manifest 身份无效");
  }
  const manifestSha256 = sha256Bytes(manifestRaw);
  const stateRoot = path.join(runtimeRoot, "state");
  const receiptPath = path.join(stateRoot, "worker-process.json");
  const startedAt = Date.now();
  while (Date.now() - startedAt <= waitBudgetMs) {
    if (await pathExists(receiptPath)) {
      await assertNoReparsePoint(stateRoot, { label: "Worker runtime state 根" });
      const receiptRaw = await readStableRegularFile(receiptPath, "Worker process receipt");
      let receipt;
      try {
        receipt = JSON.parse(receiptRaw.toString("utf8"));
      } catch {
        fail("Worker process receipt 不是有效 JSON");
      }
      assertExactKeys(receipt, [
        "manifestPathSha256", "manifestSha256", "receiptPayloadSha256", "releaseId", "supervisorCreationDate",
        "supervisorEntrypointPathSha256", "supervisorPid", "version",
      ], "Worker process receipt");
      if (!receiptRaw.equals(Buffer.from(`${canonicalJson(receipt)}\n`, "utf8"))) {
        fail("Worker process receipt 不是 canonical JSON");
      }
      validatePayloadSha(receipt, "receiptPayloadSha256", "Worker process receipt");
      const creationTime = Date.parse(receipt.supervisorCreationDate);
      if (receipt.version !== processReceiptVersion
        || receipt.releaseId !== manifest.releaseId
        || receipt.manifestSha256 !== manifestSha256
        || receipt.manifestPathSha256 !== windowsPathSha256(absoluteManifestPath)
        || receipt.supervisorEntrypointPathSha256 !== windowsPathSha256(path.join(absoluteReleaseRoot, "tools", "worker-local-runtime-supervisor.mjs"))
        || receipt.supervisorPid !== expectedSupervisorPid
        || typeof receipt.supervisorCreationDate !== "string"
        || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z$/.test(receipt.supervisorCreationDate)
        || !Number.isFinite(creationTime)) {
        fail("Worker process receipt 未绑定当前 supervisor/release 身份");
      }
      if (expectedSupervisorPid === process.pid) {
        const estimatedProcessStart = Date.now() - (process.uptime() * 1_000);
        if (Math.abs(creationTime - estimatedProcessStart) > 10_000) {
          fail("Worker process receipt creation identity 与当前 supervisor 启动时间不一致");
        }
      }
      return "starting_exact_release";
    }
    const remaining = waitBudgetMs - (Date.now() - startedAt);
    if (remaining <= 0) break;
    await waitForProcessStateRetry(Math.min(retryDelayMs, remaining));
  }
  fail("不可变 Worker supervisor 未在有界时间内取得 create-only process receipt");
}

export async function verifyWorkerReleaseProcessState({
  processPolicy,
  runtimeRoot,
  manifestPath,
  releaseRoot,
  expectedSupervisorPid,
  probePort = probeAnyLocalPort,
  readSupervisorPrelaunchState = assertSupervisorPrelaunchProcessState,
  assertStoppedState = assertTrustedStoppedProcessState,
} = {}) {
  if (!["stopped", "stopped-or-exact-release", "supervisor-prelaunch"].includes(processPolicy)) {
    fail("process policy 无效");
  }
  if (processPolicy === "supervisor-prelaunch") {
    if (!Number.isSafeInteger(expectedSupervisorPid) || expectedSupervisorPid <= 0) {
      fail("supervisor-prelaunch policy 要求当前 supervisor PID");
    }
    return readSupervisorPrelaunchState({ manifestPath, releaseRoot, expectedSupervisorPid });
  }

  const portInUse = await probePort(workerPort);
  const helperPortInUse = await probePort(workerHelperPort);
  if (portInUse || helperPortInUse) {
    if (processPolicy === "stopped") fail("formal gate 要求 3000/5791 两端口完全停止");
    return exactReleaseProcessState(manifestPath, releaseRoot);
  }
  await assertStoppedState(runtimeRoot);
  return "stopped";
}

function validateSupervisorPrelaunchVerificationReceipt(
  receipt,
  {
    manifestPath,
    approvedManifestSha256,
    releaseRoot,
    sourceFingerprint,
    buildFingerprint,
    contractReceiptSha256,
    now = Date.now(),
    allowExpired = false,
  },
) {
  assertExactKeys(receipt, [
    "buildFingerprint", "contractReceiptSha256", "issuedAtUnixMilliseconds", "manifestPathSha256",
    "manifestSha256", "nonce", "receiptPayloadSha256", "releaseId", "releaseRootPathSha256",
    "sourceFingerprint", "version",
  ], "Worker supervisor prelaunch verification receipt");
  validatePayloadSha(receipt, "receiptPayloadSha256", "Worker supervisor prelaunch verification receipt");
  const age = now - receipt.issuedAtUnixMilliseconds;
  if (receipt.version !== supervisorPrelaunchVerificationReceiptVersion
    || receipt.releaseId !== path.basename(releaseRoot)
    || receipt.manifestSha256 !== approvedManifestSha256
    || receipt.manifestPathSha256 !== windowsPathSha256(manifestPath)
    || receipt.releaseRootPathSha256 !== windowsPathSha256(releaseRoot)
    || receipt.sourceFingerprint !== sourceFingerprint || !hex64.test(receipt.sourceFingerprint ?? "")
    || receipt.buildFingerprint !== buildFingerprint || !hex64.test(receipt.buildFingerprint ?? "")
    || receipt.contractReceiptSha256 !== contractReceiptSha256 || !hex64.test(receipt.contractReceiptSha256 ?? "")
    || typeof receipt.nonce !== "string" || !/^[0-9a-f-]{36}$/.test(receipt.nonce)
    || !Number.isSafeInteger(receipt.issuedAtUnixMilliseconds)
    || age < -5_000 || (!allowExpired && age > supervisorPrelaunchVerificationReceiptMaxAgeMs)) {
    fail("Worker supervisor prelaunch verification receipt 身份或时效无效");
  }
  return age;
}

async function readSupervisorPrelaunchVerificationReceipt(receiptPath, identity) {
  const raw = await readStableRegularFile(receiptPath, "Worker supervisor prelaunch verification receipt");
  let receipt;
  try {
    receipt = JSON.parse(raw.toString("utf8"));
  } catch {
    fail("Worker supervisor prelaunch verification receipt 不是有效 JSON");
  }
  if (!raw.equals(Buffer.from(`${canonicalJson(receipt)}\n`, "utf8"))) {
    fail("Worker supervisor prelaunch verification receipt 不是 canonical JSON");
  }
  validateSupervisorPrelaunchVerificationReceipt(receipt, identity);
  return { raw, receipt, sha256: sha256Bytes(raw) };
}

async function publishSupervisorPrelaunchVerificationReceipt({
  runtimeRoot,
  releaseRoot,
  manifestPath,
  approvedManifestSha256,
  verification,
}) {
  const stateRoot = path.join(runtimeRoot, "state");
  const receiptPath = path.join(runtimeRoot, ...supervisorPrelaunchVerificationReceiptRelativePath.split("/"));
  await assertNoReparsePoint(stateRoot, { label: "Worker runtime state 根" });
  if (await pathExists(receiptPath)) {
    const existing = await readSupervisorPrelaunchVerificationReceipt(receiptPath, {
      manifestPath,
      approvedManifestSha256,
      releaseRoot,
      sourceFingerprint: verification.sourceFingerprint,
      buildFingerprint: verification.buildFingerprint,
      contractReceiptSha256: verification.contractReceiptSha256,
      allowExpired: true,
    });
    const age = Date.now() - existing.receipt.issuedAtUnixMilliseconds;
    if (age <= supervisorPrelaunchVerificationReceiptMaxAgeMs) {
      fail("Worker supervisor prelaunch verification receipt 仍在有效期内，拒绝覆盖");
    }
    // Only a canonical, identity-bound, expired receipt from an interrupted
    // prior Start is recoverable. Invalid evidence remains fail-closed.
    await unlink(receiptPath);
  }
  const receipt = withPayloadSha256({
    version: supervisorPrelaunchVerificationReceiptVersion,
    releaseId: path.basename(releaseRoot),
    manifestSha256: approvedManifestSha256,
    manifestPathSha256: windowsPathSha256(manifestPath),
    releaseRootPathSha256: windowsPathSha256(releaseRoot),
    sourceFingerprint: verification.sourceFingerprint,
    buildFingerprint: verification.buildFingerprint,
    contractReceiptSha256: verification.contractReceiptSha256,
    issuedAtUnixMilliseconds: Date.now(),
    nonce: randomUUID(),
  }, "receiptPayloadSha256");
  const bytes = Buffer.from(`${canonicalJson(receipt)}\n`, "utf8");
  await writeFileAtomicCreateOnly(receiptPath, bytes);
  const published = await readSupervisorPrelaunchVerificationReceipt(receiptPath, {
    manifestPath,
    approvedManifestSha256,
    releaseRoot,
    sourceFingerprint: verification.sourceFingerprint,
    buildFingerprint: verification.buildFingerprint,
    contractReceiptSha256: verification.contractReceiptSha256,
  });
  if (!published.raw.equals(bytes)) fail("Worker supervisor prelaunch verification receipt 发布后回读不一致");
  return published.sha256;
}

export async function consumeSupervisorPrelaunchVerificationReceipt({
  manifestPath,
  approvedManifestSha256,
  releaseRoot,
} = {}) {
  const absoluteManifestPath = path.resolve(manifestPath);
  const absoluteReleaseRoot = path.resolve(releaseRoot);
  if (path.dirname(absoluteManifestPath) !== absoluteReleaseRoot
    || path.basename(absoluteManifestPath) !== manifestFileName
    || path.basename(path.dirname(absoluteReleaseRoot)).toLowerCase() !== "releases"
    || !releaseIdPattern.test(path.basename(absoluteReleaseRoot))
    || !hex64.test(approvedManifestSha256 ?? "")) {
    fail("Worker supervisor prelaunch verification receipt manifest/release 身份无效");
  }
  const runtimeRoot = path.resolve(absoluteReleaseRoot, "..", "..");
  const stateRoot = path.join(runtimeRoot, "state");
  const receiptPath = path.join(runtimeRoot, ...supervisorPrelaunchVerificationReceiptRelativePath.split("/"));
  await assertNoReparsePoint(stateRoot, { label: "Worker runtime state 根" });
  const manifestRaw = await readStableRegularFile(absoluteManifestPath, "Worker release manifest");
  if (sha256Bytes(manifestRaw) !== approvedManifestSha256) fail("Worker release manifest 原始文件哈希未获批准");
  let manifest;
  try { manifest = JSON.parse(manifestRaw.toString("utf8")); } catch { fail("Worker release manifest 不是有效 JSON"); }
  const published = await readSupervisorPrelaunchVerificationReceipt(receiptPath, {
    manifestPath: absoluteManifestPath,
    approvedManifestSha256,
    releaseRoot: absoluteReleaseRoot,
    sourceFingerprint: manifest.source?.sourceFingerprint,
    buildFingerprint: manifest.build?.buildFingerprint,
    contractReceiptSha256: manifest.artifacts?.contractReceipt?.sha256,
  });
  await unlink(receiptPath);
  if (await pathExists(receiptPath)) fail("Worker supervisor prelaunch verification receipt 未被一次性消费");
  return published.receipt;
}

export async function validateGuardReceipt(manifest, releaseRoot, { verifyInstalledEntrypoints = false } = {}) {
  const pointer = manifest.artifacts.guardReceipt;
  assertExactKeys(pointer, ["version", "relativePath", "sha256"], "guard receipt pointer");
  if (pointer.version !== "teruisi-legacy-worker-guard-receipt-v1"
    || pointer.relativePath !== "audit/legacy-worker-guard-receipt.json" || !hex64.test(pointer.sha256 ?? "")) {
    fail("guard receipt pointer 无效");
  }
  const receiptPath = path.join(releaseRoot, ...pointer.relativePath.split("/"));
  await assertRegularFile(receiptPath, "legacy worker guard receipt");
  const receiptRead = await readCanonicalJson(receiptPath, "legacy worker guard receipt");
  if (receiptRead.sha256 !== pointer.sha256) fail("guard receipt 原始文件哈希无效");
  const receipt = receiptRead.value;
  assertExactKeys(receipt, [
    "version", "generatedAt", "sourceFingerprint", "status", "bindings", "checks", "entrypoints",
    "forbiddenLegacyDirectCommands", "receiptPayloadSha256",
  ], "legacy worker guard receipt");
  validatePayloadSha(receipt, "receiptPayloadSha256", "legacy worker guard receipt");
  if (receipt.version !== "teruisi-legacy-worker-guard-receipt-v1"
    || receipt.sourceFingerprint !== manifest.source.sourceFingerprint || receipt.status !== "passed") fail("guard receipt 身份无效");
  assertExactKeys(receipt.bindings, [
    "protectedSourceRoot", "protectedSourceRootPathSha256", "persistRoot", "persistRootPathSha256", "sourceD1Path",
    "sourceD1PathSha256", "authorityRelativePath", "authoritySidecarRelativePath",
  ], "guard receipt bindings");
  if (receipt.bindings.protectedSourceRootPathSha256 !== windowsPathSha256(receipt.bindings.protectedSourceRoot)
    || receipt.bindings.persistRootPathSha256 !== windowsPathSha256(receipt.bindings.persistRoot)
    || receipt.bindings.sourceD1PathSha256 !== windowsPathSha256(receipt.bindings.sourceD1Path)
    || receipt.bindings.persistRootPathSha256 !== manifest.runtime.persistRootPathSha256
    || receipt.bindings.sourceD1PathSha256 !== manifest.runtime.sourceD1PathSha256
    || receipt.bindings.protectedSourceRootPathSha256 !== manifest.runtime.protectedSourceRootPathSha256
    || receipt.bindings.authorityRelativePath !== "state/sales-postgresql-authority.json"
    || receipt.bindings.authoritySidecarRelativePath !== "state/sales-postgresql-authority.json.sha256") fail("guard receipt 路径绑定无效");
  assertExactKeys(receipt.checks, workerGuardCheckNames, "guard receipt checks");
  if (Object.values(receipt.checks).some((value) => value !== true)) fail("guard receipt 检查未全部通过");
  if (!Array.isArray(receipt.entrypoints)
    || canonicalJson(receipt.entrypoints.map((item) => item?.relativePath)) !== canonicalJson([...workerGuardEntrypointPaths])
    || canonicalJson(receipt.forbiddenLegacyDirectCommands) !== canonicalJson(workerGuardForbiddenScans)) {
    fail("guard receipt 证据集无效");
  }
  const relativePaths = receipt.entrypoints.map((item) => item.relativePath);
  if (new Set(relativePaths).size !== relativePaths.length) fail("guard receipt 入口文件重复");
  for (const item of receipt.entrypoints) {
    assertExactKeys(item, ["relativePath", "sha256"], "guard receipt entrypoint");
    if (!workerGuardEntrypointPaths.includes(item.relativePath) || !hex64.test(item.sha256 ?? "")) fail("guard receipt entrypoint 无效");
    const keyFile = manifest.artifacts.keyFiles.find((candidate) => candidate.relativePath === item.relativePath);
    if (!keyFile || keyFile.sha256 !== item.sha256) fail(`guard receipt entrypoint 未绑定发布关键哈希：${item.relativePath}`);
    const bundled = path.join(releaseRoot, ...item.relativePath.split("/"));
    await assertRegularFile(bundled, `bundled guard entrypoint ${item.relativePath}`);
    if (sha256Bytes(await readFile(bundled)) !== item.sha256) fail(`bundled guard entrypoint 未匹配 receipt：${item.relativePath}`);
  }
  for (const item of receipt.forbiddenLegacyDirectCommands) {
    assertExactKeys(item, ["scope", "pattern", "matches"], "guard forbidden scan");
    if (!Array.isArray(item.matches) || item.matches.length !== 0) fail("guard forbidden scan 命中了旧入口");
  }
  if (verifyInstalledEntrypoints) {
    await assertNoReparsePoint(receipt.bindings.protectedSourceRoot, { label: "protected source root" });
    for (const item of receipt.entrypoints) {
      const installed = path.join(receipt.bindings.protectedSourceRoot, ...item.relativePath.split("/"));
      await assertRegularFile(installed, `installed guard entrypoint ${item.relativePath}`);
      if (sha256Bytes(await readFile(installed)) !== item.sha256) fail(`installed guard entrypoint 未匹配 receipt：${item.relativePath}`);
    }
  }
  return { receipt, sha256: pointer.sha256 };
}

async function validateActivationFence(manifest, releaseRoot, guardReceipt) {
  const entries = guardReceipt.entrypoints.filter((item) => item.relativePath === activationFenceRelativePath);
  if (entries.length !== 1) fail("guard receipt 缺少唯一 Worker release activation fence");
  const fencePath = path.join(releaseRoot, ...activationFenceRelativePath.split("/"));
  const read = await readCanonicalJson(fencePath, "Worker release activation fence");
  if (read.sha256 !== entries[0].sha256) fail("Worker release activation fence 未绑定 guard receipt");
  assertExactKeys(read.value, [
    "version", "createdAt", "sourceFingerprint", "buildFingerprint", "payloadSha256",
  ], "Worker release activation fence");
  validatePayloadSha(read.value, "payloadSha256", "Worker release activation fence");
  const expected = workerReleaseActivationFence({
    createdAt: manifest.createdAt,
    sourceFingerprint: manifest.source.sourceFingerprint,
    buildFingerprint: manifest.build.buildFingerprint,
  });
  if (canonicalJson(read.value) !== canonicalJson(expected)) fail("Worker release activation fence 未绑定 immutable build identity");
  return read;
}

async function validateContractTests(contractTests, manifest, releaseRoot) {
  assertExactKeys(contractTests, [
    "runner", "testFiles", "summary", "tapSha256", "stderrSha256", "wallDurationMs", "sourceFingerprint", "buildFingerprint",
  ], "sales contract tests");
  assertExactKeys(contractTests.runner, ["executable", "nodeVersion", "arguments"], "sales contract test runner");
  assertExactKeys(contractTests.summary, ["tests", "pass", "fail", "cancelled", "skipped", "todo", "durationMs"], "sales contract test summary");
  const integerSummaryFields = ["tests", "pass", "fail", "cancelled", "skipped", "todo"];
  if (contractTests.runner.executable !== "node" || contractTests.runner.nodeVersion !== manifest.build.nodeVersion
    || canonicalJson(contractTests.runner.arguments) !== canonicalJson(["--import", "tsx", "--test", "--test-reporter=tap", ...salesContractTestFiles])
    || canonicalJson(contractTests.testFiles.map((item) => item.relativePath)) !== canonicalJson([...salesContractTestFiles])
    || integerSummaryFields.some((field) => !Number.isSafeInteger(contractTests.summary[field]) || contractTests.summary[field] < 0)
    || contractTests.summary.tests < 1 || contractTests.summary.pass !== contractTests.summary.tests
    || contractTests.summary.fail !== 0 || contractTests.summary.cancelled !== 0
    || contractTests.summary.skipped !== 0 || contractTests.summary.todo !== 0
    || !Number.isFinite(contractTests.summary.durationMs) || contractTests.summary.durationMs < 0
    || !Number.isSafeInteger(contractTests.wallDurationMs) || contractTests.wallDurationMs < 0
    || contractTests.sourceFingerprint !== manifest.source.sourceFingerprint
    || contractTests.buildFingerprint !== manifest.build.buildFingerprint
    || !hex64.test(contractTests.tapSha256 ?? "") || !hex64.test(contractTests.stderrSha256 ?? "")) {
    fail("sales contract test receipt 无效");
  }
  for (const item of contractTests.testFiles) {
    assertExactKeys(item, ["relativePath", "sha256"], "sales contract test file");
    if (!hex64.test(item.sha256 ?? "")) fail("sales contract test file hash 无效");
    const snapshotFile = path.join(releaseRoot, manifest.source.snapshotRoot, ...item.relativePath.split("/"));
    if (sha256Bytes(await readFile(snapshotFile)) !== item.sha256) fail("sales contract test file 与 source snapshot 不一致");
  }
}

async function validateHelperReceipt(manifest, releaseRoot) {
  const trustedBuilderPath = path.join(releaseRoot, manifest.source.snapshotRoot, "tools", "build-worker-helper.mjs");
  await assertRegularFile(trustedBuilderPath, "trusted immutable helper builder source");
  if (!hex64.test(trustedHelperBuilderSha256)
    || sha256Bytes(await readFile(trustedBuilderPath)) !== trustedHelperBuilderSha256) {
    fail("immutable helper builder 未匹配 Django trusted verifier 固定 SHA-256");
  }
  const pointer = manifest.artifacts.helperReceipt;
  assertExactKeys(pointer, ["version", "relativePath", "sha256"], "helper receipt pointer");
  if (pointer.version !== helperReceiptVersion || pointer.relativePath !== "audit/helper-build-receipt.json"
    || !hex64.test(pointer.sha256 ?? "")) fail("helper receipt pointer 无效");
  const receiptPath = path.join(releaseRoot, ...pointer.relativePath.split("/"));
  await assertRegularFile(receiptPath, "helper build receipt");
  const read = await readCanonicalJson(receiptPath, "helper build receipt");
  if (read.sha256 !== pointer.sha256) fail("helper build receipt 原始文件哈希无效");
  const receipt = read.value;
  assertExactKeys(receipt, [
    "version", "sourceFingerprint", "buildFingerprint", "status", "mutableRootMode", "evidence", "helperTree", "receiptPayloadSha256",
  ], "helper build receipt");
  validatePayloadSha(receipt, "receiptPayloadSha256", "helper build receipt");
  if (receipt.version !== helperReceiptVersion || receipt.status !== "passed"
    || receipt.mutableRootMode !== "manifest-protected-source-root"
    || receipt.sourceFingerprint !== manifest.source.sourceFingerprint
    || receipt.buildFingerprint !== manifest.build.buildFingerprint
    || canonicalJson(receipt.helperTree) !== canonicalJson(manifest.build.helperTree)
    || sha256Canonical(receipt.evidence) !== manifest.build.helperBuildEvidenceSha256) {
    fail("helper build receipt 身份/绑定无效");
  }
  await validateHelperBuilderEvidence(
    receipt.evidence,
    path.join(releaseRoot, manifest.source.snapshotRoot),
    path.join(releaseRoot, manifest.build.helperRoot),
  );
  return { sha256: read.sha256, receipt };
}

export async function verifyWorkerRelease(options = {}) {
  return verifyWorkerReleaseInternal(options);
}

// Only the candidate builder can substitute a fully verified predecessor for
// candidate process admission. Public verification and all activation paths
// always use the candidate's own strict process gate.
async function verifyWorkerReleaseInternal({
  manifestPath,
  approvedManifestSha256,
  expectedSourceD1PathSha256,
  expectedPersistRootPathSha256,
  expectedHost = workerHost,
  expectedPort = workerPort,
  requireSalesRetiredCodeReceipt = false,
  processPolicy = "stopped-or-exact-release",
  expectedSupervisorPid,
  allowTestRuntimeRoot = false,
  writeSupervisorPrelaunchReceipt = false,
} = {}, verifyPreparationPredecessor) {
  if (typeof manifestPath !== "string" || !path.win32.isAbsolute(manifestPath)) fail("manifest 路径必须为绝对路径");
  if (verifyPreparationPredecessor && writeSupervisorPrelaunchReceipt) {
    fail("Candidate preparation cannot issue a supervisor launch receipt");
  }
  if (writeSupervisorPrelaunchReceipt && processPolicy !== "stopped") {
    fail("supervisor prelaunch verification receipt 只能由 stopped 完整校验发布");
  }
  manifestPath = path.resolve(manifestPath);
  const releaseRoot = path.dirname(manifestPath);
  const releaseId = path.basename(releaseRoot);
  const releasesRoot = path.dirname(releaseRoot);
  const runtimeRoot = path.dirname(releasesRoot);
  if (path.basename(manifestPath) !== manifestFileName || path.basename(releasesRoot).toLowerCase() !== "releases" || !releaseIdPattern.test(releaseId)) {
    fail("manifest 不在有界 release 路径中");
  }
  if (!allowTestRuntimeRoot && canonicalWindowsPath(runtimeRoot) !== canonicalWindowsPath(workerRuntimeRoot)) fail("Worker runtime 根路径无效");
  await assertNoReparsePoint(manifestPath, { label: "Worker release manifest" });
  const manifestRead = await readCanonicalJson(manifestPath, "Worker release manifest");
  if (!hex64.test(approvedManifestSha256 ?? "") || manifestRead.sha256 !== approvedManifestSha256) fail("manifest 原始文件哈希未获批准");
  const manifest = manifestRead.value;
  assertExactKeys(manifest, [
    "version", "releaseId", "createdAt", "source", "build", "runtime", "artifacts", "processIdentity", "manifestPayloadSha256",
  ], "Worker release manifest");
  validatePayloadSha(manifest, "manifestPayloadSha256", "Worker release manifest");
  if (manifest.version !== releaseVersion || manifest.releaseId !== releaseId) fail("Worker release manifest 身份无效");

  assertExactKeys(manifest.source, ["rootPathSha256", "packageLockSha256", "sourceFingerprint", "snapshotRoot", "tree"], "manifest source");
  assertExactKeys(manifest.build, [
    "nodeVersion", "nodeExecutablePathSha256", "npmVersion", "npmPackageRootRelativePath", "npmPackageRootPathSha256",
    "npmCliRelativePath", "npmCliPathSha256", "npmCliSha256",
    "npmPackageJsonRelativePath", "npmPackageJsonPathSha256", "npmPackageJsonSha256", "npmCiArguments",
    "npmPackageTree",
    "buildRoot", "distTree", "nodeModulesRoot", "nodeModulesTree",
    "helperRoot", "helperTree", "helperBuildEvidenceSha256", "buildFingerprint",
  ], "manifest build");
  assertExactKeys(manifest.runtime, [
    "runtimeRootPathSha256", "releaseRootPathSha256", "persistRootPathSha256", "sourceD1PathSha256", "host", "port",
    "persistRoot", "sourceD1Path", "protectedSourceRoot", "protectedSourceRootPathSha256", "cliOverridesAllowed", "helperMode",
    "helperHost", "helperPort", "helperMutableRoot", "helperMutableRootPathSha256", "devVars",
  ], "manifest runtime");
  assertExactKeys(manifest.artifacts, ["keyFiles", "contractReceipt", "guardReceipt", "helperReceipt",
    ...(manifest.artifacts.d1RetirementReceipt ? ["d1RetirementReceipt"] : [])], "manifest artifacts");
  if (manifest.artifacts.d1RetirementReceipt) await readD1RetirementReceipt(manifest, releaseRoot);
  assertExactKeys(manifest.processIdentity, [
    "supervisorEntrypoint", "serviceControl", "manifestFile", "processReceipt", "processReceiptVersion", "wranglerEntrypoint",
    "wranglerCliEntrypoint", "fixedWranglerArguments", "helperEntrypoint", "fixedHelperArguments",
  ], "manifest processIdentity");
  assertWorkerManifestProvenance(manifest, { allowTestRuntimeRoot });
  await assertBundledNpmToolchainProvenance({
    nodeVersion: manifest.build.nodeVersion,
    nodeExecutablePathSha256: manifest.build.nodeExecutablePathSha256,
    npmVersion: manifest.build.npmVersion,
    npmPackageRootRelativePath: manifest.build.npmPackageRootRelativePath,
    npmPackageRootPathSha256: manifest.build.npmPackageRootPathSha256,
    npmCliRelativePath: manifest.build.npmCliRelativePath,
    npmCliPathSha256: manifest.build.npmCliPathSha256,
    npmCliSha256: manifest.build.npmCliSha256,
    npmPackageJsonRelativePath: manifest.build.npmPackageJsonRelativePath,
    npmPackageJsonPathSha256: manifest.build.npmPackageJsonPathSha256,
    npmPackageJsonSha256: manifest.build.npmPackageJsonSha256,
    npmPackageTree: manifest.build.npmPackageTree,
    npmCiArguments: manifest.build.npmCiArguments,
  });
  const expectedWranglerArguments = [
    "dev", "--config", "dist/server/wrangler.json", "--port", String(workerPort),
    "--ip", workerHost, "--persist-to", manifest.runtime.persistRoot,
  ];
  const expectedHelperArguments = ["serve", "--port", String(workerHelperPort)];
  if (manifest.processIdentity.supervisorEntrypoint !== "tools/worker-local-runtime-supervisor.mjs"
    || manifest.processIdentity.serviceControl !== "tools/worker-local-service.ps1"
    || manifest.processIdentity.manifestFile !== manifestFileName
    || manifest.processIdentity.processReceipt !== "state/worker-process.json"
    || manifest.processIdentity.processReceiptVersion !== processReceiptVersion
    || manifest.processIdentity.wranglerEntrypoint !== "node_modules/wrangler/bin/wrangler.js"
    || manifest.processIdentity.wranglerCliEntrypoint !== "node_modules/wrangler/wrangler-dist/cli.js"
    || canonicalJson(manifest.processIdentity.fixedWranglerArguments) !== canonicalJson(expectedWranglerArguments)
    || manifest.processIdentity.helperEntrypoint !== "helper/tmall-workflow-helper.mjs"
    || canonicalJson(manifest.processIdentity.fixedHelperArguments) !== canonicalJson(expectedHelperArguments)) {
    fail("manifest process identity/固定 Wrangler 参数无效");
  }

  for (const value of [manifest.source.packageLockSha256, manifest.source.sourceFingerprint, manifest.build.buildFingerprint]) {
    if (typeof value !== "string" || !hex64.test(value)) fail("manifest fingerprint 无效");
  }
  if (manifest.source.sourceFingerprint !== manifest.source.tree?.sha256) fail("source fingerprint 未绑定 source tree");
  if (!/^v24\./.test(manifest.build.nodeVersion) || !/^v24\./.test(process.version)) {
    fail("Worker verifier/runtime 必须使用固定 Node 24.x");
  }
  if (manifest.runtime.runtimeRootPathSha256 !== windowsPathSha256(runtimeRoot)
    || manifest.runtime.releaseRootPathSha256 !== windowsPathSha256(releaseRoot)
    || manifest.runtime.persistRootPathSha256 !== windowsPathSha256(manifest.runtime.persistRoot)
    || manifest.runtime.sourceD1PathSha256 !== windowsPathSha256(manifest.runtime.sourceD1Path)
    || manifest.runtime.protectedSourceRootPathSha256 !== windowsPathSha256(manifest.runtime.protectedSourceRoot)
    || manifest.runtime.helperMutableRootPathSha256 !== windowsPathSha256(manifest.runtime.helperMutableRoot)) {
    fail("manifest 路径哈希自身不一致");
  }
  if (manifest.runtime.sourceD1PathSha256 !== expectedSourceD1PathSha256
    || manifest.runtime.persistRootPathSha256 !== expectedPersistRootPathSha256) fail("manifest D1/persist 路径未绑定到批准值");
  if (manifest.runtime.host !== expectedHost || manifest.runtime.port !== expectedPort
    || manifest.runtime.host !== workerHost || manifest.runtime.port !== workerPort
    || manifest.runtime.cliOverridesAllowed !== false || manifest.runtime.helperMode !== "supervisor_managed_immutable_bundle"
    || manifest.runtime.helperHost !== workerHelperHost || manifest.runtime.helperPort !== workerHelperPort
    || manifest.runtime.helperMutableRoot !== manifest.runtime.protectedSourceRoot
    || manifest.runtime.helperMutableRootPathSha256 !== manifest.runtime.protectedSourceRootPathSha256) {
    fail("manifest 回环或运行时策略无效");
  }
  if (!isWithinWindowsPath(manifest.runtime.persistRoot, manifest.runtime.sourceD1Path)) fail("manifest D1 不在 persist root 内");

  if (manifest.source.snapshotRoot !== "source-snapshot") fail("manifest source snapshot 路径无效");
  const sourceSnapshotRoot = path.join(releaseRoot, manifest.source.snapshotRoot);
  await validateTree(sourceSnapshotRoot, manifest.source.tree, "source snapshot");
  const snapshotPackageLock = path.join(sourceSnapshotRoot, "package-lock.json");
  await assertRegularFile(snapshotPackageLock, "source snapshot package-lock.json");
  if (sha256Bytes(await readFile(snapshotPackageLock)) !== manifest.source.packageLockSha256) {
    fail("packageLockSha256 未绑定 source snapshot 原始文件");
  }

  if (manifest.build.buildRoot !== "dist" || manifest.build.nodeModulesRoot !== "node_modules"
    || manifest.build.helperRoot !== "helper" || !hex64.test(manifest.build.helperBuildEvidenceSha256 ?? "")) {
    fail("manifest build 相对路径/helper evidence 无效");
  }
  await validateTree(path.join(releaseRoot, "dist"), manifest.build.distTree, "dist", { excluded: new Set(["server/.dev.vars"]) });
  await validateTree(path.join(releaseRoot, "node_modules"), manifest.build.nodeModulesTree, "node_modules");
  await validateTree(path.join(releaseRoot, "helper"), manifest.build.helperTree, "helper bundle");
  const expectedBuildFingerprint = sha256Canonical({
    packageLockSha256: manifest.source.packageLockSha256,
    distTree: manifest.build.distTree,
    nodeModulesTree: manifest.build.nodeModulesTree,
    helperTree: manifest.build.helperTree,
    helperBuildEvidenceSha256: manifest.build.helperBuildEvidenceSha256,
    npmToolchain: {
      nodeVersion: manifest.build.nodeVersion,
      nodeExecutablePathSha256: manifest.build.nodeExecutablePathSha256,
      npmVersion: manifest.build.npmVersion,
      npmPackageRootRelativePath: manifest.build.npmPackageRootRelativePath,
      npmPackageRootPathSha256: manifest.build.npmPackageRootPathSha256,
      npmCliRelativePath: manifest.build.npmCliRelativePath,
      npmCliPathSha256: manifest.build.npmCliPathSha256,
      npmCliSha256: manifest.build.npmCliSha256,
      npmPackageJsonRelativePath: manifest.build.npmPackageJsonRelativePath,
      npmPackageJsonPathSha256: manifest.build.npmPackageJsonPathSha256,
      npmPackageJsonSha256: manifest.build.npmPackageJsonSha256,
      npmPackageTree: manifest.build.npmPackageTree,
      npmCiArguments: manifest.build.npmCiArguments,
    },
  });
  if (manifest.build.buildFingerprint !== expectedBuildFingerprint) fail("build fingerprint 无效");

  const keyFiles = await deploymentKeyFiles(releaseRoot);
  if (canonicalJson(keyFiles) !== canonicalJson(manifest.artifacts.keyFiles)) fail("发布关键文件哈希无效");
  const candidateVerifier = keyFiles.find((item) => item.relativePath === "tools/worker-local-release.mjs");
  const trustedVerifierSha256 = await assertTrustedVerifierMatches(
    path.join(releaseRoot, "tools", "worker-local-release.mjs"), modulePath,
  );
  if (!candidateVerifier || candidateVerifier.sha256 !== trustedVerifierSha256) fail("candidate Worker verifier key-file 绑定无效");
  const contract = manifest.artifacts.contractReceipt;
  assertExactKeys(contract, ["version", "relativePath", "sha256"], "contract receipt pointer");
  if (contract.version !== contractReceiptVersion || contract.relativePath !== "audit/sales-retired-code-receipt.json" || !hex64.test(contract.sha256)) {
    fail("contract receipt pointer 无效");
  }
  const contractPath = path.join(releaseRoot, ...contract.relativePath.split("/"));
  await assertRegularFile(contractPath, "sales retired code receipt");
  const contractRead = await readCanonicalJson(contractPath, "sales retired code receipt");
  if (contractRead.sha256 !== contract.sha256) fail("contract receipt 原始文件哈希无效");
  const receipt = contractRead.value;
  assertExactKeys(receipt, [
    "version", "generatedAt", "sourceFingerprint", "buildFingerprint", "status", "checks", "evidence", "contractTests", "receiptPayloadSha256",
  ], "sales retired code receipt");
  validatePayloadSha(receipt, "receiptPayloadSha256", "sales retired code receipt");
  if (receipt.version !== contractReceiptVersion || receipt.sourceFingerprint !== manifest.source.sourceFingerprint
    || receipt.buildFingerprint !== manifest.build.buildFingerprint || receipt.status !== "passed") {
    fail("sales retired code receipt 身份无效");
  }
  if (requireSalesRetiredCodeReceipt) {
    assertExactKeys(receipt.checks, [
      "legacySalesSourcePathsAbsent", "salesRoutesUseDjango", "operatorOnlyRetirementMigrationPresent", "operatorOnlyRetirementMigrationExcludedFromJournal",
      "sharedImportRetirementGuardsPresent", "retirementOperatorAuditV4",
    ], "sales retired code checks");
    if (Object.values(receipt.checks).some((value) => value !== true)) fail("sales retired code receipt 未全部通过");
  }
  assertExactKeys(receipt.evidence, ["legacyPathsAbsent", "djangoRoutes", "retirementMigration", "retirementOperator"], "sales retired code evidence");
  assertExactKeys(receipt.evidence.retirementMigration, ["relativePath", "sha256"], "sales retirement migration evidence");
  if (receipt.evidence.retirementMigration.relativePath !== "drizzle/0092_sales_domain_retirement.sql"
    || receipt.evidence.retirementMigration.sha256 !== salesRetirementMigrationSha256
    || sha256Bytes(await readFile(path.join(releaseRoot, manifest.source.snapshotRoot, "drizzle", "0092_sales_domain_retirement.sql")))
      !== receipt.evidence.retirementMigration.sha256) fail("0092 retirement migration 精确 SHA 未绑定到 source snapshot");
  assertExactKeys(receipt.evidence.retirementOperator, ["relativePath", "sha256", "auditVersion"], "sales retirement operator evidence");
  if (receipt.evidence.retirementOperator.relativePath !== "tools/sales-d1-retirement.ts"
    || receipt.evidence.retirementOperator.auditVersion !== "sales-d1-retirement-v4"
    || sha256Bytes(await readFile(path.join(releaseRoot, manifest.source.snapshotRoot, "tools", "sales-d1-retirement.ts")))
      !== receipt.evidence.retirementOperator.sha256) fail("sales retirement operator v4 未绑定到 source snapshot");
  await validateContractTests(receipt.contractTests, manifest, releaseRoot);
  const guardValidation = await validateGuardReceipt(manifest, releaseRoot);
  await validateActivationFence(manifest, releaseRoot, guardValidation.receipt);
  await validateHelperReceipt(manifest, releaseRoot);
  await verifyHardLinks(manifest, releaseRoot);

  const processState = verifyPreparationPredecessor
    ? await verifyPreparationPredecessor()
    : await verifyWorkerReleaseProcessState({
      processPolicy,
      runtimeRoot,
      manifestPath,
      releaseRoot,
      expectedSupervisorPid,
    });

  const verification = {
    status: verifyPreparationPredecessor ? "preparation_verified" : "verified",
    version: verificationVersion,
    manifestSha256: manifestRead.sha256,
    releaseId,
    sourceFingerprint: manifest.source.sourceFingerprint,
    buildFingerprint: manifest.build.buildFingerprint,
    sourceD1PathSha256: manifest.runtime.sourceD1PathSha256,
    persistRootPathSha256: manifest.runtime.persistRootPathSha256,
    contractReceiptSha256: contract.sha256,
    processState,
  };
  if (writeSupervisorPrelaunchReceipt) {
    verification.supervisorPrelaunchReceiptSha256 = await publishSupervisorPrelaunchVerificationReceipt({
      runtimeRoot,
      releaseRoot,
      manifestPath,
      approvedManifestSha256: manifestRead.sha256,
      verification,
    });
  }
  return verification;
}

async function manifestContext(manifestPath, approvedManifestSha256, { allowTestRuntimeRoot = false } = {}) {
  const absolute = path.resolve(manifestPath);
  const releaseRoot = path.dirname(absolute);
  const releasesRoot = path.dirname(releaseRoot);
  const runtimeRoot = path.dirname(releasesRoot);
  const releaseId = path.basename(releaseRoot);
  if (!path.win32.isAbsolute(manifestPath) || path.basename(absolute) !== manifestFileName
    || path.basename(releasesRoot).toLowerCase() !== "releases" || !releaseIdPattern.test(releaseId)) {
    fail("manifest 不在有界 release 路径中");
  }
  if (!allowTestRuntimeRoot && canonicalWindowsPath(runtimeRoot) !== canonicalWindowsPath(workerRuntimeRoot)) {
    fail("Worker runtime 根路径无效");
  }
  await assertNoReparsePoint(absolute, { label: "Worker release manifest" });
  const read = await readCanonicalJson(absolute, "Worker release manifest");
  if (!hex64.test(approvedManifestSha256 ?? "") || read.sha256 !== approvedManifestSha256) {
    fail("Worker release manifest 原始文件哈希未获批准");
  }
  const manifest = read.value;
  assertExactKeys(manifest, [
    "version", "releaseId", "createdAt", "source", "build", "runtime", "artifacts", "processIdentity", "manifestPayloadSha256",
  ], "Worker release manifest");
  validatePayloadSha(manifest, "manifestPayloadSha256", "Worker release manifest");
  if (manifest.version !== releaseVersion || manifest.releaseId !== releaseId
    || manifest.runtime?.runtimeRootPathSha256 !== windowsPathSha256(runtimeRoot)
    || manifest.runtime?.releaseRootPathSha256 !== windowsPathSha256(releaseRoot)) {
    fail("Worker release manifest 路径身份无效");
  }
  return { manifestPath: absolute, releaseRoot, runtimeRoot, manifest, manifestSha256: read.sha256 };
}

export async function verifyLegacyGuardInstallation({
  manifestPath,
  approvedManifestSha256,
  expectedProtectedSourceRootPathSha256,
  expectedSourceD1PathSha256,
  expectedPersistRootPathSha256,
  processPolicy = "stopped",
  allowTestRuntimeRoot = false,
} = {}) {
  const release = await verifyWorkerRelease({
    manifestPath,
    approvedManifestSha256,
    expectedSourceD1PathSha256,
    expectedPersistRootPathSha256,
    expectedHost: workerHost,
    expectedPort: workerPort,
    requireSalesRetiredCodeReceipt: true,
    processPolicy,
    allowTestRuntimeRoot,
  });
  const context = await manifestContext(manifestPath, approvedManifestSha256, { allowTestRuntimeRoot });
  const guard = await validateGuardReceipt(context.manifest, context.releaseRoot, { verifyInstalledEntrypoints: true });
  if (!hex64.test(expectedProtectedSourceRootPathSha256 ?? "")
    || context.manifest.runtime.protectedSourceRootPathSha256 !== expectedProtectedSourceRootPathSha256) {
    fail("protected source root 未绑定到批准值");
  }
  // verify-guard is the formal pre-recovery gate for the installed desktop
  // guard.  A valid but non-current candidate must never be allowed to seed a
  // forward-recovery tuple.
  await assertAuthorityTargetsCurrent({
    runtimeRoot: context.runtimeRoot,
    releaseId: context.manifest.releaseId,
    manifestSha256: context.manifestSha256,
    allowTestRuntimeRoot,
  });
  return {
    status: "verified",
    version: "teruisi-legacy-worker-guard-verification-v1",
    manifestSha256: context.manifestSha256,
    releaseId: context.manifest.releaseId,
    guardReceiptSha256: guard.sha256,
    protectedSourceRootPathSha256: context.manifest.runtime.protectedSourceRootPathSha256,
    sourceD1PathSha256: context.manifest.runtime.sourceD1PathSha256,
    persistRootPathSha256: context.manifest.runtime.persistRootPathSha256,
    processState: release.processState,
  };
}

export function authorityPayload({
  cutoverId,
  workerReleaseId,
  workerReleaseManifestSha256,
  djangoDeploymentManifestSha256,
  guardReceiptSha256,
  sourceD1PathSha256,
  persistRootPathSha256,
}) {
  if (typeof cutoverId !== "string" || !/^[A-Za-z0-9._:-]{8,128}$/.test(cutoverId)) fail("cutoverId 格式无效");
  if (!releaseIdPattern.test(workerReleaseId ?? "")) fail("workerReleaseId 格式无效");
  for (const value of [workerReleaseManifestSha256, djangoDeploymentManifestSha256, guardReceiptSha256, sourceD1PathSha256, persistRootPathSha256]) {
    if (!hex64.test(value ?? "")) fail("authority sentinel SHA-256 字段无效");
  }
  return withPayloadSha256({
    version: "teruisi-sales-postgresql-authority-v1",
    domain: "sales",
    authority: "postgresql",
    cutoverId,
    workerReleaseId,
    workerReleaseManifestSha256,
    djangoDeploymentManifestSha256,
    guardReceiptSha256,
    sourceD1PathSha256,
    persistRootPathSha256,
  }, "payloadSha256");
}

async function readAuthorityFiles(runtimeRoot, expectedPayload) {
  const stateRoot = path.join(runtimeRoot, "state");
  await assertNoReparsePoint(runtimeRoot, { label: "Worker runtime 根" });
  await assertNoReparsePoint(stateRoot, { label: "Worker runtime state 根" });
  const stateInfo = await lstat(stateRoot);
  if (!stateInfo.isDirectory() || stateInfo.isSymbolicLink()) fail("Worker runtime state 必须是实体目录");
  const authorityPath = path.join(stateRoot, "sales-postgresql-authority.json");
  const sidecarPath = `${authorityPath}.sha256`;
  const hasAuthority = await pathExists(authorityPath);
  const hasSidecar = await pathExists(sidecarPath);
  if (!hasAuthority) return { status: "missing", authorityPath, sidecarPath, hasSidecar };
  await assertNoReparsePoint(authorityPath, { label: "sales authority sentinel" });
  const read = await readCanonicalJson(authorityPath, "sales authority sentinel");
  assertExactKeys(read.value, [
    "version", "domain", "authority", "cutoverId", "workerReleaseId", "workerReleaseManifestSha256",
    "djangoDeploymentManifestSha256", "guardReceiptSha256", "sourceD1PathSha256", "persistRootPathSha256", "payloadSha256",
  ], "sales authority sentinel");
  validatePayloadSha(read.value, "payloadSha256", "sales authority sentinel");
  if (canonicalJson(read.value) !== canonicalJson(expectedPayload)) fail("sales authority sentinel 已存在但与本次批准元组不一致");
  if (hasSidecar) {
    await assertNoReparsePoint(sidecarPath, { label: "sales authority sidecar" });
    const sidecar = await readFile(sidecarPath);
    if (!sidecar.equals(Buffer.from(`${read.sha256}\n`, "ascii"))) fail("sales authority sidecar 已存在但无效");
  }
  return { status: hasSidecar ? "verified" : "sidecar_missing", authorityPath, sidecarPath, sha256: read.sha256 };
}

async function writeFileAtomicCreateOnly(target, bytes) {
  const temporary = `${target}.tmp-${randomUUID()}`;
  let handle;
  try {
    handle = await open(temporary, "wx");
    await handle.writeFile(bytes);
    await handle.sync();
    await handle.close();
    handle = null;
    await link(temporary, target);
    await rm(temporary, { force: true });
  } catch (error) {
    if (handle) await handle.close().catch(() => {});
    await rm(temporary, { force: true }).catch(() => {});
    throw error;
  }
}

function authorityResult(status, payload, authoritySha256) {
  return {
    status,
    version: payload.version,
    authoritySha256,
    cutoverId: payload.cutoverId,
    workerReleaseId: payload.workerReleaseId,
    workerReleaseManifestSha256: payload.workerReleaseManifestSha256,
    djangoDeploymentManifestSha256: payload.djangoDeploymentManifestSha256,
    guardReceiptSha256: payload.guardReceiptSha256,
    sourceD1PathSha256: payload.sourceD1PathSha256,
    persistRootPathSha256: payload.persistRootPathSha256,
  };
}

async function expectedAuthorityFromOptions(options, { verifyGuard = true } = {}) {
  const context = await manifestContext(options.manifestPath, options.approvedManifestSha256, {
    allowTestRuntimeRoot: options.allowTestRuntimeRoot,
  });
  if (context.manifest.runtime?.sourceD1PathSha256 !== options.expectedSourceD1PathSha256
    || context.manifest.runtime?.persistRootPathSha256 !== options.expectedPersistRootPathSha256) {
    fail("authority 路径哈希未绑定到 Worker release manifest");
  }
  const guardPointer = context.manifest.artifacts?.guardReceipt;
  assertExactKeys(guardPointer, ["version", "relativePath", "sha256"], "guard receipt pointer");
  if (guardPointer.version !== "teruisi-legacy-worker-guard-receipt-v1"
    || guardPointer.relativePath !== "audit/legacy-worker-guard-receipt.json" || !hex64.test(guardPointer.sha256 ?? "")) {
    fail("guard receipt pointer 无效");
  }
  let guard;
  if (verifyGuard) {
    guard = await verifyLegacyGuardInstallation({
      manifestPath: options.manifestPath,
      approvedManifestSha256: options.approvedManifestSha256,
      expectedProtectedSourceRootPathSha256: options.expectedProtectedSourceRootPathSha256
        ?? context.manifest.runtime.protectedSourceRootPathSha256,
      expectedSourceD1PathSha256: options.expectedSourceD1PathSha256,
      expectedPersistRootPathSha256: options.expectedPersistRootPathSha256,
      processPolicy: options.processPolicy ?? "stopped",
      allowTestRuntimeRoot: options.allowTestRuntimeRoot,
    });
  }
  const guardReceiptSha256 = guard?.guardReceiptSha256 ?? context.manifest.artifacts.guardReceipt.sha256;
  if (guardReceiptSha256 !== options.expectedGuardReceiptSha256) fail("guard receipt SHA-256 未绑定到 operator 批准值");
  const payload = authorityPayload({
    cutoverId: options.cutoverId,
    workerReleaseId: context.manifest.releaseId,
    workerReleaseManifestSha256: context.manifestSha256,
    djangoDeploymentManifestSha256: options.djangoDeploymentManifestSha256,
    guardReceiptSha256,
    sourceD1PathSha256: options.expectedSourceD1PathSha256,
    persistRootPathSha256: options.expectedPersistRootPathSha256,
  });
  return { context, payload };
}

export async function publishSalesAuthorityPayload(runtimeRoot, payload) {
  assertExactKeys(payload, [
    "version", "domain", "authority", "cutoverId", "workerReleaseId", "workerReleaseManifestSha256",
    "djangoDeploymentManifestSha256", "guardReceiptSha256", "sourceD1PathSha256", "persistRootPathSha256", "payloadSha256",
  ], "sales authority payload");
  validatePayloadSha(payload, "payloadSha256", "sales authority payload");
  const normalizedPayload = authorityPayload({
    cutoverId: payload.cutoverId,
    workerReleaseId: payload.workerReleaseId,
    workerReleaseManifestSha256: payload.workerReleaseManifestSha256,
    djangoDeploymentManifestSha256: payload.djangoDeploymentManifestSha256,
    guardReceiptSha256: payload.guardReceiptSha256,
    sourceD1PathSha256: payload.sourceD1PathSha256,
    persistRootPathSha256: payload.persistRootPathSha256,
  });
  if (canonicalJson(normalizedPayload) !== canonicalJson(payload)) fail("sales authority payload 身份无效");
  const initial = await readAuthorityFiles(runtimeRoot, payload);
  if (initial.status === "missing" && initial.hasSidecar) fail("sales authority sidecar 孤立存在，拒绝覆盖");
  const raw = Buffer.from(`${canonicalJson(payload)}\n`, "utf8");
  let createdAuthority = false;
  if (initial.status === "missing") {
    try {
      await writeFileAtomicCreateOnly(initial.authorityPath, raw);
      createdAuthority = true;
    } catch (error) {
      if (!(error && typeof error === "object" && error.code === "EEXIST")) throw error;
    }
  }
  const afterAuthority = await readAuthorityFiles(runtimeRoot, payload);
  if (afterAuthority.status === "missing") fail("sales authority sentinel 原子发布失败");
  let createdSidecar = false;
  if (afterAuthority.status === "sidecar_missing") {
    try {
      await writeFileAtomicCreateOnly(afterAuthority.sidecarPath, Buffer.from(`${afterAuthority.sha256}\n`, "ascii"));
      createdSidecar = true;
    } catch (error) {
      if (!(error && typeof error === "object" && error.code === "EEXIST")) throw error;
    }
  }
  const after = await readAuthorityFiles(runtimeRoot, payload);
  if (after.status !== "verified") fail("sales authority sentinel 写入后回读失败");
  const status = createdAuthority ? "written" : createdSidecar ? "sidecar_repaired" : "already_present";
  return authorityResult(status, payload, after.sha256);
}

export async function verifyPublishedAuthorityForCurrent({
  runtimeRoot,
  payload,
  releaseId,
  manifestSha256,
  expectedPointerSha256,
  allowTestRuntimeRoot = false,
} = {}) {
  const initialCurrent = await assertAuthorityTargetsCurrent({
    runtimeRoot,
    releaseId,
    manifestSha256,
    expectedPointerSha256,
    allowTestRuntimeRoot,
  });
  const authority = await readAuthorityFiles(runtimeRoot, payload);
  if (authority.status !== "verified") fail("sales authority sentinel/sidecar 未完整发布");
  await assertAuthorityTargetsCurrent({
    runtimeRoot,
    releaseId,
    manifestSha256,
    expectedPointerSha256: initialCurrent.pointerSha256,
    allowTestRuntimeRoot,
  });
  return authority;
}

export async function writeSalesAuthority(options) {
  const preliminary = await manifestContext(options.manifestPath, options.approvedManifestSha256, {
    allowTestRuntimeRoot: options.allowTestRuntimeRoot,
  });
  const initialCurrent = await assertAuthorityTargetsCurrent({
    runtimeRoot: preliminary.runtimeRoot,
    releaseId: preliminary.manifest.releaseId,
    manifestSha256: preliminary.manifestSha256,
    allowTestRuntimeRoot: options.allowTestRuntimeRoot,
  });
  const { context, payload } = await expectedAuthorityFromOptions(options);
  await assertAuthorityTargetsCurrent({
    runtimeRoot: context.runtimeRoot,
    releaseId: context.manifest.releaseId,
    manifestSha256: context.manifestSha256,
    expectedPointerSha256: initialCurrent.pointerSha256,
    allowTestRuntimeRoot: options.allowTestRuntimeRoot,
  });
  const publication = await publishSalesAuthorityPayload(context.runtimeRoot, payload);
  // Publishing the create-only authority is not success until the exact
  // create-only current pointer has been re-read.  If an external actor
  // removes or tampers with current during publication, keep the sentinel for
  // same-tuple recovery but fail this invocation closed.
  await assertAuthorityTargetsCurrent({
    runtimeRoot: context.runtimeRoot,
    releaseId: context.manifest.releaseId,
    manifestSha256: context.manifestSha256,
    expectedPointerSha256: initialCurrent.pointerSha256,
    allowTestRuntimeRoot: options.allowTestRuntimeRoot,
  });
  return publication;
}

export async function verifySalesAuthority(options) {
  const { context, payload } = await expectedAuthorityFromOptions(options, { verifyGuard: false });
  const authority = await verifyPublishedAuthorityForCurrent({
    runtimeRoot: context.runtimeRoot,
    payload,
    releaseId: context.manifest.releaseId,
    manifestSha256: context.manifestSha256,
    allowTestRuntimeRoot: options.allowTestRuntimeRoot,
  });
  return authorityResult("verified", payload, authority.sha256);
}

function parseCli(argv) {
  if (argv.length === 0) fail("缺少 Worker release 命令");
  const command = argv[0];
  if (!["deploy", "verify", "verify-guard", "write-authority", "verify-authority"].includes(command)) {
    fail("只支持 deploy/verify/verify-guard/write-authority/verify-authority");
  }
  const values = new Map();
  const flags = new Set();
  for (let index = 1; index < argv.length; index += 1) {
    const token = argv[index];
    if (![
      "--json", "--require-sales-retired-code-receipt", "--allow-test-runtime-root",
      "--write-supervisor-prelaunch-receipt",
    ].includes(token)) {
      if (!token.startsWith("--") || index + 1 >= argv.length || argv[index + 1].startsWith("--")) fail(`参数无效：${token}`);
      if (values.has(token)) fail(`参数重复：${token}`);
      values.set(token, argv[++index]);
    } else {
      if (flags.has(token)) fail(`参数重复：${token}`);
      flags.add(token);
    }
  }
  return { command, values, flags };
}

function required(values, name) {
  const value = values.get(name);
  if (!value) fail(`缺少参数 ${name}`);
  return value;
}

async function main() {
  const { command, values, flags } = parseCli(process.argv.slice(2));
  const assertAllowedFlags = (allowed) => {
    for (const flag of flags) if (!allowed.has(flag)) fail(`${command} 不支持参数 ${flag}`);
  };
  let result;
  if (command === "deploy") {
    const allowed = new Set(["--source-root", "--runtime-root", "--dev-vars-source", "--persist-root", "--source-d1-path"]);
    for (const key of values.keys()) if (!allowed.has(key)) fail(`deploy 不支持参数 ${key}`);
    assertAllowedFlags(new Set(["--json", "--allow-test-runtime-root"]));
    result = await deployWorkerRelease({
      sourceRoot: required(values, "--source-root"),
      runtimeRoot: values.get("--runtime-root") ?? workerRuntimeRoot,
      devVarsSource: required(values, "--dev-vars-source"),
      persistRoot: required(values, "--persist-root"),
      sourceD1Path: required(values, "--source-d1-path"),
      allowTestRuntimeRoot: flags.has("--allow-test-runtime-root"),
    });
  } else if (command === "verify") {
    const allowed = new Set([
      "--manifest", "--approved-manifest-sha256", "--expected-source-d1-path-sha256", "--expected-persist-root-path-sha256",
      "--expected-host", "--expected-port", "--process-policy",
    ]);
    for (const key of values.keys()) if (!allowed.has(key)) fail(`verify 不支持参数 ${key}`);
    assertAllowedFlags(new Set([
      "--json", "--require-sales-retired-code-receipt", "--allow-test-runtime-root",
      "--write-supervisor-prelaunch-receipt",
    ]));
    const expectedPort = Number.parseInt(values.get("--expected-port") ?? String(workerPort), 10);
    result = await verifyWorkerRelease({
      manifestPath: required(values, "--manifest"),
      approvedManifestSha256: required(values, "--approved-manifest-sha256"),
      expectedSourceD1PathSha256: required(values, "--expected-source-d1-path-sha256"),
      expectedPersistRootPathSha256: required(values, "--expected-persist-root-path-sha256"),
      expectedHost: values.get("--expected-host") ?? workerHost,
      expectedPort,
      requireSalesRetiredCodeReceipt: flags.has("--require-sales-retired-code-receipt"),
      processPolicy: values.get("--process-policy") ?? "stopped-or-exact-release",
      allowTestRuntimeRoot: flags.has("--allow-test-runtime-root"),
      writeSupervisorPrelaunchReceipt: flags.has("--write-supervisor-prelaunch-receipt"),
    });
  } else if (command === "verify-guard") {
    const allowed = new Set([
      "--manifest", "--approved-manifest-sha256", "--expected-protected-source-root-path-sha256",
      "--expected-source-d1-path-sha256", "--expected-persist-root-path-sha256", "--process-policy",
    ]);
    for (const key of values.keys()) if (!allowed.has(key)) fail(`verify-guard 不支持参数 ${key}`);
    assertAllowedFlags(new Set(["--json", "--allow-test-runtime-root"]));
    result = await verifyLegacyGuardInstallation({
      manifestPath: required(values, "--manifest"),
      approvedManifestSha256: required(values, "--approved-manifest-sha256"),
      expectedProtectedSourceRootPathSha256: required(values, "--expected-protected-source-root-path-sha256"),
      expectedSourceD1PathSha256: required(values, "--expected-source-d1-path-sha256"),
      expectedPersistRootPathSha256: required(values, "--expected-persist-root-path-sha256"),
      processPolicy: values.get("--process-policy") ?? "stopped",
      allowTestRuntimeRoot: flags.has("--allow-test-runtime-root"),
    });
  } else if (command === "write-authority") {
    const allowed = new Set([
      "--manifest", "--approved-manifest-sha256", "--django-deployment-manifest-sha256", "--cutover-id",
      "--expected-guard-receipt-sha256", "--expected-source-d1-path-sha256", "--expected-persist-root-path-sha256", "--process-policy",
    ]);
    for (const key of values.keys()) if (!allowed.has(key)) fail(`${command} 不支持参数 ${key}`);
    assertAllowedFlags(new Set(["--json", "--allow-test-runtime-root"]));
    const options = {
      manifestPath: required(values, "--manifest"),
      approvedManifestSha256: required(values, "--approved-manifest-sha256"),
      djangoDeploymentManifestSha256: required(values, "--django-deployment-manifest-sha256"),
      cutoverId: required(values, "--cutover-id"),
      expectedGuardReceiptSha256: required(values, "--expected-guard-receipt-sha256"),
      expectedSourceD1PathSha256: required(values, "--expected-source-d1-path-sha256"),
      expectedPersistRootPathSha256: required(values, "--expected-persist-root-path-sha256"),
      processPolicy: values.get("--process-policy") ?? "stopped",
      allowTestRuntimeRoot: flags.has("--allow-test-runtime-root"),
    };
    result = await writeSalesAuthority(options);
  } else {
    const allowed = new Set([
      "--manifest", "--approved-manifest-sha256", "--django-deployment-manifest-sha256", "--cutover-id",
      "--expected-guard-receipt-sha256", "--expected-source-d1-path-sha256", "--expected-persist-root-path-sha256",
    ]);
    for (const key of values.keys()) if (!allowed.has(key)) fail(`${command} 不支持参数 ${key}`);
    assertAllowedFlags(new Set(["--json", "--allow-test-runtime-root"]));
    result = await verifySalesAuthority({
      manifestPath: required(values, "--manifest"),
      approvedManifestSha256: required(values, "--approved-manifest-sha256"),
      djangoDeploymentManifestSha256: required(values, "--django-deployment-manifest-sha256"),
      cutoverId: required(values, "--cutover-id"),
      expectedGuardReceiptSha256: required(values, "--expected-guard-receipt-sha256"),
      expectedSourceD1PathSha256: required(values, "--expected-source-d1-path-sha256"),
      expectedPersistRootPathSha256: required(values, "--expected-persist-root-path-sha256"),
      allowTestRuntimeRoot: flags.has("--allow-test-runtime-root"),
    });
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === modulePath) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
