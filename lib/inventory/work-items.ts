import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoInventoryConsumerReader,
  type InventoryConsumerReader,
} from "@/lib/django/inventory-consumer-reader";
import {
  createDjangoWorkflowService,
  WORKFLOW_INVENTORY_WORK_ITEMS_PATH,
} from "@/lib/django/workflow-service";

export type InventoryWorkItemInput = {
  kind?: unknown;
  label?: unknown;
  planId?: unknown;
  inventoryKey?: unknown;
  owner?: unknown;
  dueDate?: unknown;
  expectedArrivalDate?: unknown;
  planType?: unknown;
  cleanupStrategy?: unknown;
  expectedConsumptionDays?: unknown;
  notes?: unknown;
};

export class InventoryWorkItemError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "InventoryWorkItemError";
    this.status = status;
    this.code = code;
  }
}

function text(value: unknown, label: string, max: number, required = false) {
  if (value !== undefined && typeof value !== "string") {
    throw new InventoryWorkItemError(400, "invalid_request", `${label}必须是文本`);
  }
  const normalized = typeof value === "string" ? value.trim() : "";
  if (required && !normalized) throw new InventoryWorkItemError(400, "invalid_request", `${label}不能为空`);
  if (Array.from(normalized).length > max) throw new InventoryWorkItemError(400, "invalid_request", `${label}不能超过 ${max} 个字符`);
  return normalized;
}

function calendarDate(value: unknown, label: string, fallback: string) {
  const normalized = text(value, label, 10) || fallback;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) throw new InventoryWorkItemError(400, "invalid_request", `${label}必须为 YYYY-MM-DD`);
  const [year, month, day] = normalized.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) {
    throw new InventoryWorkItemError(400, "invalid_request", `${label}不是有效日期`);
  }
  return normalized;
}

function shanghaiToday() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function addDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export type InventoryWorkflowWriter = {
  create(principal: AppPrincipal, payload: Record<string, unknown>): Promise<{
    created: boolean;
    task: { id: string; title: string; status: string; [key: string]: unknown };
  }>;
};

function createInventoryWorkflowWriter(): InventoryWorkflowWriter {
  return {
    async create(principal, payload) {
      const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, {
        method: "POST",
        path: WORKFLOW_INVENTORY_WORK_ITEMS_PATH,
        service: "writer",
        payload,
      });
      const task = result.data.task;
      if (typeof result.data.created !== "boolean" || !task || typeof task !== "object" || Array.isArray(task)
        || typeof (task as Record<string, unknown>).id !== "string"
        || typeof (task as Record<string, unknown>).title !== "string"
        || typeof (task as Record<string, unknown>).status !== "string") {
        throw new InventoryWorkItemError(503, "service_unavailable", "Django 运营事务服务返回了无效结果");
      }
      return { created: result.data.created, task: task as { id: string; title: string; status: string } };
    },
  };
}

async function createLinkedTask(input: {
  writer: InventoryWorkflowWriter;
  principal: AppPrincipal;
  entityId: string;
  entityLabel: string;
  title: string;
  workContent: string;
  category: string;
  owner: string;
  startDate: string;
  dueDate: string;
  priority: "high" | "normal" | "low";
}) {
  return input.writer.create(input.principal, {
    entityType: "product",
    entityId: input.entityId,
    label: input.entityLabel,
    url: "",
    title: input.title,
    workContent: input.workContent,
    category: input.category,
    owner: input.owner,
    shopName: "供应链",
    startDate: input.startDate,
    due: input.dueDate,
    priority: input.priority,
  });
}

export async function createInventoryWorkItem(
  input: InventoryWorkItemInput,
  principal: AppPrincipal,
  reader: InventoryConsumerReader = createDjangoInventoryConsumerReader(),
  writer: InventoryWorkflowWriter = createInventoryWorkflowWriter(),
) {
  const allowedKeys = new Set([
    "kind", "planId", "inventoryKey", "owner", "dueDate", "expectedArrivalDate",
    "planType", "cleanupStrategy", "expectedConsumptionDays", "notes", "label",
  ]);
  const unknownKey = Object.keys(input).find((key) => !allowedKeys.has(key));
  if (unknownKey) throw new InventoryWorkItemError(400, "invalid_request", `请求包含不支持的字段：${unknownKey}`);
  const kind = input.kind;
  const owner = text(input.owner, "负责人", 120, true);
  const notes = text(input.notes, "备注", 800);
  const today = shanghaiToday();

  if (kind === "procurement") {
    const planId = text(input.planId, "备货计划 ID", 128, true);
    const reference = await reader.read(principal, {
      operation: "work_item_reference",
      kind: "procurement",
      referenceId: planId,
    });
    const plan = reference.data.plan as {
      id: string;
      productCode: string;
      productName: string;
      warehouse: string;
      plannedQuantity: number;
      coverageDays: number | null;
      status: string;
    } | null;
    if (!plan) throw new InventoryWorkItemError(404, "not_found", "备货计划不存在");
    if (plan.status !== "confirmed") {
      throw new InventoryWorkItemError(409, "plan_not_confirmed", "只有已确认的备货计划才能转为采购执行事项");
    }
    const planType = input.planType === "new_product" ? "新品首单" : input.planType === "daily" || input.planType === undefined ? "日常补货" : null;
    if (!planType) throw new InventoryWorkItemError(400, "invalid_request", "计划类型必须是 daily 或 new_product");
    const expectedArrivalDate = calendarDate(input.expectedArrivalDate, "预计到货日期", addDays(today, 7));
    const dueDate = calendarDate(input.dueDate, "截止日期", expectedArrivalDate);
    if (expectedArrivalDate < today || dueDate < today) {
      throw new InventoryWorkItemError(400, "invalid_request", "预计到货日期和截止日期不能早于今天");
    }
    const supplierName = typeof reference.data.supplier === "string" && reference.data.supplier.trim()
      ? reference.data.supplier.trim()
      : "供应商待补充";
    const workContent = [
      `计划类型：${planType}`,
      `供应商：${supplierName}`,
      `入仓仓库：${plan.warehouse}`,
      `计划数量：${plan.plannedQuantity}`,
      `预计到货：${expectedArrivalDate}`,
      `当前可售：${plan.coverageDays === null ? "未知" : `${plan.coverageDays.toFixed(1)} 天`}`,
      notes ? `备注：${notes}` : "",
    ].filter(Boolean).join("\n");
    return createLinkedTask({
      writer,
      principal,
      entityId: `replenishment-plan:${plan.id}`,
      entityLabel: `${plan.productName} · ${plan.warehouse} · ${plan.plannedQuantity} 件`,
      title: `[采购备货] ${plan.productName}`,
      workContent,
      category: "库存补货",
      owner,
      startDate: today,
      dueDate,
      priority: plan.coverageDays !== null && plan.coverageDays <= 7 ? "high" : "normal",
    });
  }

  if (kind === "stale_cleanup") {
    const inventoryKey = text(input.inventoryKey, "库存货品标识", 240, true);
    const parts = inventoryKey.split("\u001f");
    if (parts.length !== 2 || parts.some((part) => !part.trim())) {
      throw new InventoryWorkItemError(400, "invalid_request", "库存货品标识必须精确包含仓库与货品编码");
    }
    const reference = await reader.read(principal, {
      operation: "work_item_reference",
      kind: "stale_cleanup",
      referenceId: inventoryKey,
    });
    const item = reference.data.item as {
      productName: string;
      warehouse: string;
      availableQuantity: number;
      inventoryAgeDays: number | null;
      sales30dQuantity: number | null;
      recommendation: string;
      status: string;
    } | null;
    if (!item) throw new InventoryWorkItemError(404, "not_found", "最新库存快照中未找到该货品");
    if (!(["stagnant", "slow", "aged"] as const).includes(item.status as "stagnant" | "slow" | "aged")) {
      throw new InventoryWorkItemError(409, "not_cleanup_candidate", "该货品当前不在滞销或高库龄清理范围内");
    }
    const strategyLabels: Record<string, string> = {
      promotion: "促销清理",
      transfer: "渠道/仓间调拨",
      return: "退供或清退评估",
      review: "人工复核后定方案",
    };
    const strategy = typeof input.cleanupStrategy === "string" ? strategyLabels[input.cleanupStrategy] : strategyLabels.review;
    if (!strategy) throw new InventoryWorkItemError(400, "invalid_request", "清理方案无效");
    const expectedDays = input.expectedConsumptionDays === undefined
      ? 30
      : Number(input.expectedConsumptionDays);
    if (!Number.isSafeInteger(expectedDays) || expectedDays < 1 || expectedDays > 365) {
      throw new InventoryWorkItemError(400, "invalid_request", "预计消耗天数必须为 1 到 365 的整数");
    }
    const dueDate = calendarDate(input.dueDate, "截止日期", addDays(today, Math.min(expectedDays, 30)));
    if (dueDate < today) throw new InventoryWorkItemError(400, "invalid_request", "截止日期不能早于今天");
    const workContent = [
      `清理方案：${strategy}`,
      `仓库：${item.warehouse}`,
      `当前库存：${item.availableQuantity}`,
      `库存库龄：${item.inventoryAgeDays === null ? "未知" : `${item.inventoryAgeDays} 天`}`,
      `前 30 天销量：${item.sales30dQuantity === null ? "未知" : item.sales30dQuantity}`,
      `期望消耗周期：${expectedDays} 天`,
      `系统建议：${item.recommendation}`,
      notes ? `备注：${notes}` : "",
    ].filter(Boolean).join("\n");
    return createLinkedTask({
      writer,
      principal,
      entityId: `inventory-stale:${(reference.data.sync as { latestInventoryBatchId?: string | null } | undefined)?.latestInventoryBatchId ?? "unknown"}:${inventoryKey}`,
      entityLabel: `${item.productName} · ${item.warehouse}`,
      title: `[滞销清理] ${item.productName}`,
      workContent,
      category: "滞销清理",
      owner,
      startDate: today,
      dueDate,
      priority: item.status === "stagnant" ? "high" : "normal",
    });
  }

  throw new InventoryWorkItemError(400, "invalid_request", "kind 必须是 procurement 或 stale_cleanup");
}
