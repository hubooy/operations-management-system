import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoWorkflowService,
  getWorkflowBackendMode,
  WORKFLOW_TASKS_PATH,
} from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { drainWorkflowAttachmentCleanup } from "@/lib/workflow/attachment-cleanup";
import { requireWorkflowJsonObject, workflowServiceResponse } from "@/lib/workflow/django-api";

function routeError(error: unknown, fallback: string) {
  return authorizationErrorResponse(error) ?? safeApiErrorResponse(error, fallback);
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "工作计划");
    await getWorkflowBackendMode();
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: WORKFLOW_TASKS_PATH, service: "reader", rawQuery: new URL(request.url).searchParams.toString() },
      { signal: request.signal },
    );
    return workflowServiceResponse(result);
  } catch (error) {
    return routeError(error, "读取工作计划失败。");
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "工作计划", "修改");
    await getWorkflowBackendMode();
    const payload = requireWorkflowJsonObject(await request.json().catch(() => null), "工作项内容必须是有效的 JSON 对象。");
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal, { method: "POST", path: WORKFLOW_TASKS_PATH, service: "writer", payload }, { signal: request.signal },
    );
    return workflowServiceResponse(result);
  } catch (error) {
    return routeError(error, "保存工作项失败。");
  }
}

export async function PATCH(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "工作计划", "修改");
    await getWorkflowBackendMode();
    const payload = requireWorkflowJsonObject(await request.json().catch(() => null), "缺少可更新的工作项字段。");
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "PATCH", path: WORKFLOW_TASKS_PATH, service: "writer", payload, rawQuery: new URL(request.url).searchParams.toString() },
      { signal: request.signal },
    );
    return workflowServiceResponse(result);
  } catch (error) {
    return routeError(error, "更新工作项失败。");
  }
}

export async function DELETE(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "工作计划", "修改");
    await getWorkflowBackendMode();
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "DELETE", path: WORKFLOW_TASKS_PATH, service: "writer", rawQuery: new URL(request.url).searchParams.toString() },
      { signal: request.signal },
    );
    const cleanupObjectKeys = Array.isArray(result.data.cleanupObjectKeys)
      ? result.data.cleanupObjectKeys.filter((value): value is string => typeof value === "string")
      : [];
    if (cleanupObjectKeys.length) {
      await drainWorkflowAttachmentCleanup(principal, cleanupObjectKeys, { signal: request.signal }).catch(() => undefined);
    }
    const publicData = { ...result.data };
    delete publicData.cleanupObjectKeys;
    return workflowServiceResponse(result, publicData);
  } catch (error) {
    return routeError(error, "删除工作项失败。");
  }
}
