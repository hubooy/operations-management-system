export type WorkflowRequestErrorCode =
  | "invalid_request"
  | "not_found"
  | "version_conflict"
  | "conflict"
  | "payload_too_large"
  | "integrity_error";

export class WorkflowRequestError extends Error {
  readonly status: 400 | 404 | 409 | 413;
  readonly code: WorkflowRequestErrorCode;

  constructor(
    status: WorkflowRequestError["status"],
    code: WorkflowRequestErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "WorkflowRequestError";
    this.status = status;
    this.code = code;
  }
}

export function invalidWorkflowRequest(message: string): never {
  throw new WorkflowRequestError(400, "invalid_request", message);
}

export function missingWorkflowResource(message: string): never {
  throw new WorkflowRequestError(404, "not_found", message);
}

export function workflowVersionConflict(message = "记录已被其他人更新，请刷新后重试"): never {
  throw new WorkflowRequestError(409, "version_conflict", message);
}

export function workflowErrorResponse(error: unknown, fallback: string): Response {
  if (error instanceof WorkflowRequestError) {
    return Response.json(
      { error: error.message, code: error.code },
      { status: error.status, headers: { "cache-control": "no-store" } },
    );
  }
  return Response.json(
    { error: fallback, code: "internal_error" },
    { status: 500, headers: { "cache-control": "no-store" } },
  );
}
