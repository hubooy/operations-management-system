import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoWorkflowService,
  WORKFLOW_NEW_PRODUCT_LINE_LEARN_PATH,
} from "@/lib/django/workflow-service";

export type NewProductLearningReceipt = {
  status: "completed" | "deferred";
  added?: number;
  ambiguous?: number;
  scanned?: number;
  message?: string;
};

/**
 * ERP product-master publication stays authoritative even if the downstream
 * PostgreSQL projection or workflow service is briefly unavailable. A later
 * page load and the weekly sender reconcile the complete catalog again.
 */
export async function reconcileNewProductCodesAfterImport(
  principal: AppPrincipal,
  sourceBatchId: string,
  signal?: AbortSignal,
): Promise<NewProductLearningReceipt> {
  if (!sourceBatchId.trim()) {
    return { status: "deferred", message: "吉客云货品主数据批次标识缺失；新品代码将在后续对账时再次学习。" };
  }
  try {
    const response = await createDjangoWorkflowService().requestJson<{
      result?: { added?: unknown[]; ambiguous?: unknown[]; scanned?: number; deferred?: boolean };
    }>(principal, {
      method: "POST",
      path: WORKFLOW_NEW_PRODUCT_LINE_LEARN_PATH,
      service: "writer",
      payload: { expectedSourceBatchId: sourceBatchId },
    }, { signal });
    if (response.data.result?.deferred) {
      return {
        status: "deferred",
        scanned: Number(response.data.result.scanned ?? 0),
        message: "吉客云货品主数据已导入；新品代码将在 PostgreSQL 投影同步后再次学习。",
      };
    }
    return {
      status: "completed",
      added: response.data.result?.added?.length ?? 0,
      ambiguous: response.data.result?.ambiguous?.length ?? 0,
      scanned: Number(response.data.result?.scanned ?? 0),
    };
  } catch {
    return {
      status: "deferred",
      message: "吉客云货品主数据已导入；新品代码将在投影同步后由页面或周报任务再次学习。",
    };
  }
}
