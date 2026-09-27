import { spawn } from "node:child_process";
import { lstat, mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  createLocalScheduledTriggerSupervisor,
  ensureRuntimeDevVarsLink,
  localLivenessFailureThreshold,
  monitorLocalWorkerLiveness,
  probeLocalWorkerLiveness,
  terminateOwnedProcessTree,
} from "./start-local-worker.mjs";
import { assertReleaseWorkerLaunchAllowed } from "./worker-authority-guard.mjs";
import {
  canonicalJson,
  canonicalWindowsPath,
  consumeSupervisorPrelaunchVerificationReceipt,
  hashTree,
  probeAnyLocalPort,
  assertNoReparsePoint,
  assertSupervisorPrelaunchProcessState,
  sha256Bytes,
  windowsPathSha256,
  workerHelperHost,
  workerHelperPort,
  workerHost,
  workerPort,
} from "./worker-local-release.mjs";

const modulePath = fileURLToPath(import.meta.url);
const releaseRoot = path.resolve(path.dirname(modulePath), "..");
const hex64 = /^[0-9a-f]{64}$/;
const restartWindowMs = 10 * 60_000;
const maxRestartsPerWindow = 5;
export const miniflareCacheRelativePath = "cache/miniflare";
export const workerdOldSpaceMiB = 3072;
export const heapPatchedMiniflareSha256 = "2b2a89fb96a270e678b4aa87e65aa1282049b18d28a1e30ff7fe7f2736b648c7";

export async function assertWorkerdHeapAdapter(root) {
  const adapter = await readFile(path.join(root, "node_modules/miniflare/dist/src/index.js"));
  if (sha256Bytes(adapter) !== heapPatchedMiniflareSha256) {
    throw new Error("workerd 3072 MiB heap adapter digest mismatch");
  }
}

export function resolveImmutableMiniflareCacheDirectory(runtimeRoot) {
  if (typeof runtimeRoot !== "string" || runtimeRoot.trim() === "") {
    throw new Error("Worker runtime root 无效，无法派生 Miniflare cache");
  }
  return path.join(path.resolve(runtimeRoot), ...miniflareCacheRelativePath.split("/"));
}

export function immutableMiniflareCacheBinding({ runtimeRoot, releaseRoot, persistRoot } = {}) {
  if (typeof releaseRoot !== "string" || releaseRoot.trim() === "") {
    throw new Error("Worker release root 无效，无法约束 Miniflare cache");
  }
  if (typeof persistRoot !== "string" || !path.isAbsolute(persistRoot)) {
    throw new Error("manifest persist root 无效，无法约束 Miniflare cache");
  }
  const absoluteRuntimeRoot = path.resolve(runtimeRoot);
  const absoluteReleaseRoot = path.resolve(releaseRoot);
  const absolutePersistRoot = path.resolve(persistRoot);
  const derivedRuntimeRoot = path.resolve(absoluteReleaseRoot, "..", "..");
  if (canonicalWindowsPath(absoluteRuntimeRoot) !== canonicalWindowsPath(derivedRuntimeRoot)) {
    throw new Error("Worker release/runtime 边界无效，拒绝派生 Miniflare cache");
  }
  const cacheRoot = resolveImmutableMiniflareCacheDirectory(absoluteRuntimeRoot);
  const relativeToRuntime = path.relative(absoluteRuntimeRoot, cacheRoot);
  const relativeToRelease = path.relative(absoluteReleaseRoot, cacheRoot);
  const cacheIdentity = canonicalWindowsPath(cacheRoot);
  const persistIdentity = canonicalWindowsPath(absolutePersistRoot);
  const normalizedRuntimeRelative = relativeToRuntime.split(path.sep).join("/").toLowerCase();
  if (normalizedRuntimeRelative !== miniflareCacheRelativePath
    || relativeToRelease === ""
    || (!relativeToRelease.startsWith(`..${path.sep}`) && relativeToRelease !== "..")) {
    throw new Error("Miniflare cache 未严格绑定到 release 外的 runtime cache 边界");
  }
  if (cacheIdentity === persistIdentity
    || cacheIdentity.startsWith(`${persistIdentity}\\`)
    || persistIdentity.startsWith(`${cacheIdentity}\\`)) {
    throw new Error("Miniflare cache 与 Wrangler persist root 不得相互包含");
  }
  return {
    relativePath: miniflareCacheRelativePath,
    cacheRoot,
    cacheRootPathSha256: windowsPathSha256(cacheRoot),
    cacheFile: path.join(cacheRoot, "cf.json"),
    cacheFilePathSha256: windowsPathSha256(path.join(cacheRoot, "cf.json")),
  };
}

export function immutableWorkerEnvironment({
  runtimeRoot,
  releaseRoot,
  persistRoot,
  inheritedEnvironment = process.env,
} = {}) {
  if (typeof persistRoot !== "string" || !path.isAbsolute(persistRoot)) {
    throw new Error("manifest persist root 无效，无法构造 immutable Worker 环境");
  }
  const environment = {};
  const controlledNames = new Set([
    "TERUISI_LOCAL_WRANGLER_STATE_DIR",
    "MINIFLARE_CACHE_DIR",
    "CLOUDFLARE_CF_FETCH_PATH",
    "CLOUDFLARE_CF_FETCH_ENABLED",
    "TERUISI_WORKERD_HEAP_MB",
  ]);
  for (const [name, value] of Object.entries(inheritedEnvironment ?? {})) {
    if (!controlledNames.has(name.toUpperCase()) && value !== undefined) environment[name] = value;
  }
  const cacheBinding = immutableMiniflareCacheBinding({ runtimeRoot, releaseRoot, persistRoot });
  environment.TERUISI_LOCAL_WRANGLER_STATE_DIR = path.resolve(persistRoot);
  environment.MINIFLARE_CACHE_DIR = cacheBinding.cacheRoot;
  // Miniflare gives this older override precedence over MINIFLARE_CACHE_DIR,
  // so bind both inputs to the same verified external cache location.
  environment.CLOUDFLARE_CF_FETCH_PATH = cacheBinding.cacheFile;
  environment.CLOUDFLARE_CF_FETCH_ENABLED = "true";
  // Consumed by the digest-pinned Miniflare config backport, not by Node.
  environment.TERUISI_WORKERD_HEAP_MB = String(workerdOldSpaceMiB);
  return environment;
}

async function ensureImmutableRuntimeCacheDirectory(target, label) {
  try {
    await mkdir(target);
  } catch (error) {
    if (!(error && typeof error === "object" && error.code === "EEXIST")) throw error;
  }
  const info = await lstat(target);
  if (!info.isDirectory() || info.isSymbolicLink()) {
    throw new Error(`${label}必须是实体目录`);
  }
  await assertNoReparsePoint(target, { label });
}

async function assertSafeMiniflareCacheFile(cacheFile) {
  let info;
  try {
    info = await lstat(cacheFile);
  } catch (error) {
    if (error && typeof error === "object" && error.code === "ENOENT") return;
    throw error;
  }
  if (!info.isFile() || info.isSymbolicLink()) {
    throw new Error("Miniflare cf.json 必须是普通非链接文件");
  }
  if (!Number.isSafeInteger(info.nlink) || info.nlink !== 1) {
    throw new Error("Miniflare cf.json 必须保持单链接文件身份");
  }
  await assertNoReparsePoint(cacheFile, { label: "Miniflare cf.json" });
}

export async function prepareImmutableMiniflareCacheDirectory({ runtimeRoot, releaseRoot, persistRoot } = {}) {
  const binding = immutableMiniflareCacheBinding({ runtimeRoot, releaseRoot, persistRoot });
  const absoluteRuntimeRoot = path.resolve(runtimeRoot);
  const cacheParent = path.join(absoluteRuntimeRoot, "cache");
  const cacheRoot = binding.cacheRoot;
  await assertNoReparsePoint(absoluteRuntimeRoot, { label: "Worker runtime root" });
  await ensureImmutableRuntimeCacheDirectory(cacheParent, "Worker runtime cache root");
  await ensureImmutableRuntimeCacheDirectory(cacheRoot, "Miniflare cache");
  await assertSafeMiniflareCacheFile(binding.cacheFile);
  return binding;
}

function waitForDelay(delayMs, signal) {
  return new Promise((resolveWait) => {
    if (signal?.aborted) return resolveWait(false);
    const timer = setTimeout(() => finish(true), delayMs);
    const onAbort = () => finish(false);
    const finish = (value) => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolveWait(value);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function observeChild(child, signal) {
  return new Promise((resolveExit, rejectExit) => {
    const onAbort = () => {
      if (child.exitCode == null && child.signalCode == null) child.kill("SIGTERM");
    };
    if (signal?.aborted) onAbort();
    else signal?.addEventListener("abort", onAbort, { once: true });
    child.once("error", rejectExit);
    child.once("exit", (code, childSignal) => {
      signal?.removeEventListener("abort", onAbort);
      resolveExit({ code, signal: childSignal });
    });
  });
}

async function waitForTerminatedChild(childExit) {
  let timeout;
  try {
    return await Promise.race([
      childExit,
      new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error("不可变 Worker 子进程未在15秒内退出，停止自动恢复")), 15_000);
      }),
    ]);
  } finally {
    clearTimeout(timeout);
  }
}

async function waitForPortRelease(port, label, signal) {
  const started = Date.now();
  // An exact loopback connect probe is insufficient here: a wildcard,
  // IPv6, or non-loopback listener can still win the next bind.  Reuse the
  // same exclusive any-interface fence as Deploy/formal verification.
  while (await probeAnyLocalPort(port)) {
    if (Date.now() - started >= 15_000) throw new Error(`${label}退出后未在15秒内释放${port}端口`);
    if (!(await waitForDelay(250, signal))) return false;
  }
  return true;
}

async function assertHelperArtifact(releaseRoot, manifest) {
  const relativePath = manifest.processIdentity.helperEntrypoint;
  const key = manifest.artifacts.keyFiles.find((item) => item.relativePath === relativePath);
  if (!key) throw new Error("manifest 未绑定 immutable helper key file");
  const raw = await readFile(path.join(releaseRoot, ...relativePath.split("/")));
  if (sha256Bytes(raw) !== key.sha256) throw new Error("immutable helper bundle 在 restart 前发生变化");
  const helperTree = await hashTree(path.join(releaseRoot, manifest.build.helperRoot));
  if (canonicalJson(helperTree) !== canonicalJson(manifest.build.helperTree)) {
    throw new Error("immutable helper resource tree 在 restart 前发生变化");
  }
}

export async function superviseImmutableHelper({ releaseRoot, manifest, manifestPath, manifestSha256, runtimeRoot, signal }) {
  let consecutiveRestarts = 0;
  while (!signal.aborted) {
    await assertSupervisorPrelaunchProcessState({
      manifestPath,
      releaseRoot,
      expectedSupervisorPid: process.pid,
    });
    await assertReleaseWorkerLaunchAllowed({ manifestPath, manifestSha256, runtimeRoot });
    await assertHelperArtifact(releaseRoot, manifest);
    if (await probeAnyLocalPort(workerHelperPort)) throw new Error("5791端口在 immutable helper restart 前被占用");
    const startedAt = Date.now();
    const child = spawn(process.execPath, [
      path.join(releaseRoot, ...manifest.processIdentity.helperEntrypoint.split("/")),
      ...manifest.processIdentity.fixedHelperArguments,
    ], {
      cwd: manifest.runtime.helperMutableRoot,
      env: {
        ...process.env,
        TERUISI_HELPER_MUTABLE_ROOT: manifest.runtime.helperMutableRoot,
      },
      stdio: "inherit",
      windowsHide: true,
    });
    const outcome = await observeChild(child, signal);
    if (signal.aborted) return;
    await waitForPortRelease(workerHelperPort, "immutable helper", signal);
    const lifetime = Math.max(0, Date.now() - startedAt);
    consecutiveRestarts = lifetime >= 30_000 ? 0 : consecutiveRestarts + 1;
    const reason = outcome.signal ? `signal=${outcome.signal}` : `exit=${outcome.code ?? "unknown"}`;
    const delay = Math.min(5_000, 500 * (2 ** Math.min(Math.max(0, consecutiveRestarts - 1), 4)));
    process.stderr.write(`immutable helper 已退出（${reason}），${delay}ms 后受控重启\n`);
    if (!(await waitForDelay(delay, signal))) return;
  }
}

export async function superviseImmutableWorker({
  releaseRoot, manifest, manifestPath, manifestSha256, runtimeRoot, signal,
  livenessMonitor = monitorLocalWorkerLiveness,
  terminateWorkerTree = terminateOwnedProcessTree,
}) {
  let restartTimestamps = [];
  let restartCount = 0;
  while (!signal.aborted) {
    await assertSupervisorPrelaunchProcessState({
      manifestPath,
      releaseRoot,
      expectedSupervisorPid: process.pid,
    });
    await assertReleaseWorkerLaunchAllowed({ manifestPath, manifestSha256, runtimeRoot });
    const miniflareCache = await prepareImmutableMiniflareCacheDirectory({
      runtimeRoot,
      releaseRoot,
      persistRoot: manifest.runtime.persistRoot,
    });
    await ensureRuntimeDevVarsLink(releaseRoot);
    if (await probeAnyLocalPort(workerPort)) throw new Error("3000端口在不可变 Worker restart 前被任意接口占用");
    const workerEnvironment = immutableWorkerEnvironment({
      runtimeRoot,
      releaseRoot,
      persistRoot: manifest.runtime.persistRoot,
    });
    if (workerEnvironment.MINIFLARE_CACHE_DIR !== miniflareCache.cacheRoot
      || workerEnvironment.CLOUDFLARE_CF_FETCH_PATH !== miniflareCache.cacheFile
      || windowsPathSha256(workerEnvironment.MINIFLARE_CACHE_DIR) !== miniflareCache.cacheRootPathSha256
      || windowsPathSha256(workerEnvironment.CLOUDFLARE_CF_FETCH_PATH) !== miniflareCache.cacheFilePathSha256) {
      throw new Error("Miniflare cache 环境未绑定受控 runtime cache 目录");
    }
    await assertWorkerdHeapAdapter(releaseRoot);
    process.stdout.write(`workerd heap policy: max-old-space-size=${workerdOldSpaceMiB} MiB; adapter=${heapPatchedMiniflareSha256}\n`);
    const child = spawn(process.execPath, [
      path.join(releaseRoot, ...manifest.processIdentity.wranglerEntrypoint.split("/")),
      ...manifest.processIdentity.fixedWranglerArguments,
    ], { cwd: releaseRoot, env: workerEnvironment, stdio: "inherit", windowsHide: true });
    // Shutdown must terminate the owned Wrangler tree, not just the parent
    // process: an inner workerd can outlive Wrangler after a fatal error.
    const childExit = observeChild(child);
    const monitorController = new AbortController();
    const abortMonitor = () => monitorController.abort();
    if (signal.aborted) abortMonitor();
    else signal.addEventListener("abort", abortMonitor, { once: true });
    const liveness = Promise.resolve().then(() => livenessMonitor({
      signal: monitorController.signal,
      failureThreshold: localLivenessFailureThreshold,
      probe: ({ signal: probeSignal }) => probeLocalWorkerLiveness({
        url: `http://${workerHost}:${workerPort}/_teruisi/local/health/live`,
        signal: probeSignal,
      }),
    })).catch((error) => ({ status: "unhealthy", error }));
    let first;
    try {
      first = await Promise.race([
        childExit.then((result) => ({ source: "process", result })),
        liveness.then((result) => ({ source: "liveness", result })),
      ]);
    } finally {
      monitorController.abort();
      signal.removeEventListener("abort", abortMonitor);
    }
    if (signal.aborted) {
      await terminateWorkerTree(child);
      await waitForTerminatedChild(childExit);
      await waitForPortRelease(workerPort, "不可变 Worker", signal);
      return;
    }
    if (first.source === "liveness") {
      await terminateWorkerTree(child);
      if (first.result.status === "aborted") {
        await waitForTerminatedChild(childExit);
        await waitForPortRelease(workerPort, "不可变 Worker", signal);
        throw new Error("不可变 Worker 存活观察器意外停止");
      }
    }
    const outcome = first.source === "liveness" ? await waitForTerminatedChild(childExit) : await childExit;
    await waitForPortRelease(workerPort, "不可变 Worker", signal);
    const now = Date.now();
    restartTimestamps = restartTimestamps.filter((value) => now - value < restartWindowMs);
    restartTimestamps.push(now);
    if (restartTimestamps.length > maxRestartsPerWindow) {
      throw new Error("不可变 Worker 在10分钟内重启超过5次，已失败关闭");
    }
    restartCount += 1;
    const reason = first.source === "liveness"
      ? `存活检查连续 ${first.result.consecutiveFailures ?? localLivenessFailureThreshold} 次失败`
      : outcome.signal ? `signal=${outcome.signal}` : `exit=${outcome.code ?? "unknown"}`;
    const delay = Math.min(30_000, 1_000 * (2 ** Math.min(restartCount - 1, 5)));
    process.stderr.write(`不可变 Worker 已退出（${reason}），${delay}ms 后受控重启\n`);
    if (!(await waitForDelay(delay, signal))) return;
  }
}

function parseArguments(argv) {
  if (argv.length !== 4 || argv[0] !== "--manifest" || argv[2] !== "--approved-manifest-sha256") {
    throw new Error("不可变 Worker supervisor 只接受固定 manifest 身份参数");
  }
  const manifestPath = path.resolve(argv[1]);
  const approvedManifestSha256 = argv[3];
  if (path.dirname(manifestPath) !== releaseRoot || path.basename(manifestPath) !== "deployment-manifest.json" || !hex64.test(approvedManifestSha256)) {
    throw new Error("不可变 Worker supervisor manifest 身份无效");
  }
  return { manifestPath, approvedManifestSha256 };
}

async function readManifestIdentity(manifestPath, approvedManifestSha256) {
  const raw = await readFile(manifestPath);
  if (sha256Bytes(raw) !== approvedManifestSha256) throw new Error("Worker release manifest 原始文件哈希不一致");
  const manifest = JSON.parse(raw.toString("utf8"));
  if (!raw.equals(Buffer.from(`${canonicalJson(manifest)}\n`, "utf8"))) throw new Error("Worker release manifest 不是 canonical JSON");
  return manifest;
}

export async function startImmutableWorker(argv = process.argv.slice(2)) {
  const { manifestPath, approvedManifestSha256 } = parseArguments(argv);
  const manifest = await readManifestIdentity(manifestPath, approvedManifestSha256);
  const runtimeRoot = path.resolve(releaseRoot, "..", "..");
  if (manifest.runtime.host !== workerHost || manifest.runtime.port !== workerPort || manifest.runtime.cliOverridesAllowed !== false) {
    throw new Error("Worker release 回环或 CLI 覆盖契约无效");
  }
  if (manifest.runtime.helperMode !== "supervisor_managed_immutable_bundle"
    || manifest.runtime.helperHost !== workerHelperHost || manifest.runtime.helperPort !== workerHelperPort
    || manifest.runtime.helperMutableRoot !== manifest.runtime.protectedSourceRoot
    || manifest.runtime.helperMutableRootPathSha256 !== manifest.runtime.protectedSourceRootPathSha256
    || canonicalJson(manifest.processIdentity.fixedHelperArguments) !== canonicalJson(["serve", "--port", String(workerHelperPort)])) {
    throw new Error("Worker release immutable helper 契约无效");
  }
  await assertSupervisorPrelaunchProcessState({
    manifestPath,
    releaseRoot,
    expectedSupervisorPid: process.pid,
  });
  await consumeSupervisorPrelaunchVerificationReceipt({
    manifestPath,
    approvedManifestSha256,
    releaseRoot,
  });
  await assertReleaseWorkerLaunchAllowed({ manifestPath, manifestSha256: approvedManifestSha256, runtimeRoot });

  // The deployed supervisor never accepts caller-provided Wrangler arguments
  // or Miniflare cache overrides. Each Worker child receives manifest-bound
  // persistence plus a cache path derived from the verified runtime root.
  await ensureRuntimeDevVarsLink(releaseRoot);

  const scheduled = createLocalScheduledTriggerSupervisor();
  const shutdown = new AbortController();
  const requestShutdown = () => shutdown.abort();
  process.once("SIGINT", requestShutdown);
  process.once("SIGTERM", requestShutdown);
  scheduled.start();
  try {
    await Promise.all([superviseImmutableWorker({
      releaseRoot,
      manifest,
      manifestPath,
      manifestSha256: approvedManifestSha256,
      runtimeRoot,
      signal: shutdown.signal,
    }), superviseImmutableHelper({
      releaseRoot,
      manifest,
      manifestPath,
      manifestSha256: approvedManifestSha256,
      runtimeRoot,
      signal: shutdown.signal,
    })]);
  } finally {
    shutdown.abort();
    process.removeListener("SIGINT", requestShutdown);
    process.removeListener("SIGTERM", requestShutdown);
    scheduled.stop();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === modulePath) {
  startImmutableWorker().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
