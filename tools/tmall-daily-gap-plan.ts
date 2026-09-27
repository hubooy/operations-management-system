/** Pure calendar planner. Coverage must come from the exact store's reader. */
export function planTmallDailyGaps(input: {
  startDate: string;
  endDate: string;
  productDailyDates: readonly string[];
  promotionDates: readonly string[];
  maximumDays: number;
}) {
  const valid = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
  if (!valid(input.startDate) || !valid(input.endDate) || input.startDate > input.endDate
    || !Number.isInteger(input.maximumDays) || input.maximumDays < 1 || input.maximumDays > 30
    || [...input.productDailyDates, ...input.promotionDates].some((date) => !valid(date))) {
    throw new Error("天猫缺失日规划输入无效");
  }
  const count = (Date.parse(input.endDate) - Date.parse(input.startDate)) / 86400000 + 1;
  if (count > 3660) throw new Error("天猫缺失日规划范围超过安全上限");
  const all = Array.from({ length: count }, (_, index) => new Date(Date.parse(input.startDate)
    + index * 86400000).toISOString().slice(0, 10));
  const products = new Set(input.productDailyDates);
  const promotions = new Set(input.promotionDates);
  const missingProductDates = all.filter((date) => !products.has(date));
  const missingPromotionDates = all.filter((date) => !promotions.has(date));
  const gaps = all.filter((date) => !products.has(date) || !promotions.has(date));
  const selectedDates = gaps.slice(0, input.maximumDays);
  return {
    selectedDates,
    productDownloadDates: selectedDates.filter((date) => !products.has(date)),
    missingProductDates,
    missingPromotionDates,
    remainingDates: gaps.slice(input.maximumDays),
    truncated: gaps.length > selectedDates.length,
  };
}
