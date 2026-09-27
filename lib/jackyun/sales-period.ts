const dayMilliseconds = 86_400_000;
export const jackyunSalesWindowDays = 45;

function isoDate(value: string) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error("销售日期格式无效。");
  const date = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) throw new Error("销售日期无效。");
  return date;
}

/** End date is yesterday in the plan's Shanghai business calendar, inclusive. */
export function rollingJackyunSalesStartDate(endDate: string) {
  const result = new Date(isoDate(endDate).getTime() - (jackyunSalesWindowDays - 1) * dayMilliseconds).toISOString().slice(0, 10);
  isoDate(result);
  return result;
}

/** Old frozen runs without an explicit start keep their original monthly scope. */
export function jackyunSalesPeriod(endDate: string, salesStartDate?: string) {
  isoDate(endDate);
  const startDate = salesStartDate === undefined ? `${endDate.slice(0, 8)}01` : salesStartDate;
  isoDate(startDate);
  if (salesStartDate !== undefined && startDate !== rollingJackyunSalesStartDate(endDate)) {
    throw new Error("销售滚动范围必须是截至计划截止日的完整 45 天。");
  }
  return { startDate, endDate };
}
