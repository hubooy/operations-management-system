import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";

type RecordValue = Record<string, unknown>;
const owner: AppPrincipal = { email: "owner@example.test", displayName: "Owner", role: "operator", scope: null };
const coordinator: AppPrincipal = { email: "market-annotation-runner@teruisi.internal", displayName: "Scheduler", role: "operator", scope: null };
let mode = "success";
let inferenceCalls = 0;
let completions: RecordValue[] = [];
let completionIds: string[] = [];
const mocks = {
  resolve: async (email: string) => {
    assert.equal(email, owner.email);
    return mode === "revoked" ? { ok: false } : { ok: true, principal: owner };
  },
  infer: async (input: RecordValue) => {
    inferenceCalls += 1;
    assert.deepEqual(input.principal, owner);
    assert.ok(Number(input.deadlineAt) < Date.now() + 150_000);
    return { segment: "A", timing: {} };
  },
  request: async (_principal: AppPrincipal, request: { payload: RecordValue }, options: { requestId?: () => string }) => {
    const command = request.payload.command as RecordValue | undefined;
    if (command?.action === "claim_task") {
      return { status: 200, revision: "1:test", data: { result: { task: {
        jobId: "job", itemId: "item", skuCode: "sku", productName: "synthetic", brand: "test",
        sourceImageUrl: "https://img10.360buyimg.com/synthetic.jpg", promptBody: "classify",
        segments: ["A"], fixedSegment: null, modelId: "model", leaseToken: "lease",
        ownerEmail: owner.email, leaseExpiresAt: new Date(Date.now() + 180_000).toISOString(),
      } } } };
    }
    if (command?.action === "complete_task") {
      completions.push(command);
      completionIds.push(options.requestId!());
      if (mode === "ack-lost" && completions.length === 1 || mode === "ack-unavailable") throw Object.assign(new Error("storage unavailable"), { status: 503 });
      return { status: 200, revision: "2:test", replayed: false, data: { result: { done: true } } };
    }
    throw new Error("unexpected progress request");
  },
};
const globals = globalThis as typeof globalThis & { __marketRunnerMocks?: typeof mocks };
globals.__marketRunnerMocks = mocks;
const mockModule = `
export const MARKET_COMMANDS_PATH = '/commands';
export const MARKET_QUERIES_PATH = '/queries';
export const requestDjangoMarketService = (...args) => globalThis.__marketRunnerMocks.request(...args);
export const runVisionAnnotation = (...args) => globalThis.__marketRunnerMocks.infer(...args);
export const visionAnnotationTiming = () => ({});
export const resolveAiBackgroundPrincipal = (...args) => globalThis.__marketRunnerMocks.resolve(...args);
`;
registerHooks({ resolve(specifier, context, next) {
  if (["@/lib/django/market-service", "@/lib/market/annotation-model", "@/lib/ai/background-principal"].includes(specifier)) {
    return { url: `data:text/javascript,${encodeURIComponent(mockModule)}`, shortCircuit: true };
  }
  return next(specifier, context);
} });
const { runClaimedDjangoMarketVisionTask } = await import("../lib/market/django-annotation-runner");

for (const scenario of ["success", "ack-lost", "ack-unavailable", "revoked"]) {
  test(`actual Django runner: ${scenario}`, async () => {
    mode = scenario;
    inferenceCalls = 0;
    completions = [];
    completionIds = [];
    const call = runClaimedDjangoMarketVisionTask({ principal: coordinator, jobId: "job", coordinatorToken: "dispatch-lease" });
    if (scenario === "ack-unavailable") await assert.rejects(call, /storage unavailable/);
    else {
      const response = await call;
      assert.equal(response.data.result.processedCount, scenario === "revoked" ? 0 : 1);
    }
    assert.equal(inferenceCalls, scenario === "revoked" ? 0 : 1);
    assert.equal(completions.length, scenario.startsWith("ack-") ? 2 : 1);
    if (scenario === "revoked") assert.equal(completions[0].failureCode, "authorization_revoked");
    else assert.ok(completions.every((command) => command.result && !command.error));
    assert.equal(new Set(completionIds).size, 1);
  });
}
