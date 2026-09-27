import assert from "node:assert/strict";
import test from "node:test";
import { dispatchAnnotationJobs } from "../lib/market/annotation-dispatch";
import { executeAnnotationAttempt } from "../lib/market/annotation-attempt";
import { annotationFailure } from "../lib/market/annotation-failure";

test("Django dispatch starts multiple plans concurrently within a total of 50 slots", async () => {
  let active = 0;
  let peak = 0;
  const starts: string[] = [];
  const releases: string[] = [];
  const result = await dispatchAnnotationJobs({
    jobs: ["a", "b", "c"].map((jobId) => ({ jobId, ownerEmail: "owner@example.test", availableSlots: 50 })),
    prepare: async ({ jobId }) => jobId,
    run: async ({ jobId }) => {
      starts.push(jobId);
      peak = Math.max(peak, ++active);
      await new Promise((resolve) => setTimeout(resolve, 1));
      active -= 1;
      return { processedCount: 1, done: true };
    },
    release: async ({ jobId }) => { releases.push(jobId); },
  });
  assert.equal(peak, 50);
  assert.deepEqual(["a", "b", "c"].map((id) => starts.filter((value) => value === id).length), [17, 17, 16]);
  assert.deepEqual(releases, ["a", "b", "c"]);
  assert.equal(result.processedCount, 50);
});

test("dispatch skips paused/unauthorized preparation and releases others after transport failure", async () => {
  const calls: string[] = [];
  const result = await dispatchAnnotationJobs({
    jobs: ["revoked", "failed", "ok"].map((jobId) => ({ jobId, ownerEmail: "owner@example.test", availableSlots: 1 })),
    prepare: async ({ jobId }) => jobId === "revoked" ? null : jobId,
    run: async ({ jobId }) => {
      calls.push(jobId);
      if (jobId === "failed") throw new Error("completion receipt unavailable");
      return { processedCount: 1, done: true };
    },
    release: async ({ jobId }) => { calls.push(`release:${jobId}`); },
  });
  assert.deepEqual(calls, ["failed", "ok", "release:failed", "release:ok"]);
  assert.equal(result.dispatchErrors, 1);
});

test("dispatch does not spin on full leases, model failures or an expired launch window", async () => {
  let clock = 0;
  let launches = 0;
  const result = await dispatchAnnotationJobs({
    jobs: [{ jobId: "a", ownerEmail: "owner@example.test", availableSlots: 1 }],
    prepare: async () => true,
    run: async () => { launches += 1; clock = 10_001; return { processedCount: 1 }; },
    release: async () => {}, now: () => clock,
  });
  assert.equal(launches, 1);
  assert.equal(result.processedCount, 1);
  for (const step of [{ waiting: true }, { failureCode: "provider_rate_limit", failedCount: 1 }, {}]) {
    launches = 0;
    await dispatchAnnotationJobs({
      jobs: [{ jobId: "a", ownerEmail: "owner@example.test", availableSlots: 1 }],
      prepare: async () => true,
      run: async () => { launches += 1; return step; }, release: async () => {},
    });
    assert.equal(launches, 1);
  }
});

test("dispatch has a hard operation budget even with immediately resolved providers", async () => {
  let count = 0;
  await dispatchAnnotationJobs({
    jobs: [{ jobId: "a", ownerEmail: "owner@example.test", availableSlots: 10 }],
    prepare: async () => true, now: () => 0,
    run: async () => { count += 1; return { processedCount: 1 }; }, release: async () => {},
  });
  assert.equal(count, 200);
});

test("slow bounded preparation leaves a full launch window and releases its lease", async () => {
  let clock = 0;
  let launched = 0;
  let released = 0;
  const result = await dispatchAnnotationJobs({
    jobs: [{ jobId: "a", ownerEmail: "owner@example.test", availableSlots: 1 }],
    prepare: async () => { clock = 10_001; return true; }, now: () => clock,
    run: async () => { launched += 1; clock += 10_001; return { processedCount: 1 }; },
    release: async () => { released += 1; },
  });
  assert.equal(launched, 1);
  assert.equal(released, 1);
  assert.equal(result.idle, false);
  assert.equal(result.preparationMs, 10_001);
  assert.equal(result.launchCount, 1);
  assert.equal(result.jobCount, 1);
  assert.equal(result.dispatchErrors, 0);
});

test("a slower plan preparation does not starve either plan of its fair first wave", async () => {
  let clock = 0;
  let finishPreparation!: (handle: string) => void;
  const slowPreparation = new Promise<string>((resolve) => { finishPreparation = resolve; });
  let active = 0;
  let peak = 0;
  const starts: string[] = [];
  const releases: string[] = [];
  const dispatch = dispatchAnnotationJobs({
    jobs: ["fast", "slow"].map((jobId) => ({ jobId, ownerEmail: "owner@example.test", availableSlots: 50 })),
    prepare: async ({ jobId }) => jobId === "slow" ? slowPreparation : jobId,
    run: async ({ jobId }) => {
      starts.push(jobId);
      peak = Math.max(peak, ++active);
      await Promise.resolve();
      active -= 1;
      return { processedCount: 1, done: true };
    },
    release: async ({ jobId }) => { releases.push(jobId); },
    now: () => clock,
  });
  await Promise.resolve();
  assert.equal(starts.length, 0);
  clock = 13_000;
  finishPreparation("slow");
  const result = await dispatch;
  assert.equal(peak, 50);
  assert.deepEqual(["fast", "slow"].map((id) => starts.filter((value) => value === id).length), [25, 25]);
  assert.deepEqual(releases, ["fast", "slow"]);
  assert.equal(result.preparationMs, 13_000);
  assert.equal(result.launchCount, 50);
  assert.equal(result.processedCount, 50);
  assert.equal(result.jobCount, 2);
  assert.equal(result.dispatchErrors, 0);
});

test("a database acknowledgement failure never becomes a second model/failure completion", async () => {
  let inferred = 0;
  let failed = 0;
  await assert.rejects(executeAnnotationAttempt({
    infer: async () => { inferred += 1; return { segment: "A" }; },
    complete: async () => { throw new Error("lost completion response"); },
    fail: async () => { failed += 1; return {}; },
  }), /lost completion/);
  assert.equal(inferred, 1);
  assert.equal(failed, 0);
});

test("model exceptions retain retry class and bounded diagnostics", async () => {
  const revoked = annotationFailure(Object.assign(new Error("账号或数据权限已变化"), { status: 403 }));
  assert.equal(revoked.failureCode, "authorization_revoked");
  assert.equal(annotationFailure(Object.assign(new Error("视觉模型调用失败（状态码 403：接口密钥被拒绝）"), { status: 403 })).failureCode, "model_configuration");
  const rateLimit = annotationFailure(Object.assign(new Error("模型供应商限流"), { status: 429, retryAfterMs: 1_000_000 }));
  assert.equal(rateLimit.failureKind, "rate_limit");
  assert.equal(rateLimit.retryAfterMs, 300_000);
  assert.equal(annotationFailure(new Error("模型调用超时")).failureKind, "transient");
  assert.equal(annotationFailure(new Error("模型接口网络错误")).failureCode, "model_network");
  assert.doesNotMatch(annotationFailure(new Error("api_key=sk-example1234567890")).failureMessage, /example123/);
  let failed = 0;
  await executeAnnotationAttempt({ infer: async () => { throw new Error("timeout"); }, complete: async () => 0, fail: async () => ++failed });
  assert.equal(failed, 1);
});
