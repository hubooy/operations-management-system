import { spawn } from "node:child_process";
import { access, link, mkdir, stat } from "node:fs/promises";
import { createConnection } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { assertLegacyWorkerLaunchAllowed } from "./worker-authority-guard.mjs";

const projectRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const localWorkerHost = "127.0.0.1";
const localWorkerPort = 3000;
const tmallWorkflowHelperPort = 5791;
const helperStableLifetimeMs = 30_000;
const localScheduledTriggerUrl = `http://127.0.0.1:${localWorkerPort}/_teruisi/local/market-annotation-scheduled`;
const localScheduledInitialDelayMs = 5_000;
const localScheduledIntervalMs = 60_000;
const localScheduledRetryDelayMs = 5_000;
const localLivenessUrl = `http://127.0.0.1:${localWorkerPort}/_teruisi/local/health/live`;
const localLivenessInitialDelayMs = 10_000;
const localLivenessIntervalMs = 10_000;
// A netshop import can legitimately occupy the single local workerd isolate
// until its bounded 120-second request timeout.  Fourteen consecutive misses
// keep the earliest liveness takeover beyond that window, so the supervisor
// cannot terminate a valid import request while still recovering a dead Worker in a
// bounded amount of time.
export const localLivenessFailureThreshold = 14;
const localLivenessTimeoutMs = 5_000;
const localReadinessUrl = `http://127.0.0.1:${localWorkerPort}/_teruisi/local/health/ready`;
const localReadinessInitialDelayMs = 10_000;
const localReadinessIntervalMs = 10_000;
const localReadinessTimeoutMs = 5_000;
const localWorkerStableLifetimeMs = 10 * 60_000;
const localWorkerRestartWindowMs = 10 * 60_000;
const localWorkerMaxRestartsPerWindow = 5;
const localWorkerPortReleaseTimeoutMs = 15_000;

export function getLocalWorkerBuildCommand(root = projectRoot) {
  return {
    command: process.execPath,
    args: [resolve(root, "node_modules", "vinext", "dist", "cli.js"), "build"],
    env: { ...process.env, VITE_TERUISI_LOCAL_BUILD: "true" },
  };
}

export function getLocalWorkerRuntimeCommand(
  root = projectRoot,
  wranglerArgs = [],
  persistDirectory = process.env.TERUISI_LOCAL_WRANGLER_STATE_DIR?.trim() || ".wrangler/state",
) {
  const boundedWranglerArgs = rejectLocalWorkerBindingOverrides(wranglerArgs);
  return {
    command: process.execPath,
    args: [
      resolve(root, "node_modules", "wrangler", "bin", "wrangler.js"),
      "dev",
      "--config",
      "dist/server/wrangler.json",
      "--port",
      String(localWorkerPort),
      "--ip",
      localWorkerHost,
      "--persist-to",
      persistDirectory,
      ...boundedWranglerArgs,
    ],
  };
}

function rejectLocalWorkerBindingOverrides(args = []) {
  const forbiddenBindingArgument = args.find((argument) => (
    /^--(?:ip|host|hostname)(?:=|$)/i.test(String(argument))
  ));
  if (forbiddenBindingArgument) {
    throw new Error(
      `本地 Worker 监听地址固定为 ${localWorkerHost}，不允许通过 ${forbiddenBindingArgument} 覆盖`,
    );
  }
  return [...args];
}

export function getTmallWorkflowHelperCommand(root = projectRoot, port = tmallWorkflowHelperPort) {
  return {
    command: process.execPath,
    args: [
      "--import",
      "tsx",
      resolve(root, "tools", "tmall-sycm-cookie-pipeline.ts"),
      "serve",
      "--port",
      String(port),
    ],
    env: { ...process.env },
  };
}

export function getTmallWorkflowHelperRestartDelay(restartCount) {
  const normalizedCount = Number.isFinite(restartCount) ? Math.max(0, Math.floor(restartCount)) : 0;
  return Math.min(5_000, 500 * (2 ** Math.min(normalizedCount, 4)));
}

export function getLocalWorkerRestartDelay(restartCount) {
  const normalizedCount = Number.isFinite(restartCount) ? Math.max(0, Math.floor(restartCount)) : 0;
  return Math.min(30_000, 1_000 * (2 ** Math.min(normalizedCount, 5)));
}

export function parseLocalWorkerArguments(args = []) {
  const wranglerArgs = rejectLocalWorkerBindingOverrides(
    args.filter((argument) => argument !== "--build"),
  );
  return {
    shouldBuild: args.includes("--build"),
    wranglerArgs,
  };
}

export function isLocalWorkerPortInUse(port = 3000, host = "127.0.0.1") {
  return new Promise((resolvePort) => {
    const socket = createConnection({ port, host });
    let settled = false;
    const finish = (inUse) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolvePort(inUse);
    };
    socket.setTimeout(750, () => finish(false));
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
  });
}

export async function assertLocalWorkerPortAvailable(checkPort = isLocalWorkerPortInUse) {
  if (await checkPort(localWorkerPort)) {
    throw new Error("端口 3000 已有服务运行。为避免覆盖正在使用的 dist 构建，本次构建/启动已取消。");
  }
}

export async function assertTmallWorkflowHelperPortAvailable(checkPort = isLocalWorkerPortInUse) {
  if (await checkPort(tmallWorkflowHelperPort)) {
    throw new Error("端口 5791 已有服务运行，无法确认其是否属于当前天猫工作流；本次启动已安全取消");
  }
}

function waitForDelay(delayMs, signal) {
  return new Promise((resolveWait) => {
    if (signal?.aborted) {
      resolveWait(false);
      return;
    }

    let settled = false;
    const finish = (completed) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolveWait(completed);
    };
    const onAbort = () => finish(false);
    const timer = setTimeout(() => finish(true), Math.max(0, delayMs));
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function probeLocalWorkerHealthEndpoint({ fetchImpl, url, timeoutMs, signal, label }) {
  const controller = new AbortController();
  let timedOut = false;
  const onAbort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener("abort", onAbort, { once: true });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, Math.max(1, timeoutMs));

  try {
    const response = await fetchImpl(url, {
      method: "GET",
      headers: { "x-teruisi-local-health": "1" },
      cache: "no-store",
      signal: controller.signal,
    });
    await response.arrayBuffer();
    if (!response.ok) throw new Error(`${label}失败：HTTP ${response.status}`);
    return response.status;
  } catch (error) {
    if (timedOut && !signal?.aborted) throw new Error(`${label}超时`);
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", onAbort);
  }
}

export async function probeLocalWorkerLiveness({
  fetchImpl = fetch,
  url = localLivenessUrl,
  timeoutMs = localLivenessTimeoutMs,
  signal,
} = {}) {
  return probeLocalWorkerHealthEndpoint({
    fetchImpl,
    url,
    timeoutMs,
    signal,
    label: "本地 Worker 存活检查",
  });
}

export async function probeLocalWorkerReadiness({
  fetchImpl = fetch,
  url = localReadinessUrl,
  timeoutMs = localReadinessTimeoutMs,
  signal,
} = {}) {
  return probeLocalWorkerHealthEndpoint({
    fetchImpl,
    url,
    timeoutMs,
    signal,
    label: "本地 Worker Django 就绪检查",
  });
}

export async function monitorLocalWorkerLiveness({
  probe = probeLocalWorkerLiveness,
  wait = waitForDelay,
  signal,
  initialDelayMs = localLivenessInitialDelayMs,
  intervalMs = localLivenessIntervalMs,
  failureThreshold = localLivenessFailureThreshold,
  onFailure,
  onRecovered,
} = {}) {
  const normalizedThreshold = Math.max(1, Math.floor(failureThreshold));
  if ((await wait(initialDelayMs, signal)) === false || signal?.aborted) {
    return { status: "aborted", consecutiveFailures: 0 };
  }

  let consecutiveFailures = 0;
  while (!signal?.aborted) {
    try {
      await probe({ signal });
      if (consecutiveFailures > 0) onRecovered?.({ previousFailures: consecutiveFailures });
      consecutiveFailures = 0;
    } catch (error) {
      if (signal?.aborted) return { status: "aborted", consecutiveFailures };
      consecutiveFailures += 1;
      onFailure?.({ consecutiveFailures, error });
      if (consecutiveFailures >= normalizedThreshold) {
        return { status: "unhealthy", consecutiveFailures, error };
      }
    }

    if ((await wait(intervalMs, signal)) === false) break;
  }
  return { status: "aborted", consecutiveFailures };
}

export async function monitorLocalWorkerReadiness({
  probe = probeLocalWorkerReadiness,
  wait = waitForDelay,
  signal,
  initialDelayMs = localReadinessInitialDelayMs,
  intervalMs = localReadinessIntervalMs,
  onFailure,
  onDegraded,
  onRecovered,
} = {}) {
  if ((await wait(initialDelayMs, signal)) === false || signal?.aborted) {
    return { status: "aborted", state: "unknown", consecutiveFailures: 0 };
  }

  let consecutiveFailures = 0;
  let state = "unknown";
  while (!signal?.aborted) {
    try {
      await probe({ signal });
      if (state === "degraded") onRecovered?.({ previousFailures: consecutiveFailures });
      consecutiveFailures = 0;
      state = "ready";
    } catch (error) {
      if (signal?.aborted) return { status: "aborted", state, consecutiveFailures };
      consecutiveFailures += 1;
      onFailure?.({ consecutiveFailures, error });
      if (state !== "degraded") onDegraded?.({ consecutiveFailures, error });
      state = "degraded";
    }

    if ((await wait(intervalMs, signal)) === false) break;
  }
  return { status: "aborted", state, consecutiveFailures };
}

export function createTmallWorkflowHelperSupervisor({
  root = projectRoot,
  spawnProcess = spawn,
  scheduleRestart = setTimeout,
  cancelRestart = clearTimeout,
  now = Date.now,
} = {}) {
  let child = null;
  let restartTimer = null;
  let stopping = false;
  let consecutiveRestarts = 0;

  const launch = () => {
    if (stopping || child) return;
    const { command, args, env } = getTmallWorkflowHelperCommand(root);
    const startedAt = now();
    const launchedChild = spawnProcess(command, args, {
      cwd: root,
      env,
      stdio: "inherit",
      windowsHide: true,
    });
    child = launchedChild;
    let settled = false;

    const scheduleNextLaunch = (reason) => {
      if (settled) return;
      settled = true;
      if (child === launchedChild) child = null;
      if (stopping) return;

      const lifetime = Math.max(0, now() - startedAt);
      consecutiveRestarts = lifetime >= helperStableLifetimeMs ? 0 : consecutiveRestarts + 1;
      const delay = getTmallWorkflowHelperRestartDelay(Math.max(0, consecutiveRestarts - 1));
      console.warn(`天猫工作流辅助服务已退出（${reason}），将在 ${delay}ms 后重新待命`);
      restartTimer = scheduleRestart(() => {
        restartTimer = null;
        launch();
      }, delay);
    };

    launchedChild.once("error", (error) => scheduleNextLaunch(error instanceof Error ? error.message : String(error)));
    launchedChild.once("exit", (code, signal) => {
      const reason = signal ? `signal=${signal}` : `code=${code ?? "unknown"}`;
      scheduleNextLaunch(reason);
    });
    console.log(`天猫工作流辅助服务已启动：http://127.0.0.1:${tmallWorkflowHelperPort}`);
  };

  return {
    start() {
      if (!stopping && !child && !restartTimer) launch();
    },
    stop() {
      stopping = true;
      if (restartTimer) {
        cancelRestart(restartTimer);
        restartTimer = null;
      }
      const runningChild = child;
      child = null;
      if (runningChild && runningChild.exitCode == null && runningChild.signalCode == null) {
        runningChild.kill();
      }
    },
  };
}

export async function triggerLocalScheduledEvent({
  fetchImpl = fetch,
  url = localScheduledTriggerUrl,
  signal,
} = {}) {
  const response = await fetchImpl(url, {
    method: "POST",
    headers: { "x-teruisi-local-scheduled": "1" },
    signal,
  });
  await response.arrayBuffer();
  if (!response.ok) throw new Error(`本地定时事件触发失败：HTTP ${response.status}`);
  return response.status;
}

export function createLocalScheduledTriggerSupervisor({
  trigger = triggerLocalScheduledEvent,
  scheduleTrigger = setTimeout,
  cancelTrigger = clearTimeout,
  createAbortController = () => new AbortController(),
  initialDelayMs = localScheduledInitialDelayMs,
  intervalMs = localScheduledIntervalMs,
  retryDelayMs = localScheduledRetryDelayMs,
} = {}) {
  let timer = null;
  let activeController = null;
  let started = false;
  let stopping = false;

  const schedule = (delay) => {
    if (stopping || timer || activeController) return;
    timer = scheduleTrigger(() => {
      timer = null;
      void execute();
    }, delay);
  };

  const execute = async () => {
    if (stopping || activeController) return;
    const controller = createAbortController();
    activeController = controller;
    let nextDelay = intervalMs;
    try {
      await trigger({ signal: controller.signal });
    } catch (error) {
      nextDelay = retryDelayMs;
      if (!stopping && !(error instanceof Error && error.name === "AbortError")) {
        const message = error instanceof Error ? error.message : String(error);
        console.warn(`本地 Cloudflare 定时事件暂未触发（${message.slice(0, 200)}），将在 ${retryDelayMs}ms 后重试`);
      }
    } finally {
      if (activeController === controller) activeController = null;
      if (!stopping) schedule(nextDelay);
    }
  };

  return {
    start() {
      if (started || stopping) return;
      started = true;
      schedule(initialDelayMs);
      console.log(`本地 Cloudflare 定时任务触发器已启动：每 ${Math.round(intervalMs / 1_000)} 秒检查一次市场图片缓存与云端 AI 标注队列`);
    },
    stop() {
      stopping = true;
      if (timer) {
        cancelTrigger(timer);
        timer = null;
      }
      activeController?.abort();
      activeController = null;
    },
  };
}

function observeProcessExit(child) {
  if (child.exitCode != null || child.signalCode != null) {
    return Promise.resolve({ kind: "exit", code: child.exitCode, signal: child.signalCode });
  }
  return new Promise((resolveExit) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolveExit(result);
    };
    child.once("error", (error) => finish({ kind: "error", error }));
    child.once("exit", (code, signal) => finish({ kind: "exit", code, signal }));
  });
}

export async function terminateOwnedProcessTree(child, {
  spawnProcess = spawn,
  platform = process.platform,
} = {}) {
  if (!child || child.exitCode != null || child.signalCode != null) return;

  if (platform === "win32" && Number.isSafeInteger(child.pid) && child.pid > 0) {
    const killer = spawnProcess(
      "taskkill.exe",
      ["/PID", String(child.pid), "/T", "/F"],
      { stdio: "ignore", windowsHide: true },
    );
    const result = await observeProcessExit(killer);
    if (result.kind === "error") throw result.error;
    return;
  }

  const exit = observeProcessExit(child);
  child.kill("SIGTERM");
  const stopped = await Promise.race([
    exit.then(() => true),
    waitForDelay(5_000).then(() => false),
  ]);
  if (!stopped && child.exitCode == null && child.signalCode == null) child.kill("SIGKILL");
}

export async function waitForLocalWorkerPortRelease({
  checkPort = isLocalWorkerPortInUse,
  wait = waitForDelay,
  now = Date.now,
  signal,
  timeoutMs = localWorkerPortReleaseTimeoutMs,
} = {}) {
  const startedAt = now();
  while (await checkPort(localWorkerPort)) {
    if (now() - startedAt >= timeoutMs) {
      throw new Error("旧本地 Worker 未在时限内释放端口 3000，已停止自动重启以避免覆盖其他进程");
    }
    if ((await wait(250, signal)) === false) return false;
  }
  return true;
}

function boundedProcessReason(result) {
  if (result.kind === "error") {
    const message = result.error instanceof Error ? result.error.message : String(result.error);
    return `启动异常：${message.slice(0, 160)}`;
  }
  return result.signal
    ? `进程被信号 ${result.signal} 中断`
    : `进程退出码 ${result.code ?? "未知"}`;
}

export async function superviseLocalWorker({
  root = projectRoot,
  wranglerArgs = [],
  spawnProcess = spawn,
  livenessMonitor = monitorLocalWorkerLiveness,
  readinessMonitor = monitorLocalWorkerReadiness,
  terminateProcessTree = terminateOwnedProcessTree,
  checkPort = isLocalWorkerPortInUse,
  wait = waitForDelay,
  now = Date.now,
  signal,
  logger = console,
  livenessInitialDelayMs = localLivenessInitialDelayMs,
  livenessIntervalMs = localLivenessIntervalMs,
  livenessFailureThreshold = localLivenessFailureThreshold,
  readinessInitialDelayMs = localReadinessInitialDelayMs,
  readinessIntervalMs = localReadinessIntervalMs,
  stableLifetimeMs = localWorkerStableLifetimeMs,
  restartWindowMs = localWorkerRestartWindowMs,
  maxRestartsPerWindow = localWorkerMaxRestartsPerWindow,
  prepareRestart,
} = {}) {
  let consecutiveShortLivedRestarts = 0;
  let restartTimestamps = [];
  let hasLaunched = false;

  while (!signal?.aborted) {
    if (hasLaunched) await prepareRestart?.(root);
    hasLaunched = true;
    const runtimeCommand = getLocalWorkerRuntimeCommand(root, wranglerArgs);
    const startedAt = now();
    const child = spawnProcess(
      runtimeCommand.command,
      runtimeCommand.args,
      { cwd: root, stdio: "inherit", windowsHide: true },
    );
    const exitOutcome = observeProcessExit(child);
    const monitorController = new AbortController();
    const abortMonitor = () => monitorController.abort();
    if (signal?.aborted) monitorController.abort();
    else signal?.addEventListener("abort", abortMonitor, { once: true });

    const readinessObservation = Promise.resolve().then(() => readinessMonitor({
      signal: monitorController.signal,
      initialDelayMs: readinessInitialDelayMs,
      intervalMs: readinessIntervalMs,
      onDegraded: ({ error }) => {
        const message = error instanceof Error ? error.message : String(error);
        logger.warn(`本地 Worker Django 就绪状态已降级（${message.slice(0, 180)}）；将继续运行且不会因此重启`);
      },
      onRecovered: ({ previousFailures }) => {
        logger.warn(`本地 Worker Django 就绪状态已恢复（此前连续失败 ${previousFailures} 次）`);
      },
    })).catch((error) => {
      if (!monitorController.signal.aborted) {
        const message = error instanceof Error ? error.message : String(error);
        logger.warn(`本地 Worker Django 就绪观察器异常停止（${message.slice(0, 180)}）；不会因此重启`);
      }
      return { status: "observer_error" };
    });
    const livenessOutcome = Promise.resolve().then(() => livenessMonitor({
      signal: monitorController.signal,
      initialDelayMs: livenessInitialDelayMs,
      intervalMs: livenessIntervalMs,
      failureThreshold: livenessFailureThreshold,
      onFailure: ({ consecutiveFailures, error }) => {
        const message = error instanceof Error ? error.message : String(error);
        logger.warn(`本地 Worker 存活检查失败（${consecutiveFailures}/${livenessFailureThreshold}）：${message.slice(0, 180)}`);
      },
      onRecovered: ({ previousFailures }) => {
        logger.warn(`本地 Worker 存活检查已恢复（此前连续失败 ${previousFailures} 次）`);
      },
    })).catch((error) => ({
      status: "unhealthy",
      consecutiveFailures: livenessFailureThreshold,
      error,
    }));

    const outcome = await Promise.race([
      exitOutcome.then((result) => ({ source: "process", result })),
      livenessOutcome.then((result) => ({ source: "liveness", result })),
    ]);
    monitorController.abort();
    void readinessObservation;
    signal?.removeEventListener("abort", abortMonitor);

    if (signal?.aborted) {
      if (child.exitCode == null && child.signalCode == null) await terminateProcessTree(child);
      await waitForLocalWorkerPortRelease({ checkPort, wait, now, timeoutMs: localWorkerPortReleaseTimeoutMs });
      return;
    }

    let reason;
    if (outcome.source === "liveness") {
      if (outcome.result.status === "aborted") return;
      reason = `存活检查连续 ${outcome.result.consecutiveFailures} 次失败`;
      await terminateProcessTree(child);
    } else {
      reason = boundedProcessReason(outcome.result);
    }

    await waitForLocalWorkerPortRelease({ checkPort, wait, now, signal });
    const restartAt = now();
    restartTimestamps = restartTimestamps.filter((timestamp) => restartAt - timestamp < restartWindowMs);
    restartTimestamps.push(restartAt);
    if (restartTimestamps.length > maxRestartsPerWindow) {
      throw new Error(`本地 Worker 在 ${Math.round(restartWindowMs / 60_000)} 分钟内已自动重启 ${maxRestartsPerWindow} 次，已停止自愈以避免重启风暴；请查看启动日志`);
    }

    if (restartAt - startedAt >= stableLifetimeMs) consecutiveShortLivedRestarts = 0;
    else consecutiveShortLivedRestarts += 1;
    const delay = getLocalWorkerRestartDelay(Math.max(0, consecutiveShortLivedRestarts - 1));
    logger.warn(`本地 Worker 不可用（${reason}），将在 ${delay}ms 后自动重启`);
    if ((await wait(delay, signal)) === false) return;
  }
}

async function waitForChild(child, operation) {
  const result = await new Promise((resolveExit, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolveExit({ code, signal }));
  });

  if (result.code !== 0) {
    const reason = result.signal ? `被信号 ${result.signal} 中断` : `退出码 ${result.code ?? "未知"}`;
    throw new Error(`${operation}失败：${reason}`);
  }
}

export async function buildLocalWorker(root = projectRoot) {
  const { command, args, env } = getLocalWorkerBuildCommand(root);
  const child = spawn(command, args, {
    cwd: root,
    env,
    stdio: "inherit",
    windowsHide: true,
  });
  await waitForChild(child, "本地 Worker 构建");
}

export async function ensureRuntimeDevVarsLink(root = projectRoot) {
  const sourcePath = resolve(root, ".dev.vars");
  const runtimePath = resolve(root, "dist", "server", ".dev.vars");

  try {
    await access(sourcePath);
  } catch {
    throw new Error("缺少本机 .dev.vars，无法为 Worker 加载本机密钥配置");
  }

  await mkdir(dirname(runtimePath), { recursive: true });
  try {
    await link(sourcePath, runtimePath);
    return { runtimePath, created: true };
  } catch (error) {
    if (error && typeof error === "object" && "code" in error && error.code === "EEXIST") {
      const [sourceInfo, runtimeInfo] = await Promise.all([
        stat(sourcePath, { bigint: true }),
        stat(runtimePath, { bigint: true }),
      ]);
      if (sourceInfo.dev !== runtimeInfo.dev || sourceInfo.ino !== runtimeInfo.ino) {
        throw new Error("已存在的 Worker .dev.vars 不是受控源文件的硬链接");
      }
      return { runtimePath, created: false };
    }
    throw error;
  }
}

export async function startLocalWorker(wranglerArgs = process.argv.slice(2)) {
  await assertLegacyWorkerLaunchAllowed();
  await assertLocalWorkerPortAvailable();
  await assertTmallWorkflowHelperPortAvailable();
  const { runtimePath, created } = await ensureRuntimeDevVarsLink();
  const runtimeCommand = getLocalWorkerRuntimeCommand(projectRoot, wranglerArgs);
  const wranglerEntrypoint = runtimeCommand.args[0];
  try {
    await access(wranglerEntrypoint);
  } catch {
    throw new Error("未找到 Wrangler。请先安装项目依赖后重试");
  }

  console.log(`${created ? "已创建" : "复用"}运行时密钥链接：${runtimePath}`);
  const helperSupervisor = createTmallWorkflowHelperSupervisor();
  const scheduledTriggerSupervisor = createLocalScheduledTriggerSupervisor();
  const shutdownController = new AbortController();
  const requestShutdown = () => shutdownController.abort();
  process.once("SIGINT", requestShutdown);
  process.once("SIGTERM", requestShutdown);
  helperSupervisor.start();
  try {
    scheduledTriggerSupervisor.start();
    await superviseLocalWorker({
      root: projectRoot,
      wranglerArgs,
      signal: shutdownController.signal,
      prepareRestart: async (root) => {
        await assertLegacyWorkerLaunchAllowed();
        await ensureRuntimeDevVarsLink(root);
      },
    });
  } finally {
    process.removeListener("SIGINT", requestShutdown);
    process.removeListener("SIGTERM", requestShutdown);
    scheduledTriggerSupervisor.stop();
    helperSupervisor.stop();
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { shouldBuild, wranglerArgs } = parseLocalWorkerArguments(process.argv.slice(2));
  const run = async () => {
    await assertLegacyWorkerLaunchAllowed();
    if (shouldBuild) {
      await assertLocalWorkerPortAvailable();
      await buildLocalWorker();
    }
    await startLocalWorker(wranglerArgs);
  };
  run().catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  });
}
