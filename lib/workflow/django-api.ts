import type { DjangoWorkflowServiceResult } from "@/lib/django/workflow-service";
import { PublicApiError } from "@/lib/http/api-error";

export function requireWorkflowJsonObject(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PublicApiError(400, "invalid_request", message);
  }
  return value as Record<string, unknown>;
}

export function workflowServiceResponse<T extends Record<string, unknown>>(
  result: DjangoWorkflowServiceResult<T>,
  data: Record<string, unknown> = result.data,
) {
  const headers = new Headers({
    "cache-control": "no-store",
    "x-workflow-data-revision": result.revision,
  });
  if (result.replayed) headers.set("x-teruisi-write-replay", "1");
  return Response.json(data, { status: result.status, headers });
}

export function encodedWorkflowResource(value: string) {
  return encodeURIComponent(value);
}
