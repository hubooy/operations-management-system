import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  assertLocalWorkerPortAvailable,
  assertTmallWorkflowHelperPortAvailable,
  createLocalScheduledTriggerSupervisor,
  createTmallWorkflowHelperSupervisor,
  ensureRuntimeDevVarsLink,
  getLocalWorkerBuildCommand,
  getLocalWorkerRestartDelay,
  getLocalWorkerRuntimeCommand,
  getTmallWorkflowHelperCommand,
  getTmallWorkflowHelperRestartDelay,
  localLivenessFailureThreshold,
  monitorLocalWorkerLiveness,
  monitorLocalWorkerReadiness,
  parseLocalWorkerArguments,
  probeLocalWorkerLiveness,
  probeLocalWorkerReadiness,
  superviseLocalWorker,
  triggerLocalScheduledEvent,
} from "../tools/start-local-worker.mjs";

class FakeLocalWorkerChild extends EventEmitter {
  pid: number;
  exitCode: number | null = null;
  signalCode: string | null = null;

  constructor(pid: number) {
    super();
    this.pid = pid;
  }
}

function waitForMonitorAbort(signal?: AbortSignal) {
  if (signal?.aborted) return Promise.resolve({ status: "aborted", consecutiveFailures: 0 });
  return new Promise<{ status: "aborted"; consecutiveFailures: number }>((resolveAbort) => {
    signal?.addEventListener(
      "abort",
      () => resolveAbort({ status: "aborted", consecutiveFailures: 0 }),
      { once: true },
    );
  });
}

test("local Worker build always enables the local-only build flag", () => {
  const command = getLocalWorkerBuildCommand("D:/example-project");

  assert.equal(command.args.at(-1), "build");
  assert.match(command.args[0] ?? "", /example-project[\\/]node_modules[\\/]vinext[\\/]dist[\\/]cli\.js$/);
  assert.equal(command.env.VITE_TERUISI_LOCAL_BUILD, "true");
});

test("local Worker keeps runtime arguments bounded without test-only middleware", () => {
  const command = getLocalWorkerRuntimeCommand("D:/example-project", ["--log-level", "warn"]);

  assert.match(command.args[0] ?? "", /example-project[\\/]node_modules[\\/]wrangler[\\/]bin[\\/]wrangler\.js$/);
  assert.equal(command.args.includes("--test-scheduled"), false);
  assert.deepEqual(
    command.args.slice(command.args.indexOf("--ip"), command.args.indexOf("--ip") + 2),
    ["--ip", "127.0.0.1"],
  );
  assert.equal(command.args.filter((argument) => argument === "--ip").length, 1);
  assert.equal(command.args[command.args.indexOf("--persist-to") + 1], ".wrangler/state");
  assert.equal(command.args.filter((argument) => argument === "--persist-to").length, 1);
  assert.deepEqual(command.args.slice(-2), ["--log-level", "warn"]);
});

test("local Worker can mount one explicit existing Wrangler state directory", () => {
  const command = getLocalWorkerRuntimeCommand(
    "D:/deployment-worktree",
    [],
    "D:/operations-system/.wrangler/state",
  );

  assert.equal(
    command.args[command.args.indexOf("--persist-to") + 1],
    "D:/operations-system/.wrangler/state",
  );
  assert.equal(command.args.filter((argument) => argument === "--persist-to").length, 1);
});

test("local Worker starts the loopback-only Tmall workflow helper without passing credentials", () => {
  const command = getTmallWorkflowHelperCommand("D:/example-project", 5791);

  assert.equal(command.command, process.execPath);
  assert.deepEqual(command.args.slice(0, 2), ["--import", "tsx"]);
  assert.match(command.args[2] ?? "", /example-project[\\/]tools[\\/]tmall-sycm-cookie-pipeline\.ts$/);
  assert.deepEqual(command.args.slice(3), ["serve", "--port", "5791"]);
  assert.equal(command.args.some((argument) => /--(?:cookie|password|token)|(?:cookie|password|token)=/i.test(argument)), false);
});

test("control-only build flag is consumed before arguments reach Wrangler", () => {
  const parsed = parseLocalWorkerArguments(["--build", "--log-level", "warn"]);

  assert.equal(parsed.shouldBuild, true);
  assert.deepEqual(parsed.wranglerArgs, ["--log-level", "warn"]);
  assert.equal(parseLocalWorkerArguments(["--log-level", "debug"]).shouldBuild, false);
});

test("local Worker rejects every external host or IP binding override", () => {
  for (const args of [
    ["--ip", "0.0.0.0"],
    ["--ip=0.0.0.0"],
    ["--host", "0.0.0.0"],
    ["--host=example.test"],
    ["--hostname", "example.test"],
  ]) {
    assert.throws(
      () => parseLocalWorkerArguments(args),
      /监听地址固定为 127\.0\.0\.1.*不允许.*覆盖/,
    );
    assert.throws(
      () => getLocalWorkerRuntimeCommand("D:/example-project", args),
      /监听地址固定为 127\.0\.0\.1.*不允许.*覆盖/,
    );
  }
});

test("local Worker refuses to build or start while port 3000 is occupied", async () => {
  await assert.rejects(
    assertLocalWorkerPortAvailable(async () => true),
    /端口 3000 已有服务运行/,
  );
  await assert.doesNotReject(assertLocalWorkerPortAvailable(async () => false));
});

test("local Worker refuses an unknown listener on the Tmall helper port", async () => {
  await assert.rejects(
    assertTmallWorkflowHelperPortAvailable(async (port) => {
      assert.equal(port, 5791);
      return true;
    }),
    /端口 5791 已有服务运行/,
  );
  await assert.doesNotReject(assertTmallWorkflowHelperPortAvailable(async () => false));
});

test("local Worker liveness probe is bounded, loopback-only, and independent from D1", async () => {
  let request: { url: string; init: RequestInit } | null = null;
  const status = await probeLocalWorkerLiveness({
    fetchImpl: async (url, init) => {
      request = { url: String(url), init: init ?? {} };
      return Response.json({ ok: true });
    },
    timeoutMs: 100,
  });

  assert.equal(status, 200);
  assert.equal(request?.url, "http://127.0.0.1:3000/_teruisi/local/health/live");
  assert.equal(request?.init.method, "GET");
  assert.equal(new Headers(request?.init.headers).get("x-teruisi-local-health"), "1");
  assert.equal(request?.init.cache, "no-store");

  await assert.rejects(
    probeLocalWorkerLiveness({
      fetchImpl: async () => Response.json({ ok: false }, { status: 503 }),
      timeoutMs: 100,
    }),
    /存活检查失败：HTTP 503/,
  );
});

test("local Worker readiness probe keeps the D1 ready/degraded contract", async () => {
  let request: { url: string; init: RequestInit } | null = null;
  const status = await probeLocalWorkerReadiness({
    fetchImpl: async (url, init) => {
      request = { url: String(url), init: init ?? {} };
      return Response.json({ ok: true, status: "ready" });
    },
    timeoutMs: 100,
  });

  assert.equal(status, 200);
  assert.equal(request?.url, "http://127.0.0.1:3000/_teruisi/local/health/ready");
  assert.equal(new Headers(request?.init.headers).get("x-teruisi-local-health"), "1");

  await assert.rejects(
    probeLocalWorkerReadiness({
      fetchImpl: async () => Response.json({ ok: false, status: "degraded" }, { status: 503 }),
      timeoutMs: 100,
    }),
    /Django 就绪检查失败：HTTP 503/,
  );
});

test("liveness monitor trips only on consecutive failures", async () => {
  const outcomes = ["failure", "success", "failure", "failure", "failure"];
  const recovered: number[] = [];
  const failures: number[] = [];
  const result = await monitorLocalWorkerLiveness({
    probe: async () => {
      const outcome = outcomes.shift();
      if (outcome === "failure") throw new Error("Worker unavailable");
    },
    wait: async () => true,
    initialDelayMs: 0,
    intervalMs: 0,
    failureThreshold: 3,
    onFailure: ({ consecutiveFailures }) => failures.push(consecutiveFailures),
    onRecovered: ({ previousFailures }) => recovered.push(previousFailures),
  });

  assert.equal(result.status, "unhealthy");
  assert.equal(result.consecutiveFailures, 3);
  assert.deepEqual(failures, [1, 1, 2, 3]);
  assert.deepEqual(recovered, [1]);
});

test("local Worker default liveness window cannot interrupt a bounded 120-second import", () => {
  const minimumFailureWindowMs = (localLivenessFailureThreshold - 1) * 10_000;

  assert.ok(minimumFailureWindowMs > 120_000);
});

test("readiness observer reports degraded and recovered states without becoming unhealthy", async () => {
  const controller = new AbortController();
  const outcomes = ["failure", "failure", "success", "failure", "failure"];
  const failures: number[] = [];
  const degraded: number[] = [];
  const recovered: number[] = [];
  const result = await monitorLocalWorkerReadiness({
    probe: async () => {
      const outcome = outcomes.shift();
      if (outcome === "failure") throw new Error("D1 timeout");
    },
    wait: async () => {
      if (outcomes.length === 0) {
        controller.abort();
        return false;
      }
      return true;
    },
    signal: controller.signal,
    initialDelayMs: 0,
    intervalMs: 0,
    onFailure: ({ consecutiveFailures }) => failures.push(consecutiveFailures),
    onDegraded: ({ consecutiveFailures }) => degraded.push(consecutiveFailures),
    onRecovered: ({ previousFailures }) => recovered.push(previousFailures),
  });

  assert.equal(result.status, "aborted");
  assert.equal(result.state, "degraded");
  assert.deepEqual(failures, [1, 2, 1, 2]);
  assert.deepEqual(degraded, [1, 1]);
  assert.deepEqual(recovered, [2]);
});

test("continuous D1 readiness timeouts never restart the local Worker", async () => {
  const controller = new AbortController();
  const children: FakeLocalWorkerChild[] = [];
  const terminated: number[] = [];
  let readinessAttempts = 0;

  await superviseLocalWorker({
    root: "D:/example-project",
    spawnProcess: () => {
      const child = new FakeLocalWorkerChild(10_000 + children.length);
      children.push(child);
      return child;
    },
    readinessMonitor: async ({ signal, onDegraded }) => {
      for (let attempt = 0; attempt < 5; attempt += 1) {
        readinessAttempts += 1;
        if (attempt === 0) onDegraded?.({ consecutiveFailures: 1, error: new Error("D1 timeout") });
        await Promise.resolve();
      }
      return waitForMonitorAbort(signal);
    },
    livenessMonitor: async ({ signal }) => {
      await new Promise((resolveTurn) => setImmediate(resolveTurn));
      controller.abort();
      return waitForMonitorAbort(signal);
    },
    terminateProcessTree: async (child: FakeLocalWorkerChild) => {
      terminated.push(child.pid);
      child.exitCode = 0;
      child.emit("exit", 0, null);
    },
    checkPort: async () => false,
    wait: async () => true,
    signal: controller.signal,
    logger: { warn() {} },
  });

  assert.equal(readinessAttempts, 5);
  assert.equal(children.length, 1);
  assert.deepEqual(terminated, [children[0]!.pid]);
});

test("local Worker supervisor restarts only after a liveness failure", async () => {

  const controller = new AbortController();
  const children: FakeLocalWorkerChild[] = [];
  const terminated: number[] = [];
  let clock = 0;
  let livenessRuns = 0;
  await superviseLocalWorker({
    root: "D:/example-project",
    spawnProcess: () => {
      const child = new FakeLocalWorkerChild(20_000 + children.length);
      children.push(child);
      return child;
    },
    readinessMonitor: ({ signal }) => waitForMonitorAbort(signal),
    livenessMonitor: async ({ signal }) => {
      livenessRuns += 1;
      if (livenessRuns === 1) return { status: "unhealthy", consecutiveFailures: 3 };
      controller.abort();
      return waitForMonitorAbort(signal);
    },
    terminateProcessTree: async (child: FakeLocalWorkerChild) => {
      terminated.push(child.pid);
      child.exitCode = 1;
      child.emit("exit", 1, null);
    },
    checkPort: async () => false,
    wait: async () => true,
    now: () => { clock += 1_000; return clock; },
    signal: controller.signal,
    logger: { warn() {} },
  });

  assert.equal(children.length, 2);
  assert.deepEqual(terminated, children.map((child) => child.pid));
});

test("local Worker supervisor restarts an exited process without treating readiness as fatal", async () => {
  const controller = new AbortController();
  const children: FakeLocalWorkerChild[] = [];
  const terminated: number[] = [];
  const preparedRoots: string[] = [];
  const warnings: string[] = [];
  let clock = 0;

  await superviseLocalWorker({
    root: "D:/example-project",
    spawnProcess: () => {
      const child = new FakeLocalWorkerChild(30_000 + children.length);
      children.push(child);
      if (children.length === 1) {
        queueMicrotask(() => {
          child.exitCode = 1;
          child.emit("exit", 1, null);
        });
      } else {
        queueMicrotask(() => controller.abort());
      }
      return child;
    },
    readinessMonitor: async ({ signal, onDegraded }) => {
      onDegraded?.({ consecutiveFailures: 1, error: new Error("D1 timeout") });
      return waitForMonitorAbort(signal);
    },
    livenessMonitor: ({ signal }) => waitForMonitorAbort(signal),
    terminateProcessTree: async (child: FakeLocalWorkerChild) => {
      terminated.push(child.pid);
      child.exitCode = 0;
      child.emit("exit", 0, null);
    },
    checkPort: async () => false,
    wait: async () => true,
    prepareRestart: async (root: string) => { preparedRoots.push(root); },
    now: () => { clock += 1_000; return clock; },
    signal: controller.signal,
    logger: { warn(message: string) { warnings.push(message); } },
  });

  assert.equal(children.length, 2);
  assert.deepEqual(terminated, [children[1]!.pid]);
  assert.deepEqual(preparedRoots, ["D:/example-project"]);
  assert.equal(warnings.some((message) => /进程退出码 1/.test(message)), true);
});

test("local Worker supervisor stops its owned process on shutdown without restarting", async () => {
  const controller = new AbortController();
  const children: FakeLocalWorkerChild[] = [];
  const terminated: number[] = [];

  await superviseLocalWorker({
    root: "D:/example-project",
    spawnProcess: () => {
      const child = new FakeLocalWorkerChild(40_000);
      children.push(child);
      queueMicrotask(() => controller.abort());
      return child;
    },
    readinessMonitor: ({ signal }) => waitForMonitorAbort(signal),
    livenessMonitor: ({ signal }) => waitForMonitorAbort(signal),
    terminateProcessTree: async (child: FakeLocalWorkerChild) => {
      terminated.push(child.pid);
      child.exitCode = 0;
      child.emit("exit", 0, null);
    },
    checkPort: async () => false,
    wait: async () => true,
    signal: controller.signal,
    logger: { warn() {} },
  });

  assert.equal(children.length, 1);
  assert.deepEqual(terminated, [40_000]);
});

test("local Worker supervisor fails closed before a liveness restart storm", async () => {
  let launches = 0;
  let clock = 0;
  await assert.rejects(
    superviseLocalWorker({
      root: "D:/example-project",
      spawnProcess: () => new FakeLocalWorkerChild(50_000 + (++launches)),
      readinessMonitor: ({ signal }) => waitForMonitorAbort(signal),
      livenessMonitor: async () => ({ status: "unhealthy", consecutiveFailures: 3 }),
      terminateProcessTree: async (child: FakeLocalWorkerChild) => {
        child.exitCode = 1;
        child.emit("exit", 1, null);
      },
      checkPort: async () => false,
      wait: async () => true,
      now: () => { clock += 1_000; return clock; },
      restartWindowMs: 60_000,
      maxRestartsPerWindow: 2,
      logger: { warn() {} },
    }),
    /已自动重启 2 次.*停止自愈/,
  );
  assert.equal(launches, 3);
  assert.deepEqual([0, 1, 2, 99].map(getLocalWorkerRestartDelay), [1_000, 2_000, 4_000, 30_000]);
});

test("Worker health routes keep liveness D1-free and readiness explicitly degraded", async () => {
  const source = await readFile(new URL("../worker/index.ts", import.meta.url), "utf8");
  const livenessStart = source.indexOf("if (url.pathname === localLivenessPath)");
  const readinessStart = source.indexOf("if (url.pathname === localReadinessPath)");
  const scheduledStart = source.indexOf("if (url.pathname === localScheduledPath)");

  assert.notEqual(livenessStart, -1);
  assert.ok(readinessStart > livenessStart);
  assert.ok(scheduledStart > readinessStart);
  const livenessBlock = source.slice(livenessStart, readinessStart);
  const readinessBlock = source.slice(readinessStart, scheduledStart);
  assert.doesNotMatch(livenessBlock, /env\.DB|sqlite_master/);
  assert.match(livenessBlock, /status: "live"/);
  assert.match(readinessBlock, /probeDjangoBackendReadiness/);
  assert.match(readinessBlock, /status: "ready"/);
  assert.match(readinessBlock, /status: "degraded"/);
  assert.match(source, /\/_teruisi\/local\/health\/ready/);
  assert.match(source, /\/_teruisi\/local\/health\/live/);
  assert.match(source, /x-teruisi-local-health/);
  assert.doesNotMatch(source, /env\.DB|sqlite_master/);
  assert.match(source, /django_unavailable/);
  assert.match(source, /TERUISI_RUNTIME_ENV === "development"/);
});

test("Tmall helper supervisor restarts a completed one-shot process and stops its owned child", () => {
  class FakeChild extends EventEmitter {
    exitCode: number | null = null;
    signalCode: string | null = null;
    killed = false;

    kill() {
      this.killed = true;
      this.exitCode = 0;
      this.emit("exit", 0, null);
      return true;
    }
  }

  const children: FakeChild[] = [];
  const timers: Array<{ callback: () => void; delay: number; cancelled?: boolean }> = [];
  const supervisor = createTmallWorkflowHelperSupervisor({
    root: "D:/example-project",
    spawnProcess: () => {
      const child = new FakeChild();
      children.push(child);
      return child;
    },
    scheduleRestart: (callback, delay) => {
      const timer = { callback, delay };
      timers.push(timer);
      return timer;
    },
    cancelRestart: (timer) => {
      timer.cancelled = true;
    },
    now: () => 1_000,
  });

  supervisor.start();
  assert.equal(children.length, 1);
  children[0]!.emit("exit", 0, null);
  assert.equal(timers.length, 1);
  assert.equal(timers[0]!.delay, 500);
  timers[0]!.callback();
  assert.equal(children.length, 2);

  supervisor.stop();
  assert.equal(children[1]!.killed, true);
  assert.equal(timers.length, 1);
  assert.deepEqual([0, 1, 2, 99].map(getTmallWorkflowHelperRestartDelay), [500, 1_000, 2_000, 5_000]);
});

test("local Worker triggers the loopback Cloudflare scheduled handler without credentials", async () => {
  let request: { url: string; init: RequestInit } | null = null;
  const status = await triggerLocalScheduledEvent({
    fetchImpl: async (url, init) => {
      request = { url: String(url), init: init ?? {} };
      return new Response("ok", { status: 200 });
    },
  });

  assert.equal(status, 200);
  assert.equal(request?.url, "http://127.0.0.1:3000/_teruisi/local/market-annotation-scheduled");
  assert.equal(request?.init.method, "POST");
  assert.deepEqual(request?.init.headers, { "x-teruisi-local-scheduled": "1" });
});

test("local scheduled trigger retries startup failures and never overlaps its own timer", async () => {
  const timers: Array<{ callback: () => void; delay: number; cancelled?: boolean }> = [];
  let attempts = 0;
  const supervisor = createLocalScheduledTriggerSupervisor({
    trigger: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("worker not ready");
    },
    scheduleTrigger: (callback, delay) => {
      const timer = { callback, delay };
      timers.push(timer);
      return timer;
    },
    cancelTrigger: (timer) => { timer.cancelled = true; },
    initialDelayMs: 10,
    intervalMs: 60,
    retryDelayMs: 5,
  });

  supervisor.start();
  supervisor.start();
  assert.equal(timers.length, 1);
  assert.equal(timers[0]?.delay, 10);

  timers[0]!.callback();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(attempts, 1);
  assert.equal(timers[1]?.delay, 5);

  timers[1]!.callback();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(attempts, 2);
  assert.equal(timers[2]?.delay, 60);

  supervisor.stop();
  assert.equal(timers[2]?.cancelled, true);
});

test("prebuilt local Worker receives the ignored root .dev.vars through a hard link", async () => {
  const root = await mkdtemp(join(tmpdir(), "teruisi-local-worker-"));
  try {
    await mkdir(join(root, "dist", "server"), { recursive: true });
    await writeFile(join(root, ".dev.vars"), "AI_SECRET_ENCRYPTION_KEY=test-only-key\n", "utf8");

    const first = await ensureRuntimeDevVarsLink(root);
    const second = await ensureRuntimeDevVarsLink(root);

    assert.equal(first.created, true);
    assert.equal(second.created, false);
    assert.equal(await readFile(first.runtimePath, "utf8"), "AI_SECRET_ENCRYPTION_KEY=test-only-key\n");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
