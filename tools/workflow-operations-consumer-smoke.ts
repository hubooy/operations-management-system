import {
  createDjangoWorkflowService,
  WORKFLOW_OPERATION_RECORDS_PATH,
  WORKFLOW_TASKS_PATH,
  WORKFLOW_TEMPLATES_PATH,
} from "@/lib/django/workflow-service";
import { readDjangoWorkflowConsumer } from "@/lib/django/workflow-consumer-reader";

const query = (process.env.TERUISI_WORKFLOW_SMOKE_QUERY ?? "").trim();
if (query.length < 2 || query.length > 80) {
  throw new Error("TERUISI_WORKFLOW_SMOKE_QUERY 必须是 2–80 个字符");
}

const principal = {
  email: "local-admin@teruisi.local",
  displayName: "本地管理员",
  role: "admin" as const,
  scope: null,
};
const scopedPrincipal = {
  ...principal,
  scope: { warehouses: [] as string[], channels: [] as string[], platforms: ["__workflow_smoke_no_match__"] },
};
const service = createDjangoWorkflowService();
const taskQuery = new URLSearchParams({ q: query, page: "1", pageSize: "5" }).toString();
const scopedOperationQuery = new URLSearchParams({ page: "1", pageSize: "5" }).toString();

const [search, tasksResponse, templatesResponse, scopedOperationsResponse] = await Promise.all([
  readDjangoWorkflowConsumer(principal, {
    operation: "workflow_search",
    query,
    offset: 0,
    limit: 5,
  }),
  service.requestJson<Record<string, unknown>>(principal, {
    method: "GET", path: WORKFLOW_TASKS_PATH, service: "reader", rawQuery: taskQuery,
  }),
  service.requestJson<Record<string, unknown>>(principal, {
    method: "GET", path: WORKFLOW_TEMPLATES_PATH, service: "reader", rawQuery: "",
  }),
  service.requestJson<Record<string, unknown>>(scopedPrincipal, {
    method: "GET", path: WORKFLOW_OPERATION_RECORDS_PATH, service: "reader", rawQuery: scopedOperationQuery,
  }),
]);

const tasks = Array.isArray(tasksResponse.data.items) ? tasksResponse.data.items : [];
const templates = Array.isArray(templatesResponse.data.items) ? templatesResponse.data.items : [];
const scopedOperations = Array.isArray(scopedOperationsResponse.data.items) ? scopedOperationsResponse.data.items : [];
const scopedFilters = scopedOperationsResponse.data.filtersApplied;
const scopedOperationMode = scopedFilters && typeof scopedFilters === "object" && !Array.isArray(scopedFilters)
  ? (scopedFilters as Record<string, unknown>).dataScope
  : null;

const result = {
  status: "passed",
  workflowRevision: search.revision,
  searchReturned: search.data.items.length,
  searchTaskReturned: search.data.items.filter((item) => item.targetHint === "task").length,
  aiTaskReturned: tasks.length,
  aiTemplateReturned: templates.length,
  scopedOperationReturned: scopedOperations.length,
  scopedOperationMode,
};

if (
  !/^\d+:[0-9a-f]{12}$/.test(search.revision)
  || result.searchTaskReturned < 1
  || result.aiTaskReturned < 1
  || result.scopedOperationReturned !== 0
  || result.scopedOperationMode !== "restricted"
) {
  throw new Error(`运营事务消费链路 smoke 未满足正式契约: ${JSON.stringify(result)}`);
}

process.stdout.write(`${JSON.stringify(result)}\n`);
