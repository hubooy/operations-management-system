import type { BudgetPlan, BudgetResult } from "../../app/ai-business-budget";
export type { BudgetPlan, BudgetResult };
export type EvidenceBinding = { evidenceRunId: string; evidenceVersion: number; evidencePlanDigest: string; catalogDigest: string; sealedDigest: string };
export type BudgetRun = { id: string; status: string; version: number; plan: { schemaVersion?: string; sourceCount?: number; catalogDigest?: string }; sources: Record<string, { complete: boolean }> };
export type Source = { key: string; domain: string; query: Record<string, string>; ordinal: number };
export type TargetRow = { id: string; rowIndex: number; entity: Record<string, unknown>; dimensionMissing: boolean; metrics: Record<string, { value: number | null; missingRows: number } | null> };
export type SelectedTarget = { source: Source; dimension: string; row: TargetRow; fields: Record<string, string> };
export type TargetPage = { schemaVersion: string; evidenceBinding: EvidenceBinding; sourceKey: string; dimension: string; sourceMetadata: { coverage?: { status?: string } }; rows: TargetRow[]; pagination: { offset: number; limit: number; total: number; nextOffset: number | null; hasMore: boolean } };
export const dimensions = { shop: "店铺", category: "品类", spu: "SPU", sku: "SKU", keyword: "关键词", searchTerm: "搜索词" };
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return "["+value.map(canonical).join(",")+"]";
  if (value !== null && typeof value === "object") return "{"+Object.keys(value).sort().map(key => JSON.stringify(key)+":"+canonical((value as Record<string, unknown>)[key])).join(",")+"}";
  return JSON.stringify(value);
}
export function validateBinding(value: EvidenceBinding, run: BudgetRun, fixed?: EvidenceBinding | null) {
  if (!value || Object.keys(value).sort().join(",") !== "catalogDigest,evidencePlanDigest,evidenceRunId,evidenceVersion,sealedDigest" || value.evidenceRunId !== run.id || value.evidenceVersion !== run.version || value.catalogDigest !== run.plan.catalogDigest || ![value.catalogDigest, value.evidencePlanDigest, value.sealedDigest].every(item => typeof item === "string" && /^[a-f0-9]{64}$/.test(item)) || (fixed && canonical(fixed) !== canonical(value))) throw new Error("封存证据绑定已变化，请重新打开预算配置。");
}
export function eligible(source: Source): string {
  if (source.domain !== "netshop" || source.query.dataset !== "promotion") return "非推广来源，不能作为预算基数";
  if ((source.query.window ?? "current") !== "current") return "非本期来源，仅用于比较，不能分配本期预算";
  return "";
}
export function validateTargetPage(page: TargetPage, run: BudgetRun, sourceKey: string, dimension: string, offset: number, fixed?: EvidenceBinding | null) {
  validateBinding(page?.evidenceBinding, run, fixed);
  const p = page.pagination;
  if (page.schemaVersion !== "business-budget-targets-v1" || page.sourceKey !== sourceKey || page.dimension !== dimension || !p || p.offset !== offset || p.limit !== 20 || !Number.isSafeInteger(p.total) || p.total < 0 || !Array.isArray(page.rows) || page.rows.length > 20 || offset+page.rows.length > p.total || (!page.rows.length && offset < p.total) || p.nextOffset !== (offset+page.rows.length < p.total ? offset+page.rows.length : null) || p.hasMore !== (p.nextOffset !== null) || !page.sourceMetadata || page.rows.some((row, i) => !row || !/^[a-f0-9]{64}$/.test(row.id) || row.rowIndex !== offset+i || typeof row.dimensionMissing !== "boolean" || !row.entity || !row.metrics) || new Set(page.rows.map(row => row.id)).size !== page.rows.length) throw new Error("预算目标分页回执无效，未启用选择。");
}
function number(raw: string | undefined, scale: number, min: number, max: number, label: string): number {
  const text = (raw ?? "").trim(), digits = scale === 10000 ? 4 : scale === 100 ? 2 : 0;
  if (!(digits ? new RegExp(`^\\d{1,13}(?:\\.\\d{1,${digits}})?$`) : /^\d{1,13}$/).test(text)) throw new Error(`请明确填写${label}，不能留空或使用超出精度的数字。`);
  const [whole, decimal = ""] = text.split(".");
  const result = Number(whole)*scale+(digits ? Number(decimal.padEnd(digits, "0")) : 0);
  if (!Number.isSafeInteger(result) || result < min || result > max) throw new Error(`${label}超出允许范围。`);
  return result;
}
function label(raw: string | undefined, name: string) {
  const value = (raw ?? "").trim();
  if (!value || value.length > 80 || /[\u0000-\u001f]/.test(value)) throw new Error(`请填写有效的${name}（最多 80 字）。`);
  return value;
}
export function makePlan(fields: Record<string, string>, selected: SelectedTarget[], scenarios: Record<string, string>[]): BudgetPlan {
  if (selected.length < 1 || selected.length > 100 || scenarios.length < 1 || scenarios.length > 5) throw new Error("请选择 1–100 个目标并填写 1–5 个情景。");
  const total = number(fields.totalBudgetCents, 100, 1, 1e12, "总预算"), horizon = number(fields.horizonDays, 1, 1, 93, "规划天数");
  const scopes = new Map<string, string>(), identities = new Set<string>(), dates = new Set<string>();
  const targets = selected.map(({ source, dimension, row, fields: input }) => {
    if (eligible(source) || !(dimension in dimensions) || row.dimensionMissing || !/^[a-f0-9]{64}$/.test(row.id) || !Number.isSafeInteger(row.rowIndex) || row.rowIndex < 0 || row.rowIndex > 249999) throw new Error("预算目标身份无效。");
    const scope = canonical([source.query.platform, source.query.shop]), group = canonical([source.key, dimension]), id = canonical([group, row.id]);
    if (scopes.has(scope) && scopes.get(scope) !== group) throw new Error("同一店铺不能混选不同来源或聚合维度。");
    if (identities.has(id)) throw new Error("预算目标重复。");
    scopes.set(scope, group); identities.add(id); dates.add(canonical([source.query.startDate, source.query.endDate]));
    const max = number(input.maxBudgetCents, 100, 0, 1e12, "目标最高预算");
    return { sourceKey: source.key, dimension, rowIndex: row.rowIndex, rowId: row.id, weight: number(input.weight, 1, 1, 10000, "分配权重"), minBudgetCents: number(input.minBudgetCents, 100, 0, max, "目标最低预算"), maxBudgetCents: max, ownerRole: label(input.ownerRole, "负责人角色"), minimumRoasBps: number(input.minimumRoasBps, 10000, 0, 1000000, "最低产出比") };
  });
  if (dates.size !== 1) throw new Error("预算目标必须使用相同本期日期区间。");
  const assumptions = scenarios.map(s => ({ name: label(s.name, "情景名称"), cpcFactorBps: number(s.cpcFactorBps, 100, 1000, 30000, "点击成本系数"), orderRateFactorBps: number(s.orderRateFactorBps, 100, 1000, 30000, "订单行率系数"), orderValueFactorBps: number(s.orderValueFactorBps, 100, 1000, 30000, "订单行价值系数"), contributionMarginBps: s.contributionMarginBps?.trim() ? number(s.contributionMarginBps, 100, 0, 10000, "贡献毛利率") : null }));
  if (new Set(assumptions.map(s => s.name)).size !== assumptions.length) throw new Error("情景名称不能重复。");
  const reserve = number(fields.reserveCents, 100, 0, total, "预留预算");
  if (targets.reduce((sum, target) => sum+target.minBudgetCents, 0) > total-reserve) throw new Error("目标最低预算合计超过可分配预算。");
  const plan = { totalBudgetCents: total, reserveCents: reserve, horizonDays: horizon, observationDays: number(fields.observationDays, 1, 1, horizon, "观察天数"), reviewAfterSpendBps: number(fields.reviewAfterSpendBps, 100, 1, 10000, "消耗复核比例"), minimumClicks: number(fields.minimumClicks, 1, 1, 1e6, "最低点击数"), minimumOrderLines: number(fields.minimumOrderLines, 1, 1, 1e6, "最低订单行数"), targets, scenarios: assumptions };
  if (new TextEncoder().encode(canonical(plan)).byteLength > 48000) throw new Error("完整预算参数超过容量，未截断目标。");
  return plan;
}
