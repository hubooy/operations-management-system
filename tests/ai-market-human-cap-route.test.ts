import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { isHumanCapApprovalPath, isPublicAiPath, requestDjangoAi } from "../lib/django/ai-service";
import { PublicApiError } from "../lib/http/api-error";

const principal = { email: "admin@example.invalid", displayName: "管理员", role: "admin" as const, scope: null };
const environment = {
  TERUISI_DJANGO_INTERNAL_SECRET: "Isolated-hmac-transport-secret-0123456789",
  TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18111",
  TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18112",
};
const base = `/api/ai/market-v2-cap-approvals/${"a".repeat(64)}`;

test("human cap paths are exact and stay on signed AI writer", async () => {
  const calls: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    calls.push(new Request(input, init));
    return Response.json({ providerCallsAllowed: false }, { headers: { "x-ai-revision": "1" } });
  };
  for (const [path, method, payload] of [
    [`${base}/preview`, "GET", undefined],
    [`${base}/outcome`, "GET", undefined],
    [base, "POST", { explicitApproval: true }],
    [`${base}/revoke`, "POST", { approvalId: "b".repeat(64), reason: "测试" }],
  ] as const) {
    assert.equal(isHumanCapApprovalPath(path), true);
    assert.equal(isPublicAiPath(path), true);
    await requestDjangoAi(principal, { path, method, ...(payload ? { payload } : {}) },
      { environment, fetchImpl });
    assert.equal(new URL(calls.at(-1)!.url).port, "18112");
  }
  for (const path of [`${base}/`, `${base}/approve`, `${base}/revoke/extra`,
    `/api/ai/market-v2-cap-approvals/${"g".repeat(64)}`]) {
    assert.equal(isPublicAiPath(path), false);
  }
  for (const path of [`${base}/preview`, `${base}/outcome`]) {
    await assert.rejects(requestDjangoAi(principal, { path, service: "reader" },
      { environment, fetchImpl }), PublicApiError);
  }
  await assert.rejects(requestDjangoAi(principal, { path: `${base}/revoke`,
    method: "POST", service: "reader", payload: {} },
    { environment, fetchImpl }), PublicApiError);
});

test("human cap browser routes expose only exact read or write verbs", async () => {
  for (const [suffix, verb] of [["", "POST"], ["preview/", "GET"],
    ["outcome/", "GET"], ["revoke/", "POST"]] as const) {
    const source = await readFile(
      `app/api/ai/market-v2-cap-approvals/[ledgerId]/${suffix}route.ts`, "utf8");
    assert.match(source, new RegExp(`export const ${verb} = forwardAiRequest`));
    assert.doesNotMatch(source, /export const (?:PATCH|PUT|DELETE)\b/);
  }
  const edge = await readFile("lib/ai/django-route.ts", "utf8");
  assert.match(edge, /AI_MARKET_V2_HUMAN_CAP_ENABLED/);
  assert.match(edge, /market-v2-cap-approvals/);
});
