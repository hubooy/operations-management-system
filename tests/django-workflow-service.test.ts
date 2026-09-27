import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  DjangoWorkflowServiceResponseError,
  requestDjangoWorkflowJson,
  workflowBackendModeFromEnvironment,
  WORKFLOW_ATTACHMENT_CLEANUP_PATH,
  WORKFLOW_CONSUMER_QUERY_PATH,
  WORKFLOW_INVENTORY_WORK_ITEMS_PATH,
  WORKFLOW_LAUNCH_PROJECTS_PATH,
  WORKFLOW_OPERATION_RECORDS_PATH,
  WORKFLOW_TASKS_PATH,
  WORKFLOW_IMPORT_CHAIN_STATUS_PATH,
  WORKFLOW_TEMPLATES_PATH,
} from "../lib/django/workflow-service";
import { readDjangoWorkflowConsumer } from "../lib/django/workflow-consumer-reader";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = {
  email: "admin@example.test",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const secret = "workflow-service-contract-secret-at-least-32-bytes";
const config = {
  readerBaseUrl: "http://127.0.0.1:8061",
  writerBaseUrl: "http://127.0.0.1:8062",
  internalSecret: secret,
  timeoutMs: 2_000,
  maxRequestBytes: 1024 * 1024,
  maxResponseBytes: 1024 * 1024,
};
const projectId = "7e28149d-f0bd-4fb8-b87f-77e507b28130";
const projectPath = `${WORKFLOW_LAUNCH_PROJECTS_PATH}/${projectId}`;
const stagePath = `${projectPath}/stages/pricing`;

function verifySignature(request: Request, path: string, query = "") {
  const canonical = [
    "v1",
    request.headers.get("x-teruisi-timestamp")!,
    request.headers.get("x-teruisi-request-id")!,
    request.method,
    path,
    query,
    request.headers.get("x-teruisi-content-sha256")!,
    request.headers.get("x-teruisi-principal")!,
  ].join("\n");
  assert.equal(
    request.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
}

test("workflow backend mode is terminally Django and fails closed without exact config", () => {
  assert.throws(
    () => workflowBackendModeFromEnvironment({}),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  assert.equal(workflowBackendModeFromEnvironment({ TERUISI_DJANGO_WORKFLOW_MODE: "django" }), "django");
  assert.throws(
    () => workflowBackendModeFromEnvironment({ TERUISI_DJANGO_WORKFLOW_MODE: "legacy" }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  assert.throws(
    () => workflowBackendModeFromEnvironment({ TERUISI_DJANGO_WORKFLOW_MODE: "shadow" }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});

test("workflow reader signs exact filters and requires a bounded revision", async () => {
  let observed: Request | undefined;
  const rawQuery = "status=blocked&supplier=%E4%BE%9B%E5%BA%94%E5%95%86%E7%94%B2&page=1&pageSize=50";
  const result = await requestDjangoWorkflowJson<Record<string, unknown>>(
    principal,
    { method: "GET", path: WORKFLOW_LAUNCH_PROJECTS_PATH, rawQuery, service: "reader" },
    {
      config,
      now: () => 1_800_000_000_000,
      requestId: () => "workflow-reader-1",
      fetchImpl: async (input, init) => {
        observed = new Request(input, init);
        return Response.json(
          { items: [], summary: {} },
          { headers: { "x-workflow-data-revision": "4:abcdef123456" } },
        );
      },
    },
  );
  assert.ok(observed);
  assert.equal(new URL(observed.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed.url).search.slice(1), rawQuery);
  verifySignature(observed, WORKFLOW_LAUNCH_PROJECTS_PATH, rawQuery);
  assert.equal(result.revision, "4:abcdef123456");
});

test("workflow mutations stay on the writer and keep path-specific signatures", async () => {
  const observed: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    observed.push(new Request(input, init));
    return Response.json(
      { ok: true },
      { headers: { "x-workflow-data-revision": "8:abcdef123456", "x-teruisi-write-replay": "1" } },
    );
  };
  const created = await requestDjangoWorkflowJson(
    principal,
    { method: "POST", path: WORKFLOW_LAUNCH_PROJECTS_PATH, service: "writer", payload: { productName: "新品" } },
    { config, fetchImpl, requestId: () => "workflow-create" },
  );
  await requestDjangoWorkflowJson(
    principal,
    { method: "PATCH", path: projectPath, service: "writer", payload: { expectedVersion: 1, owner: "商品组" } },
    { config, fetchImpl, requestId: () => "workflow-project-update" },
  );
  await requestDjangoWorkflowJson(
    principal,
    { method: "PATCH", path: stagePath, service: "writer", payload: { expectedVersion: 1, status: "completed" } },
    { config, fetchImpl, requestId: () => "workflow-stage-update" },
  );
  await requestDjangoWorkflowJson(
    principal,
    { method: "DELETE", path: projectPath, service: "writer", rawQuery: "expectedVersion=2" },
    { config, fetchImpl, requestId: () => "workflow-delete" },
  );
  assert.equal(observed.length, 4);
  assert.ok(observed.every((request) => new URL(request.url).origin === config.writerBaseUrl));
  verifySignature(observed[0]!, WORKFLOW_LAUNCH_PROJECTS_PATH);
  verifySignature(observed[2]!, stagePath);
  verifySignature(observed[3]!, projectPath, "expectedVersion=2");
  assert.equal(created.replayed, true);
});

test("full workflow paths use strict reader and writer method allowlists", async () => {
  const taskId = "task-1";
  const attachmentId = "attachment-1";
  const recordId = "record-1";
  const observed: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    observed.push(new Request(input, init));
    return Response.json({}, { headers: { "x-workflow-data-revision": "10:abcdef123456" } });
  };
  const requests = [
    { method: "GET", path: WORKFLOW_IMPORT_CHAIN_STATUS_PATH, service: "reader" },
    { method: "GET", path: WORKFLOW_TASKS_PATH, service: "reader" },
    { method: "POST", path: `${WORKFLOW_TASKS_PATH}/${taskId}/comments`, service: "writer", payload: { content: "复核" } },
    { method: "GET", path: `${WORKFLOW_TASKS_PATH}/${taskId}/attachments/${attachmentId}`, service: "reader" },
    { method: "GET", path: `/api/workflow/new-product-lines/${projectId}/image`, service: "reader" },
    { method: "DELETE", path: `${WORKFLOW_TASKS_PATH}/${taskId}/attachments/${attachmentId}`, service: "writer" },
    { method: "GET", path: WORKFLOW_TEMPLATES_PATH, service: "reader" },
    { method: "PATCH", path: `${WORKFLOW_OPERATION_RECORDS_PATH}/${recordId}`, service: "writer", payload: { expectedVersion: 1, status: "处理中" } },
    { method: "GET", path: `${WORKFLOW_OPERATION_RECORDS_PATH}/${recordId}/activity`, service: "reader" },
    { method: "GET", path: WORKFLOW_ATTACHMENT_CLEANUP_PATH, service: "writer" },
    { method: "POST", path: WORKFLOW_INVENTORY_WORK_ITEMS_PATH, service: "writer", payload: { entityId: "sku-1" } },
  ] as const;
  for (const [index, request] of requests.entries()) {
    await requestDjangoWorkflowJson(principal, request, {
      config,
      fetchImpl,
      requestId: () => `workflow-full-path-${index}`,
    });
  }
  assert.equal(observed.length, requests.length);
  assert.equal(
    observed.filter((request) => new URL(request.url).origin === config.readerBaseUrl).length,
    6,
  );
  assert.equal(
    observed.filter((request) => new URL(request.url).origin === config.writerBaseUrl).length,
    5,
  );
  await assert.rejects(
    requestDjangoWorkflowJson(
      principal,
      { method: "GET", path: WORKFLOW_TASKS_PATH, service: "writer" },
      { config, fetchImpl: async () => assert.fail("wrong service must not fetch") },
    ),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});

test("workflow consumer search stays on the reader with a fixed bounded contract", async () => {
  let observed: Request | undefined;
  const result = await readDjangoWorkflowConsumer(
    principal,
    { operation: "launch_project_search", query: "净水器", offset: 0, limit: 8 },
    {
      config,
      requestId: () => "workflow-consumer-1",
      fetchImpl: async (input, init) => {
        observed = new Request(input, init);
        return Response.json({
          operation: "launch_project_search",
          data: {
            items: [{
              id: projectId,
              title: "大通量净水器",
              subtitle: "供应商甲 · 商用净水 · 进行中",
              detail: "SKU-001 · 新品负责人",
              updatedAt: "2026-09-02T10:00:00+08:00",
              amountCents: 399_900,
            }],
            total: 1,
            truncated: false,
          },
        }, { headers: { "x-workflow-data-revision": "9:abcdef123456" } });
      },
    },
  );
  assert.ok(observed);
  assert.equal(new URL(observed.url).origin, config.readerBaseUrl);
  assert.equal(observed.method, "POST");
  verifySignature(observed, WORKFLOW_CONSUMER_QUERY_PATH);
  assert.equal(result.data.items[0]?.title, "大通量净水器");

  await assert.rejects(
    readDjangoWorkflowConsumer(
      principal,
      { operation: "launch_project_search", query: "一", offset: 0, limit: 8 },
      { config, fetchImpl: async () => assert.fail("invalid request must not fetch") },
    ),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});

test("workflow allowlists, loopback config, upstream errors and revision failures fail closed", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("request must fail before fetch");
  for (const input of [
    { method: "GET", path: WORKFLOW_IMPORT_CHAIN_STATUS_PATH, service: "writer" },
    { method: "POST", path: WORKFLOW_IMPORT_CHAIN_STATUS_PATH, service: "reader" },
    { method: "GET", path: WORKFLOW_LAUNCH_PROJECTS_PATH, service: "writer" },
    { method: "POST", path: WORKFLOW_LAUNCH_PROJECTS_PATH, payload: {}, service: "reader" },
    { method: "PATCH", path: `${projectPath}/stages/unknown`, payload: {}, service: "writer" },
    { method: "GET", path: "/api/workflow/unknown", service: "reader" },
  ] as const) {
    await assert.rejects(
      requestDjangoWorkflowJson(principal, input, { config, fetchImpl: neverFetch }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  for (const unsafe of [
    { ...config, writerBaseUrl: config.readerBaseUrl },
    { ...config, readerBaseUrl: "http://example.com" },
    { ...config, internalSecret: "short" },
  ]) {
    await assert.rejects(
      requestDjangoWorkflowJson(
        principal,
        { method: "GET", path: WORKFLOW_LAUNCH_PROJECTS_PATH, service: "reader" },
        { config: unsafe, fetchImpl: neverFetch },
      ),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  await assert.rejects(
    requestDjangoWorkflowJson(
      principal,
      { method: "GET", path: WORKFLOW_LAUNCH_PROJECTS_PATH, service: "reader" },
      {
        config,
        fetchImpl: async () => Response.json({ error: "筛选无效", code: "invalid_request" }, { status: 400 }),
      },
    ),
    (error: unknown) => error instanceof DjangoWorkflowServiceResponseError
      && error.status === 400
      && error.upstreamCode === "invalid_request",
  );
  await assert.rejects(
    requestDjangoWorkflowJson(
      principal,
      { method: "GET", path: WORKFLOW_LAUNCH_PROJECTS_PATH, service: "reader" },
      { config, fetchImpl: async () => Response.json({ items: [] }) },
    ),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});
