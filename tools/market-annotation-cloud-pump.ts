/**
 * 云端标注后台泵。
 *
 * 云端识别的模型调用本来就发生在服务端（runVisionAnnotation 在 Worker 里执行），
 * 浏览器只是反复 POST run_batch 的「泵」。这个常驻进程把同一个泵搬到浏览器之外，
 * 关掉页面后任务继续跑；中断也不会丢进度——续跑完全依赖服务端既有的推理租约、
 * attempt_count 与幂等入库。
 *
 *   TERUISI_SITE_URL=https://你的站点 \
 *   TERUISI_ANNOTATION_AGENT_TOKEN=创建 agent 时的一次性 token \
 *   npm run market:annotation-cloud-pump
 *
 * 可选参数：--once 只推进一批后退出；--job <id> 固定任务；--poll-ms <n> 空闲轮询间隔。
 */
import { setTimeout as delay } from "node:timers/promises";

import { MARKET_ANNOTATION_CONCURRENCY_LIMITS } from "../lib/market/annotation-limits";
import { AnnotationRunRetryController, annotationRequestRetryKind, annotationRetryDelayMs } from "../lib/market/annotation-retry";

type PumpResult = {
  idle?: boolean; jobId?: string; category?: string; concurrency?: number;
  done?: boolean; waiting?: boolean; processedCount?: number; reusedCount?: number; failedCount?: number;
  failureKind?: "rate_limit" | "transient" | "permanent"; failureCode?: string; failureMessage?: string; retryAfterMs?: number;
  job?: { status?: string; completedCount?: number; failedCount?: number; totalCount?: number } | null;
};

const siteUrl = requiredEnv("TERUISI_SITE_URL").replace(/\/$/, "");
const token = requiredEnv("TERUISI_ANNOTATION_AGENT_TOKEN");
const once = process.argv.includes("--once");
const pinnedJobId = argument("--job")?.trim() || "";
const pollMs = Math.max(1_000, Math.min(60_000, Number(argument("--poll-ms") || 10_000)));
const REQUEST_TIMEOUT_MS = 110_000;
const IDLE_SPIN_MS = 250;

let stopping = false;
let fatalError: unknown = null;
// 所有等待都挂在这个信号上，Ctrl+C 才能立刻生效，而不是先睡满退避或轮询间隔。
const stopController = new AbortController();
async function sleep(ms: number) {
  try { await delay(Math.max(1, ms), undefined, { signal: stopController.signal }); }
  catch { /* 收到停止信号，立即返回 */ }
}

async function pump(jobId: string): Promise<PumpResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(siteUrl + "/api/market/annotations/worker", {
      method: "POST", redirect: "manual", signal: controller.signal,
      headers: { "content-type": "application/json", authorization: "Bearer " + token },
      body: JSON.stringify({ action: "pump_cloud", ...(jobId ? { jobId } : {}) }),
    });
    const payload = await response.json().catch(() => null) as (PumpResult & { error?: string }) | null;
    if (!response.ok || !payload) {
      const failure = new Error(String(payload?.error || "泵接口 HTTP " + response.status)) as Error & { status: number };
      failure.status = response.status;
      throw failure;
    }
    return payload;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("泵请求超时");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/** 用一个任务的全部并发通道跑到底；并发目标每轮从服务端读回，页面上改并发即时生效。 */
async function runJob(jobId: string, initialConcurrency: number) {
  const retry = new AnnotationRunRetryController(initialConcurrency);
  const totals = { processed: 0, reused: 0, failed: 0 };
  let done = false;
  let autoPaused = false;
  let pauseReason = "";
  let activeRequestCount = 0;

  const syncTarget = (next: number | undefined) => {
    if (!Number.isSafeInteger(next) || Number(next) < 1 || Number(next) === retry.targetConcurrency) return;
    retry.updateTarget(Number(next));
    log(`并发目标已按服务端配置调整为 ${retry.targetConcurrency}`);
  };
  const scheduleRetry = (kind: "transient" | "rate_limit", workerIndex: number, retryAfterMs = 0, failureCode = "", failureMessage = "") => {
    const decision = retry.schedule(kind, workerIndex, retryAfterMs);
    if (decision.suppressedByGlobalRateLimit || !decision.countedIncident) return;
    const cause = failureMessage.trim().slice(0, 300) || failureCode || "未返回具体原因";
    if (decision.shouldPause) {
      autoPaused = true;
      done = true;
      pauseReason = `运行并发降至 1 后又连续出现 ${decision.floorFailureCount} 个独立失败窗口，最近原因：${cause}`;
      log(`任务已自动暂停：${pauseReason}`);
      return;
    }
    const change = decision.concurrency < decision.previousConcurrency
      ? `运行并发 ${decision.previousConcurrency} → ${decision.concurrency}`
      : `运行并发保持 ${decision.concurrency}`;
    log(kind === "rate_limit"
      ? `供应商限流，${change}，全部通道 ${Math.ceil(decision.delayMs / 1_000)} 秒后续跑`
      : `模型或网络异常，${change}，通道 ${workerIndex} ${Math.ceil(decision.delayMs / 1_000)} 秒后重试`);
  };

  const worker = async (workerIndex: number) => {
    while (!stopping && !done) {
      while (!stopping && Date.now() < retry.blockedUntil(workerIndex)) {
        await sleep(Math.min(1_000, Math.max(1, retry.blockedUntil(workerIndex) - Date.now())));
      }
      if (stopping || done) break;
      if (activeRequestCount >= retry.workerLimit) { await sleep(IDLE_SPIN_MS); continue; }

      activeRequestCount += 1;
      let result: PumpResult | null = null;
      let failure: unknown = null;
      try { result = await pump(jobId); }
      catch (error) { failure = error; }
      finally { activeRequestCount -= 1; }

      if (failure) {
        const kind = annotationRequestRetryKind(failure);
        if (!kind) { fatalError = failure; stopping = true; break; }
        const message = failure instanceof Error && /超时|timeout/i.test(failure.message)
          ? "后台泵到服务端的请求超时"
          : "后台泵到服务端的网络请求异常";
        scheduleRetry(kind, workerIndex, 0, "request_transport", message);
        continue;
      }
      syncTarget(result?.concurrency);
      if (result?.idle || result?.done) { done = true; break; }
      if (result?.waiting) {
        await sleep(annotationRetryDelayMs("waiting", 0, Number(result?.retryAfterMs ?? 0)));
        continue;
      }
      const reused = Math.max(0, Number(result?.reusedCount ?? 0));
      const processed = Math.max(0, Number(result?.processedCount ?? 0));
      totals.reused += reused;
      totals.processed += processed;
      const failureKind = String(result?.failureKind ?? "");
      if (failureKind) totals.failed += Math.max(1, Number(result?.failedCount ?? 0));
      if (failureKind === "rate_limit" || failureKind === "transient") {
        scheduleRetry(failureKind, workerIndex, Number(result?.retryAfterMs ?? 0), String(result?.failureCode ?? ""), String(result?.failureMessage ?? ""));
      } else if (failureKind === "permanent") {
        log(`当前图片识别失败：${String(result?.failureMessage ?? result?.failureCode ?? "识别失败").slice(0, 300)}`);
      } else if (!failureKind && processed > reused) {
        const recovery = retry.recordSuccess(processed - reused);
        if (recovery.recovered) log(`连接恢复，运行并发提升至 ${recovery.concurrency}/${retry.targetConcurrency}`);
      } else if (!failureKind && processed === 0) {
        // 认领竞争（raced）时服务端不推进任何条目，稍等一拍避免空转打接口。
        await sleep(IDLE_SPIN_MS);
      }
      if (workerIndex === 0 && result?.job) {
        log(`进度 ${result.job.completedCount ?? 0}/${result.job.totalCount ?? 0}（失败 ${result.job.failedCount ?? 0}）· 本轮处理 ${totals.processed}，复用 ${totals.reused}`);
      }
    }
  };

  await Promise.all(Array.from({ length: MARKET_ANNOTATION_CONCURRENCY_LIMITS.maximum }, (_, index) => worker(index)));
  return { ...totals, autoPaused, pauseReason };
}

async function main() {
  log(`后台泵已启动，站点 ${siteUrl}${pinnedJobId ? `，固定任务 ${pinnedJobId}` : ""}`);
  while (!stopping) {
    const probe = await pump(pinnedJobId);
    if (probe.idle || !probe.jobId) {
      if (once) { log("当前没有待推进的云端标注任务"); return; }
      await sleep(pollMs);
      continue;
    }
    log(`接管任务 ${probe.jobId}（${probe.category}），配置并发 ${probe.concurrency}`);
    if (once) { log("--once：已推进一批后退出"); return; }
    const totals = await runJob(probe.jobId, Math.max(1, Number(probe.concurrency) || 1));
    log(`任务 ${probe.jobId} 本轮结束：处理 ${totals.processed}（复用 ${totals.reused}，失败 ${totals.failed}）`);
    if (fatalError) throw fatalError;
    if (totals.autoPaused) {
      log(`后台泵停止续跑，需人工检查后重新启动：${totals.pauseReason}`);
      return;
    }
    if (pinnedJobId) return;
  }
}

function log(message: string) { process.stdout.write(`[${new Date().toISOString()}] ${message}\n`); }
function requiredEnv(name: string) { const value = process.env[name]?.trim(); if (!value) throw new Error("Missing environment variable " + name); return value; }
function argument(name: string) { const index = process.argv.indexOf(name); return index >= 0 ? process.argv[index + 1] : undefined; }

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    if (stopping) process.exit(130);
    stopping = true;
    stopController.abort();
    log(`收到 ${signal}，等待在途请求结束后退出；再按一次立即中止`);
  });
}

void main().catch((error) => { process.stderr.write((error instanceof Error ? error.message : String(error)) + "\n"); process.exitCode = 1; });
