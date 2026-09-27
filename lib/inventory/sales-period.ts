export type InventorySalesPeriodInput = {
  startDate?: string;
  endDate?: string;
};

export type InventorySalesBounds = {
  startDate: string | null;
  endDate: string | null;
};

const DEFAULT_WINDOW_DAYS = 30;
const MAX_WINDOW_DAYS = 730;

export class InventorySalesPeriodError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InventorySalesPeriodError";
  }
}

function addDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function dayDifference(start: string, end: string) {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000);
}

function isIsoDate(value: string | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value;
}

export function resolveInventorySalesPeriod(options: InventorySalesPeriodInput, salesBounds: InventorySalesBounds) {
  if ((options.startDate && !isIsoDate(options.startDate)) || (options.endDate && !isIsoDate(options.endDate))) {
    throw new InventorySalesPeriodError("库存统计周期日期格式无效，请使用 YYYY-MM-DD。");
  }
  const defaultEndDate = salesBounds.endDate;
  const defaultStartDate = defaultEndDate ? addDays(defaultEndDate, -(DEFAULT_WINDOW_DAYS - 1)) : null;
  const requestedStartDate = options.startDate ?? defaultStartDate;
  const requestedEndDate = options.endDate ?? defaultEndDate;
  if (requestedStartDate && requestedEndDate && requestedStartDate > requestedEndDate) {
    throw new InventorySalesPeriodError("库存统计周期的开始日期不能晚于结束日期。");
  }
  if (requestedStartDate && requestedEndDate && dayDifference(requestedStartDate, requestedEndDate) + 1 > MAX_WINDOW_DAYS) {
    throw new InventorySalesPeriodError(`库存统计周期最多支持 ${MAX_WINDOW_DAYS} 天。`);
  }
  const startCandidate = requestedStartDate && salesBounds.startDate
    ? (requestedStartDate > salesBounds.startDate ? requestedStartDate : salesBounds.startDate)
    : null;
  const endCandidate = requestedEndDate && salesBounds.endDate
    ? (requestedEndDate < salesBounds.endDate ? requestedEndDate : salesBounds.endDate)
    : null;
  const hasOverlap = Boolean(startCandidate && endCandidate && startCandidate <= endCandidate);
  const salesStartDate = hasOverlap ? startCandidate : null;
  const salesEndDate = hasOverlap ? endCandidate : null;
  const salesWindowDays = salesStartDate && salesEndDate
    ? dayDifference(salesStartDate, salesEndDate) + 1
    : requestedStartDate && requestedEndDate ? dayDifference(requestedStartDate, requestedEndDate) + 1 : DEFAULT_WINDOW_DAYS;
  return { requestedStartDate, requestedEndDate, salesStartDate, salesEndDate, salesWindowDays };
}
