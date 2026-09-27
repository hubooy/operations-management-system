import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoWorkflowService,
  WORKFLOW_ATTACHMENT_CLEANUP_PATH,
} from "@/lib/django/workflow-service";
import { deleteWorkflowAttachmentObject } from "@/lib/workflow/attachment-storage";

function summary(error: unknown) {
  const value = error instanceof Error && error.name ? error.name : "storage_cleanup_failed";
  return Array.from(value).slice(0, 120).join("") || "storage_cleanup_failed";
}

async function report(
  principal: AppPrincipal,
  payload: Record<string, unknown>,
  options: { signal?: AbortSignal } = {},
) {
  await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
    principal,
    { method: "POST", path: WORKFLOW_ATTACHMENT_CLEANUP_PATH, service: "writer", payload },
    options,
  );
}

export async function enqueueWorkflowAttachmentCleanup(
  principal: AppPrincipal,
  objectKey: string,
  options: { signal?: AbortSignal } = {},
) {
  await report(principal, { objectKey, enqueue: true }, options);
}

export async function drainWorkflowAttachmentCleanup(
  principal: AppPrincipal,
  objectKeys?: readonly string[],
  options: { signal?: AbortSignal } = {},
) {
  let keys = objectKeys ? [...new Set(objectKeys)].slice(0, 100) : [];
  if (!objectKeys) {
    const queued = await createDjangoWorkflowService().requestJson<{ items: Array<{ objectKey: string }> }>(
      principal,
      { method: "GET", path: WORKFLOW_ATTACHMENT_CLEANUP_PATH, service: "writer", rawQuery: "limit=50" },
      options,
    );
    keys = queued.data.items.map((item) => item.objectKey);
  }
  let deleted = 0; let failed = 0;
  for (const objectKey of keys) {
    try {
      await deleteWorkflowAttachmentObject(objectKey);
      await report(principal, { objectKey, deleted: true }, options);
      deleted += 1;
    } catch (error) {
      failed += 1;
      await report(principal, { objectKey, deleted: false, error: summary(error) }, options).catch(() => undefined);
    }
  }
  return { attempted: keys.length, deleted, failed };
}
