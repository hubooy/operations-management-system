import { getOperationsBusinessDates } from "@/lib/ai/business-time";
import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  readDjangoSalesConsumer,
  type SalesConsumerReaderResult,
  type SalesConsumerRequestMap,
} from "@/lib/django/sales-consumer-reader";
import { PublicApiError } from "@/lib/http/api-error";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const SALES_REVISION = /^\d+:\d+$/;

type FreshnessRequest = SalesConsumerRequestMap["freshness"];
type FreshnessResult = SalesConsumerReaderResult<FreshnessRequest>;

export type SalesDataHealth = {
  status: "available" | "empty";
  source: "django_postgresql";
  authority: "sales_single_write";
  revision: string;
  timeZone: "Asia/Shanghai";
  currentBusinessDate: string;
  expectedThroughDate: string;
  checkedAt: string;
  coverage: {
    startDate: string | null;
    cutoffDate: string | null;
    coveredDayCount: number;
    lagDaysToCurrentBusinessDate: number | null;
    throughYesterday: boolean;
  };
  latestBatch: {
    id: string;
    fileName: string;
    completedAt: string | null;
    rowCount: number;
  } | null;
};

export type SalesDataHealthOptions = {
  now?: Date;
  read?: (
    principal: AppPrincipal,
    request: FreshnessRequest,
  ) => Promise<FreshnessResult>;
};

function unavailable(): PublicApiError {
  return new PublicApiError(
    503,
    "service_unavailable",
    "销售数据健康状态暂时不可用，请稍后重试。",
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function validIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !ISO_DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function dayDifference(earlier: string, later: string): number {
  const from = new Date(`${earlier}T00:00:00Z`).valueOf();
  const to = new Date(`${later}T00:00:00Z`).valueOf();
  return Math.round((to - from) / 86_400_000);
}

function normalizeLatestBatch(value: unknown): SalesDataHealth["latestBatch"] {
  if (value === null) return null;
  if (!isRecord(value) || !exactKeys(value, ["id", "fileName", "completedAt", "rowCount"])) {
    throw unavailable();
  }
  if (typeof value.id !== "string" || value.id.length < 1 || value.id.length > 128
    || typeof value.fileName !== "string" || value.fileName.length < 1 || value.fileName.length > 255
    || /[\u0000-\u001f\u007f]/.test(value.fileName)
    || (value.completedAt !== null && (
      typeof value.completedAt !== "string"
      || value.completedAt.length < 10
      || value.completedAt.length > 64
      || Number.isNaN(Date.parse(value.completedAt))
    ))
    || !Number.isSafeInteger(value.rowCount) || Number(value.rowCount) < 0) {
    throw unavailable();
  }
  return {
    id: value.id,
    fileName: value.fileName,
    completedAt: value.completedAt as string | null,
    rowCount: Number(value.rowCount),
  };
}

function normalizeFreshness(result: FreshnessResult): {
  revision: string;
  startDate: string | null;
  cutoffDate: string | null;
  latestBatch: SalesDataHealth["latestBatch"];
} {
  if (!SALES_REVISION.test(result.revision) || result.revision.length > 128
    || !isRecord(result.data)
    || !exactKeys(result.data, ["dataStartDate", "dataCutoffDate", "latestBatch"])) {
    throw unavailable();
  }
  const startDate = result.data.dataStartDate;
  const cutoffDate = result.data.dataCutoffDate;
  if ((startDate !== null && !validIsoDate(startDate))
    || (cutoffDate !== null && !validIsoDate(cutoffDate))
    || (startDate === null) !== (cutoffDate === null)
    || (startDate !== null && cutoffDate !== null && startDate > cutoffDate)) {
    throw unavailable();
  }
  return {
    revision: result.revision,
    startDate,
    cutoffDate,
    latestBatch: normalizeLatestBatch(result.data.latestBatch),
  };
}

export async function getSalesDataHealth(
  principal: AppPrincipal,
  options: SalesDataHealthOptions = {},
): Promise<SalesDataHealth> {
  const now = options.now ?? new Date();
  if (Number.isNaN(now.valueOf())) throw unavailable();
  const read = options.read ?? ((actor, request) => readDjangoSalesConsumer(actor, request));
  const freshness = normalizeFreshness(await read(principal, { operation: "freshness" }));
  const dates = getOperationsBusinessDates(now);
  const lagDays = freshness.cutoffDate === null
    ? null
    : dayDifference(freshness.cutoffDate, dates.today);
  if (lagDays !== null && lagDays < 0) throw unavailable();
  const coveredDayCount = freshness.startDate === null || freshness.cutoffDate === null
    ? 0
    : dayDifference(freshness.startDate, freshness.cutoffDate) + 1;

  return {
    status: freshness.cutoffDate === null ? "empty" : "available",
    source: "django_postgresql",
    authority: "sales_single_write",
    revision: freshness.revision,
    timeZone: "Asia/Shanghai",
    currentBusinessDate: dates.today,
    expectedThroughDate: dates.yesterday,
    checkedAt: now.toISOString(),
    coverage: {
      startDate: freshness.startDate,
      cutoffDate: freshness.cutoffDate,
      coveredDayCount,
      lagDaysToCurrentBusinessDate: lagDays,
      throughYesterday: freshness.cutoffDate !== null && freshness.cutoffDate >= dates.yesterday,
    },
    latestBatch: freshness.latestBatch,
  };
}
