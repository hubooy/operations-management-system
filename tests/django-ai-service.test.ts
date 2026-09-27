import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";
import { aiHeaders, aiConsumer, isPublicAiPath, requestDjangoAi } from "../lib/django/ai-service";
import { PublicApiError } from "../lib/http/api-error";

const principal = { email: "owner@example.invalid", displayName: "镜像操作员", role: "analyst" as const, scope: { warehouses: ["A"], channels: [], platforms: ["京东"] } };
const environment = { TERUISI_DJANGO_INTERNAL_SECRET: "Isolated-hmac-transport-secret-0123456789", TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18111", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18112" };
const json = (payload: unknown, status = 200, headers = {}) => Response.json(payload, { status, headers: { "x-ai-revision": "42", ...headers } });

test("business file routes keep reads and writes on their owning process", async () => {
  const requests: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => { requests.push(new Request(input, init)); return json({}); };
  for (const path of ["/api/ai/reports/report_1/files", "/api/ai/business-files/file_1", "/api/ai/business-files/file_1/chunks/xlsx"]) {
    assert.equal(isPublicAiPath(path), true);
    await requestDjangoAi(principal, { path }, { environment, fetchImpl });
    assert.equal(new URL(requests.at(-1)!.url).port, "18111");
  }
  for (const path of ["/api/ai/reports/report_1/files", "/api/ai/business-files/file_1/control"]) {
    await requestDjangoAi(principal, { path, method: "POST", payload: {} }, { environment, fetchImpl });
    assert.equal(new URL(requests.at(-1)!.url).port, "18112");
  }
  for (const path of ["/api/ai/business-files", "/api/ai/business-files/file_1/chunks/csv", "/api/ai/business-files/file_1/internal"]) assert.equal(isPublicAiPath(path), false);
});

test("volume chunk routes keep bounded coordinates and read-only forwarding", async () => {
  const calls: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => { calls.push(new Request(input, init)); return json({}); };
  for (const suffix of ["0/chunks/json", "1/chunks/html", "100/chunks/xlsx"]) {
    const path = `/api/ai/business-files/file_1/volumes/${suffix}`;
    assert.equal(isPublicAiPath(path), true);
    await requestDjangoAi(principal, { path }, { environment, fetchImpl });
    assert.equal(new URL(calls.at(-1)!.url).port, "18111");
  }
  for (const suffix of ["01/chunks/html", "101/chunks/xlsx", "-1/chunks/json", "1/chunks/csv", "1/chunks/html/extra"]) {
    assert.equal(isPublicAiPath(`/api/ai/business-files/file_1/volumes/${suffix}`), false);
  }
  const route = await readFile("app/api/ai/business-files/[id]/volumes/[volumeIndex]/chunks/[format]/route.ts", "utf8");
  assert.ok(route.includes("export const GET = forwardAiRequest"));
  assert.doesNotMatch(route, /export const (?:POST|PATCH|PUT|DELETE)/);
});

test("business planning preview is an explicit reader POST and evidence list is read only", async () => {
  const requests: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => { requests.push(new Request(input, init)); return json({}); };
  assert.equal(isPublicAiPath('/api/ai/business-plan/preview'), true);
  assert.equal(isPublicAiPath('/api/ai/business-plan/execute'), false);
  await requestDjangoAi(principal, { path:'/api/ai/business-plan/preview', method:'POST', payload:{}, service:'reader' }, {environment,fetchImpl});
  assert.equal(new URL(requests.at(-1)!.url).port,'18111');
  await requestDjangoAi(principal, {path:'/api/ai/business-evidence'}, {environment,fetchImpl});
  assert.equal(new URL(requests.at(-1)!.url).port,'18111');
  const route=await readFile('lib/ai/django-route.ts','utf8');
  assert.ok(route.includes('url.pathname === "/api/ai/business-plan/preview"'));
  const endpoint=await readFile('app/api/ai/business-plan/preview/route.ts','utf8');
  assert.ok(endpoint.includes('export const POST = forwardAiRequest'));
});

test("AI principal envelope binds exact Unicode identity, method, path, query, body and request ID", async () => {
  const input = { secret: environment.TERUISI_DJANGO_INTERNAL_SECRET, principal, method: "POST", path: "/api/ai/consumer", query: "page=1", body: '{"query":"大毛利"}', requestId: "idempotent-1", timestamp: 1800000000 };
  const headers = await aiHeaders(input);
  const encoded = headers.get("x-teruisi-principal")!;
  assert.deepEqual(JSON.parse(Buffer.from(encoded, "base64url").toString()), principal);
  const digest = createHash("sha256").update(input.body).digest("hex");
  assert.equal(headers.get("x-teruisi-content-sha256"), digest);
  const canonical = ["v1", String(input.timestamp), input.requestId, input.method, input.path, input.query, digest, encoded].join("\n");
  assert.equal(headers.get("x-teruisi-signature"), "v1=" + createHmac("sha256", input.secret).update(canonical).digest("hex"));
  for (const change of [{ query: "page=2" }, { body: "{}" }, { path: "/api/ai/models" }, { requestId: "idempotent-2" }]) {
    assert.notEqual((await aiHeaders({ ...input, ...change })).get("x-teruisi-signature"), headers.get("x-teruisi-signature"));
  }
});

test("business source catalog routes stay bounded and use the owning reader", async () => {
  const calls: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => { calls.push(new Request(input, init)); return json({}); };
  for (const path of ["/api/ai/business-evidence/run_1/sources", "/api/ai/business-evidence/run_1/sources/source_1", "/api/ai/business-evidence/run_1/sources/analysis"]) {
    assert.equal(isPublicAiPath(path), true);
    await requestDjangoAi(principal, { path }, { environment, fetchImpl });
    assert.equal(new URL(calls.at(-1)!.url).port, "18111");
  }
  for (const path of ["/api/ai/business-evidence/run_1/sources/", "/api/ai/business-evidence/run_1/sources/source_1/extra", "/api/ai/business-evidence/run_1/source", "/api/ai/business-evidence/run_1/sources/" + "x".repeat(161)]) {
    assert.equal(isPublicAiPath(path), false);
  }
  for (const path of ["app/api/ai/business-evidence/[id]/sources/route.ts", "app/api/ai/business-evidence/[id]/sources/[sourceKey]/route.ts"]) {
    const route = await readFile(path, "utf8");
    assert.ok(route.includes("export const GET = forwardAiRequest"));
    assert.doesNotMatch(route, /export const (?:POST|PATCH|DELETE|PUT)/);
  }
});

test("AI thin transport selects independent reader/writer and preserves revision and replay", async () => {
  const calls: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => { calls.push(new Request(input, init)); return json({ ok: true }, 201, { "x-teruisi-write-replay": "1" }); };
  const result = await requestDjangoAi(principal, { path: "/api/ai/conversations" }, { environment, fetchImpl });
  assert.equal(new URL(calls[0].url).port, "18111");
  assert.equal(result.revision, "42"); assert.equal(result.replayed, true);
  await requestDjangoAi(principal, { path: "/api/ai/artifacts/ai-artifact-fixture" }, { environment, fetchImpl });
  assert.equal(new URL(calls[1].url).port, "18112");
  await aiConsumer(principal, { operation: "model-runtime", id: "model" }, { environment, fetchImpl });
  assert.equal(new URL(calls[2].url).port, "18112");
  assert.equal(calls[2].headers.has("x-teruisi-signature"), true);
  await aiConsumer(principal, { operation: "tool-audit", entry: {} }, { environment, fetchImpl });
  assert.equal(new URL(calls[3].url).port, "18112");
  assert.equal(calls[3].headers.has("x-teruisi-signature"), true);
});

test("admin model settings GET is an exact signed writer read", async () => {
  const route = await readFile("lib/ai/django-route.ts", "utf8");
  assert.match(route, /url\.pathname === "\/api\/ai\/models" && request\.method === "GET"/);
  assert.match(route, /\? \{ service: "writer" as const \}/);
  const calls: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    calls.push(new Request(input, init));
    return json({ items: [{ id: "model-1", apiKeySuffix: "test" }] });
  };
  const result = await requestDjangoAi(principal, {
    path: "/api/ai/models", method: "GET", service: "writer",
  }, { environment, fetchImpl });
  assert.equal(new URL(calls[0].url).port, "18112");
  assert.equal(calls[0].headers.has("x-teruisi-signature"), true);
  assert.deepEqual(result.data, { items: [{ id: "model-1", apiKeySuffix: "test" }] });
  const listed = await aiConsumer(principal, { operation: "model-list", modelType: "text" }, { environment, fetchImpl });
  assert.equal(new URL(calls[1].url).port, "18112");
  assert.equal(calls[1].headers.has("x-teruisi-signature"), true);
  assert.deepEqual(listed, result.data);
});

test("only exact report-detail GET uses signed writer read without a reader override", async () => {
  const calls: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    calls.push(new Request(input, init));
    return json({ item: { id: "report_1" }, contentError: "未达到完成条件" });
  };
  await requestDjangoAi(principal, { path: "/api/ai/reports/report_1" },
    { environment, fetchImpl });
  assert.equal(new URL(calls[0].url).port, "18112");
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].headers.has("x-teruisi-signature"), true);
  for (const path of ["/api/ai/reports", "/api/ai/reports/report_1/files",
    "/api/ai/reports/report_1/budget",
    "/api/ai/reports/report_1/promotion-keyword-sku"]) {
    await requestDjangoAi(principal, { path }, { environment, fetchImpl });
    assert.equal(new URL(calls.at(-1)!.url).port, "18111", path);
  }
  await requestDjangoAi(principal, { path: "/api/ai/reports/report_1",
    method: "POST", payload: {} }, { environment, fetchImpl });
  assert.equal(new URL(calls.at(-1)!.url).port, "18112");
  const before = calls.length;
  await assert.rejects(requestDjangoAi(principal,
    { path: "/api/ai/reports/report_1", service: "reader" },
    { environment, fetchImpl }), PublicApiError);
  assert.equal(calls.length, before);
  await assert.rejects(requestDjangoAi(principal,
    { path: "/api/ai/reports/report_1/extra" },
    { environment, fetchImpl }), PublicApiError);
  assert.equal(calls.length, before);
});

test("AI transport rejects invalid configuration, oversized payloads, redirects, malformed JSON and missing revisions", async () => {
  let calls = 0;
  const fetchImpl: typeof fetch = async () => { calls++; return json({ ok: true }); };
  for (const config of [{ ...environment, TERUISI_DJANGO_AI_READER_BASE_URL: "http://169.254.169.254" }, { ...environment, TERUISI_DJANGO_AI_WRITER_BASE_URL: environment.TERUISI_DJANGO_AI_READER_BASE_URL }, { ...environment, TERUISI_DJANGO_INTERNAL_SECRET: "short" }]) {
    await assert.rejects(requestDjangoAi(principal, { path: "/api/ai/models" }, { environment: config, fetchImpl }), PublicApiError);
  }
  await assert.rejects(requestDjangoAi(principal, { path: "/api/ai/consumer", method: "POST", payload: { value: "a".repeat(1024 * 1024) } }, { environment, fetchImpl }), PublicApiError);
  assert.equal(calls, 0);
  for (const response of [new Response("{}", { status: 302, headers: { location: "https://example.invalid" } }), new Response("not-json"), Response.json({}), json([], 200), json({ error: "unsafe", code: "arbitrary" }, 500)]) {
    await assert.rejects(requestDjangoAi(principal, { path: "/api/ai/models" }, { environment, fetchImpl: async () => response }), (error: unknown) => error instanceof PublicApiError && error.status === 503 && !error.message.includes("unsafe"));
  }
});

test("AI production entry points contain no retired D1 domain implementation or schema bootstrap", async () => {
  const walk = async (url: URL): Promise<URL[]> => (await Promise.all((await readdir(url, { withFileTypes: true })).map(entry => entry.isDirectory() ? walk(new URL(entry.name + "/", url)) : [new URL(entry.name, url)]))).flat();
  for (const file of [...await walk(new URL("../lib/ai/", import.meta.url)), ...await walk(new URL("../app/api/ai/", import.meta.url))]) {
    const content = await readFile(file, "utf8");
    assert.doesNotMatch(content, /(?:FROM|INTO|UPDATE|TABLE)\s+[`"]?ai_(?:models|conversations|memory|agent|workflow|space|tool_audit)|ensureAi(?:Assistant|Space|Agent|Workflow|Memory|Artifact)Schema|tests\/legacy/i, file.pathname);
  }
  assert.equal(isPublicAiPath("/api/ai/internal/edge"), false);
  assert.equal(isPublicAiPath("/api/ai/consumer"), false);
  assert.equal(isPublicAiPath("/api/ai/space/assets/one/content"), true);
  assert.equal(isPublicAiPath("/api/ai/dingtalk-schedules"), true);
  assert.equal(isPublicAiPath("/api/ai/dingtalk-schedules/run"), true);
  assert.equal(isPublicAiPath("/api/ai/dingtalk-schedules/arbitrary"), false);
});

test("DingTalk schedules use the admin-gated thin route and disjoint reader/writer", async () => {
  const edge = await readFile(new URL("../lib/ai/django-route.ts", import.meta.url), "utf8");
  assert.ok(edge.includes("dingtalk-schedules(?:\\/run)?"));
  const requests: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => { requests.push(new Request(input, init)); return json({ items: [] }); };
  await requestDjangoAi(principal, { path: "/api/ai/dingtalk-schedules" }, { environment, fetchImpl });
  await requestDjangoAi(principal, { path: "/api/ai/dingtalk-schedules/run", method: "POST", payload: { id: "fixture", expectedVersion: 1 } }, { environment, fetchImpl });
  assert.equal(new URL(requests[0].url).port, "18111");
  assert.equal(new URL(requests[1].url).port, "18112");
});


test("AI production image path has no R2 binding or storage bridge", async () => {
  const edge = await readFile(new URL("../lib/ai/django-edge.ts", import.meta.url), "utf8");
  assert.doesNotMatch(edge, /R2Bucket|SALES_IMPORT_FILES|cloudflare:workers|storage_(?:get|put|delete)/);
  const space = await readFile(new URL("../backend/ai_assistant/space.py", import.meta.url), "utf8");
  assert.doesNotMatch(space, /transport\.edge\(\s*["']storage_/);
  assert.match(space, /AiSpaceAssetPayload\.objects\.create/);
});


test("fixed budget reference is an exact reader route with GET-only Next export", async () => {
  const calls: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => { calls.push(new Request(input, init)); return json({}); };
  const path = "/api/ai/reports/report_1/budget-reference";
  assert.equal(isPublicAiPath(path), true);
  await requestDjangoAi(principal, { path, query: new URLSearchParams({ runId: "run_1", offset: "0", limit: "20" }) }, { environment, fetchImpl });
  assert.equal(new URL(calls[0].url).port, "18111");
  assert.equal(calls[0].method, "GET");
  for (const suffix of ["/", "/extra", "-preview"]) assert.equal(isPublicAiPath(path+suffix), false);
  const source = await readFile("app/api/ai/reports/[reportId]/budget-reference/route.ts", "utf8");
  assert.match(source, /export const GET = forwardAiRequest/);
  assert.doesNotMatch(source, /export const (POST|PUT|PATCH|DELETE)/);
});
