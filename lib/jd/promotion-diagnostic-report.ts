import { createXlsxWorkbookBytes, type XlsxOutputSheet } from "../imports/xlsx-write";

export const PROMOTION_DIAGNOSTIC_SCHEMA = "jd-promotion-diagnostic-v1";
type MetricKey = "spendCents" | "impressions" | "clicks" | "reportedOrderLines" | "reportedGmvCents";
const METRICS: MetricKey[] = ["spendCents", "impressions", "clicks", "reportedOrderLines", "reportedGmvCents"];

export type DiagnosticMetrics = Record<MetricKey, number | null>;
export type DiagnosticBatchOwnership = { batchId: string; status: string; source: string; dataset: string; platform: string;
  shopName: string; dateMin: string; dateMax: string; rowCount: number; warningCount: number };
export type DiagnosticGroup = {
  key: string;
  rowCount: number;
  metrics: DiagnosticMetrics;
  id?: string | null;
  name?: string;
  planId?: string | null;
  skuId?: string | null;
  keyword?: string | null;
  searchTerm?: string | null;
};
export type DiagnosticPeriod = {
  schemaVersion: string;
  identity: { platform: string; shopName: string };
  period: { startDate: string; endDate: string };
  sourceRevision: string;
  coverage: {
    requestedDates: string[];
    presentDates: string[];
    missingDates: string[];
    complete: boolean;
    rowCount: number;
    aggregateReconciled: boolean;
    batchOwnershipReconciled: boolean;
  };
  metricAvailability: Record<MetricKey, { presentRows: number; totalRows: number; complete: boolean }>;
  sourceBatches: Array<{ date: string; batchIds: string[]; accountNicknames: string[]; accountPresentRows: number; rowCount: number; aggregateBatchId: string;
    ownership: DiagnosticBatchOwnership[]; aggregateOwnership: DiagnosticBatchOwnership }>;
  summary: DiagnosticMetrics;
  daily: Array<{ date: string; rowCount: number; metrics: DiagnosticMetrics }>;
  groups: Record<"plans" | "products" | "keywords" | "searchTerms" | "keywordSku", DiagnosticGroup[]>;
  limitations: string[];
};

type Cell = string | number | null;
export type ReportColumn = { key: string; label: string; kind: "text" | "number" | "money" | "percent" | "ratio" };
export type ReportTable = { key: string; title: string; note: string; columns: ReportColumn[]; rows: Cell[][] };
export type ReportTarget = { tableKey: string; groupKey: string };
export type ReportAction = { priority: string; object: string; evidence: string; change: string; metric: string; observation: string; rollback: string; tableKey: string; target?: ReportTarget };
export type PromotionDiagnosticReport = {
  schemaVersion: "jd-promotion-report-v1";
  analysisType: "deterministic_review_draft";
  shopName: string;
  period: { startDate: string; endDate: string };
  previousPeriod: { startDate: string; endDate: string } | null;
  complete: boolean;
  comparisonAvailable: boolean;
  sourceRevision: string;
  metrics: DiagnosticMetrics;
  previousMetrics: DiagnosticMetrics | null;
  coverage: DiagnosticPeriod["coverage"];
  findings: Array<{ title: string; text: string; tableKey: string; target?: ReportTarget }>;
  actions: ReportAction[];
  tables: ReportTable[];
  limitations: string[];
};

function validDate(value: string) {
  return /^20\d\d-\d\d-\d\d$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

function dates(start: string, end: string) {
  if (!validDate(start) || !validDate(end) || start > end) throw new Error("推广诊断日期无效");
  const result: string[] = [];
  for (let time = Date.parse(`${start}T00:00:00Z`); time <= Date.parse(`${end}T00:00:00Z`); time += 86_400_000) {
    result.push(new Date(time).toISOString().slice(0, 10));
    if (result.length > 31) throw new Error("推广诊断超过31天");
  }
  return result;
}

function safeMetric(value: number | null, name: string) {
  if (value !== null && (!Number.isSafeInteger(value) || value < 0)) throw new Error(`推广诊断${name}不是非负整数`);
}

export function validateDiagnosticPeriod(period: DiagnosticPeriod) {
  if (period?.schemaVersion !== PROMOTION_DIAGNOSTIC_SCHEMA || period.identity?.platform !== "京东"
    || !period.identity?.shopName || !period.sourceRevision || period.coverage?.aggregateReconciled !== true
    || period.coverage?.batchOwnershipReconciled !== true) {
    throw new Error("推广诊断来源身份、修订或逐日对账无效");
  }
  const expected = dates(period.period.startDate, period.period.endDate);
  if (JSON.stringify(period.coverage.requestedDates) !== JSON.stringify(expected)
    || !Array.isArray(period.daily) || period.daily.length !== expected.length
    || period.daily.some((item, index) => item.date !== expected[index])
    || period.coverage.complete !== (period.coverage.missingDates.length === 0)
    || !Number.isSafeInteger(period.coverage.rowCount) || period.coverage.rowCount < 0) {
    throw new Error("推广诊断日期覆盖或行数无效");
  }
  if (period.sourceBatches.length !== period.coverage.presentDates.length
    || new Set(period.sourceBatches.map((item) => item.date)).size !== period.sourceBatches.length
    || period.sourceBatches.some((item) => !period.coverage.presentDates.includes(item.date)
      || !Array.isArray(item.accountNicknames) || item.accountNicknames.length > 10
      || !Number.isSafeInteger(item.accountPresentRows) || item.accountPresentRows < 0 || item.accountPresentRows > item.rowCount
      || !Array.isArray(item.ownership) || item.ownership.length !== item.batchIds.length
      || !item.aggregateOwnership || item.aggregateOwnership.batchId !== item.aggregateBatchId
      || ![...item.ownership, item.aggregateOwnership].every((owner) => owner.status === "completed"
        && owner.source === "jd_promotion" && owner.dataset === "ad" && owner.platform === "京东"
        && owner.shopName === period.identity.shopName && owner.dateMin <= item.date && owner.dateMax >= item.date
        && Number.isSafeInteger(owner.rowCount) && owner.rowCount >= 1
        && Number.isSafeInteger(owner.warningCount) && owner.warningCount >= 0)
      || item.ownership.some((owner) => !item.batchIds.includes(owner.batchId)))) {
    throw new Error("推广来源账户或系统批次归属证据无效");
  }
  for (const key of METRICS) {
    safeMetric(period.summary[key], key);
    for (const day of period.daily) safeMetric(day.metrics?.[key], key);
    const availability = period.metricAvailability?.[key];
    if (!availability || availability.totalRows !== period.coverage.rowCount
      || !Number.isSafeInteger(availability.presentRows) || availability.presentRows < 0
      || availability.presentRows > availability.totalRows
      || availability.complete !== (availability.totalRows > 0 && availability.presentRows === availability.totalRows)
      || (availability.complete && period.summary[key] === null)) {
      throw new Error("推广诊断指标可用性与来源行数不一致");
    }
  }
  for (const name of ["plans", "products", "keywords", "searchTerms", "keywordSku"] as const) {
    const groups = period.groups?.[name];
    if (!Array.isArray(groups) || groups.reduce((total, group) => total + group.rowCount, 0) !== period.coverage.rowCount
      || new Set(groups.map((group) => group.key)).size !== groups.length) {
      throw new Error(`推广诊断${name}分组未与来源行数对平`);
    }
    for (const group of groups) {
      if (!group.key || !Number.isSafeInteger(group.rowCount) || group.rowCount < 1) throw new Error("推广诊断分组身份无效");
      for (const key of METRICS) safeMetric(group.metrics?.[key], key);
    }
  }
  return expected;
}

function value(metrics: DiagnosticMetrics, key: MetricKey) { return metrics[key]; }
function money(cents: number | null) { return cents === null ? null : cents / 100; }
function ratio(numerator: number | null, denominator: number | null) {
  return numerator === null || denominator === null || denominator <= 0 ? null : numerator / denominator;
}
function percent(numerator: number | null, denominator: number | null) {
  const raw = ratio(numerator, denominator);
  return raw === null ? null : Math.round(raw * 10_000) / 100;
}
function rounded(raw: number | null, places = 2) { return raw === null ? null : Math.round(raw * 10 ** places) / 10 ** places; }
function change(current: number | null, previous: number | null) {
  return current === null || previous === null || previous <= 0 ? null : rounded((current / previous - 1) * 100);
}
function metricRow(label: string, current: number | null, previous: number | null, unit: string, comparable: boolean): Cell[] {
  return [label, current, comparable ? previous : null, comparable ? change(current, previous) : null, unit];
}
function metricGroup(group: DiagnosticGroup): Cell[] {
  const m = group.metrics;
  return [group.name ?? group.id ?? group.key, group.rowCount, money(m.spendCents), m.impressions,
    m.clicks, percent(m.clicks, m.impressions), m.reportedOrderLines,
    percent(m.reportedOrderLines, m.clicks), money(m.reportedGmvCents), rounded(ratio(m.reportedGmvCents, m.spendCents)), group.key];
}

const GROUP_COLUMNS: ReportColumn[] = [
  { key: "name", label: "对象", kind: "text" }, { key: "rows", label: "来源行", kind: "number" },
  { key: "spend", label: "花费（元）", kind: "money" }, { key: "impressions", label: "展现", kind: "number" },
  { key: "clicks", label: "点击", kind: "number" }, { key: "ctr", label: "CTR（%）", kind: "percent" },
  { key: "orders", label: "归因订单行", kind: "number" }, { key: "orderRate", label: "点击→归因订单行（%）", kind: "percent" },
  { key: "gmv", label: "归因总订单金额（元）", kind: "money" }, { key: "roas", label: "归因ROAS", kind: "ratio" },
  { key: "groupKey", label: "分组身份（来源键）", kind: "text" },
];

type PrimaryGroupKey = "plans" | "products" | "keywords" | "searchTerms";
type GroupComparison = { current: DiagnosticGroup | null; previous: DiagnosticGroup | null; state: string };

function stableIdentity(tableKey: PrimaryGroupKey, group: DiagnosticGroup) {
  const field = tableKey === "plans"
    ? (group.planId?.trim() ? `id:${group.planId.trim()}` : group.name?.trim() && group.name !== "未提供计划名称" ? `name:${group.name.trim()}` : null)
    : (tableKey === "products" ? group.skuId : tableKey === "keywords" ? group.keyword : group.searchTerm)?.trim();
  return field || null;
}

function compareGroups(tableKey: PrimaryGroupKey, current: DiagnosticGroup[], previous: DiagnosticGroup[] | null): GroupComparison[] {
  const identity = (group: DiagnosticGroup) => stableIdentity(tableKey, group);
  const count = (groups: DiagnosticGroup[]) => {
    const counts = new Map<string, number>();
    for (const group of groups) {
      const id = identity(group);
      if (id) counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    return counts;
  };
  const currentCounts = count(current), previousCounts = count(previous ?? []);
  const previousByIdentity = new Map((previous ?? []).map((group) => [identity(group), group]));
  const currentIdentities = new Set<string>();
  const rows: GroupComparison[] = current.map((group) => {
    const id = identity(group);
    if (!id || currentCounts.get(id) !== 1 || (previousCounts.get(id) ?? 0) > 1) {
      return { current: group, previous: null, state: "身份未知" };
    }
    currentIdentities.add(id);
    if (!previous) return { current: group, previous: null, state: "无前期基线" };
    const baseline = previousByIdentity.get(id) ?? null;
    return { current: group, previous: baseline, state: baseline ? "可比" : "新增" };
  });
  if (previous) for (const group of previous) {
    const id = identity(group);
    if (!id || previousCounts.get(id) !== 1 || (currentCounts.get(id) ?? 0) > 1) {
      rows.push({ current: null, previous: group, state: "身份未知" });
    } else if (!currentIdentities.has(id)) {
      rows.push({ current: null, previous: group, state: "消失" });
    }
  }
  return rows;
}

function percentagePointChange(current: number | null, previous: number | null) {
  return current === null || previous === null ? null : rounded(current - previous);
}

function comparisonRow(pair: GroupComparison): Cell[] {
  const current = pair.current?.metrics, previous = pair.previous?.metrics;
  const comparable = pair.state === "可比";
  const currentCtr = current ? percent(current.clicks, current.impressions) : null;
  const previousCtr = previous ? percent(previous.clicks, previous.impressions) : null;
  const currentOrderRate = current ? percent(current.reportedOrderLines, current.clicks) : null;
  const previousOrderRate = previous ? percent(previous.reportedOrderLines, previous.clicks) : null;
  const currentRoas = current ? rounded(ratio(current.reportedGmvCents, current.spendCents)) : null;
  const previousRoas = previous ? rounded(ratio(previous.reportedGmvCents, previous.spendCents)) : null;
  const metric = (key: MetricKey, amount = false): Cell[] => {
    const now = current?.[key] ?? null, before = previous?.[key] ?? null;
    return [amount ? money(now) : now, amount ? money(before) : before, comparable ? change(now, before) : null];
  };
  return [pair.current?.name ?? pair.current?.id ?? pair.previous?.name ?? pair.previous?.id ?? pair.current?.key ?? pair.previous?.key ?? "—",
    pair.state, pair.current?.rowCount ?? null, pair.previous?.rowCount ?? null,
    ...metric("spendCents", true), ...metric("impressions"), ...metric("clicks"),
    currentCtr, previousCtr, comparable ? percentagePointChange(currentCtr, previousCtr) : null,
    ...metric("reportedOrderLines"), currentOrderRate, previousOrderRate,
    comparable ? percentagePointChange(currentOrderRate, previousOrderRate) : null,
    ...metric("reportedGmvCents", true), currentRoas, previousRoas,
    comparable ? change(currentRoas, previousRoas) : null, pair.current?.key ?? pair.previous?.key ?? null];
}

const COMPARISON_COLUMNS: ReportColumn[] = [
  { key: "name", label: "对象", kind: "text" }, { key: "state", label: "对照状态", kind: "text" },
  { key: "currentRows", label: "本期来源行", kind: "number" }, { key: "previousRows", label: "前期来源行", kind: "number" },
  ...([["spend", "花费（元）", "money"], ["impressions", "展现", "number"], ["clicks", "点击", "number"]] as const).flatMap(([key, label, kind]) => [
    { key: `${key}Current`, label: `本期${label}`, kind }, { key: `${key}Previous`, label: `前期${label}`, kind },
    { key: `${key}Change`, label: `${label}环比（%）`, kind: "percent" as const },
  ]),
  { key: "ctrCurrent", label: "本期CTR（%）", kind: "percent" }, { key: "ctrPrevious", label: "前期CTR（%）", kind: "percent" },
  { key: "ctrPointChange", label: "CTR变化（百分点）", kind: "percent" },
  ...([["orders", "归因订单行", "number"]] as const).flatMap(([key, label, kind]) => [
    { key: `${key}Current`, label: `本期${label}`, kind }, { key: `${key}Previous`, label: `前期${label}`, kind },
    { key: `${key}Change`, label: `${label}环比（%）`, kind: "percent" as const },
  ]),
  { key: "orderRateCurrent", label: "本期点击→归因订单行率（%）", kind: "percent" },
  { key: "orderRatePrevious", label: "前期点击→归因订单行率（%）", kind: "percent" },
  { key: "orderRatePointChange", label: "归因订单行率变化（百分点）", kind: "percent" },
  { key: "gmvCurrent", label: "本期归因金额（元）", kind: "money" },
  { key: "gmvPrevious", label: "前期归因金额（元）", kind: "money" },
  { key: "gmvChange", label: "归因金额环比（%）", kind: "percent" },
  { key: "roasCurrent", label: "本期归因ROAS", kind: "ratio" },
  { key: "roasPrevious", label: "前期归因ROAS", kind: "ratio" },
  { key: "roasChange", label: "归因ROAS环比（%）", kind: "percent" },
  { key: "groupKey", label: "分组身份（来源键）", kind: "text" },
];

export function buildPromotionDiagnosticReport(current: DiagnosticPeriod, previous?: DiagnosticPeriod | null): PromotionDiagnosticReport {
  const currentDates = validateDiagnosticPeriod(current);
  if (previous) validateDiagnosticPeriod(previous);
  if (previous && (previous.identity.shopName !== current.identity.shopName || previous.identity.platform !== current.identity.platform)) {
    throw new Error("推广诊断对照期店铺身份不一致");
  }
  const comparable = Boolean(previous && previous.coverage.complete && current.coverage.complete
    && previous.sourceRevision === current.sourceRevision && previous.coverage.requestedDates.length === currentDates.length);
  const m = current.summary, p = comparable ? previous!.summary : null;
  const currentOrderRate = percent(m.reportedOrderLines, m.clicks);
  const previousOrderRate = p ? percent(p.reportedOrderLines, p.clicks) : null;
  const currentCtr = percent(m.clicks, m.impressions);
  const previousCtr = p ? percent(p.clicks, p.impressions) : null;
  const currentRoas = rounded(ratio(m.reportedGmvCents, m.spendCents));
  const previousRoas = p ? rounded(ratio(p.reportedGmvCents, p.spendCents)) : null;
  const identifiablePlans = current.groups.plans.filter((item) => item.planId || (item.name && item.name !== "未提供计划名称"));
  const unknownPlan = current.groups.plans.find((item) => !identifiablePlans.includes(item));
  const unknownKeyword = current.groups.keywords.find((item) => !item.keyword);
  const anonymousPlanSpend = current.groups.plans.filter((item) => !identifiablePlans.includes(item))
    .reduce((sum, item) => sum + (item.metrics.spendCents ?? 0), 0);
  const topPlans = [...identifiablePlans].filter((item) => item.metrics.spendCents !== null)
    .sort((a, b) => (b.metrics.spendCents ?? 0) - (a.metrics.spendCents ?? 0)).slice(0, 3);
  const topSpend = topPlans.reduce((sum, item) => sum + (item.metrics.spendCents ?? 0), 0);
  const concentration = topPlans.length ? percent(topSpend, m.spendCents) : null;
  const anonymousShare = percent(anonymousPlanSpend, m.spendCents);
  const unlabelledSpendShare = (groups: DiagnosticGroup[], key: "keyword" | "searchTerm") =>
    percent(groups.filter((group) => !group[key]).reduce((sum, group) => sum + (group.metrics.spendCents ?? 0), 0), m.spendCents);
  const noKeywordShare = unlabelledSpendShare(current.groups.keywords, "keyword");
  const noSearchTermShare = unlabelledSpendShare(current.groups.searchTerms, "searchTerm");
  const primaryKeys = ["plans", "products", "keywords", "searchTerms"] as const;
  const groupComparisons = Object.fromEntries(primaryKeys.map((key) => [key,
    compareGroups(key, current.groups[key], comparable ? previous!.groups[key] : null),
  ])) as Record<PrimaryGroupKey, GroupComparison[]>;
  const findings: PromotionDiagnosticReport["findings"] = [];
  const limitations = [...new Set([
    ...current.limitations,
    ...(previous?.limitations ?? []),
    "推广总订单金额和订单行是平台归因口径，不是ERP净销售、利润或增量效果。",
    "各维度是同一推广来源行的不同分组，不可跨表相加。归因窗口未独立核实。",
    "关键词或搜索词为空可能对应非搜索类定向；空值单列，不把它当成关键词或搜索词效果。",
    "品类、SPU、市场、店铺净销售、B端销售和同比尚未接入同店同日期的可核对来源，本报告不推断这些结论。",
    "规则诊断仅列人工复核候选；未调用模型，也未自动修改投放。",
  ])];
  if (!current.coverage.complete) findings.push({ title: "本期数据未齐", text: `缺少 ${current.coverage.missingDates.join("、")}，暂不形成完整周期结论。`, tableKey: "coverage" });
  if (!comparable) {
    limitations.push(previous ? "前期缺日、周期长度或来源修订不同，环比数值留空。" : "未提供前等长周期来源，环比数值留空。");
  } else if (currentOrderRate !== null && previousOrderRate !== null) {
    const direction = currentOrderRate < previousOrderRate ? "下降" : currentOrderRate > previousOrderRate ? "上升" : "持平";
    findings.push({ title: "展现→点击→归因转化", text: `CTR ${previousCtr ?? "—"}%→${currentCtr ?? "—"}%，点击到归因订单行率 ${previousOrderRate}%→${currentOrderRate}%（${direction}）。这是平台归因表的变化，不证明原因或利润变化。`, tableKey: "summary" });
  }
  if (current.coverage.complete) {
    if (concentration !== null) findings.push({ title: "可识别计划花费", text: `可识别计划中花费前三合计占全店 ${concentration}%；计划身份缺失的花费占 ${anonymousShare ?? "—"}%。优先核搜索词、跟单SKU和归因成熟度，不能仅凭集中度判断低效。`, tableKey: "plans" });
    else if (anonymousShare !== null && anonymousShare > 0) findings.push({ title: "计划身份缺失", text: `计划身份缺失的花费占 ${anonymousShare}%；不能给出计划排名或计划级预算建议。`, tableKey: "plans", ...(unknownPlan ? { target: { tableKey: "plans", groupKey: unknownPlan.key } } : {}) });
    if (noKeywordShare !== null && noKeywordShare > 0) findings.push({ title: "关键词适用范围", text: `无关键词来源行占全店花费 ${noKeywordShare}%；搜索词空值花费占 ${noSearchTermShare ?? "—"}%。应先区分搜索与非搜索定向，不能把关键词子集结论推广到全部投放。`, tableKey: "keywords", ...(unknownKeyword ? { target: { tableKey: "keywords", groupKey: unknownKeyword.key } } : {}) });
  }
  const actions: ReportAction[] = [];
  if (!current.coverage.complete) actions.push({ priority: "先补源", object: current.identity.shopName, evidence: `缺日：${current.coverage.missingDates.join("、")}`, change: "核对原自然计划的来源和导入结果，数据齐全前不作效果排名", metric: "日期覆盖/批次与五项源指标", observation: "下一次原自然同步后", rollback: "新批次口径变化时撤回旧结论", tableKey: "coverage" });
  if (current.coverage.complete && anonymousShare !== null && anonymousShare > 0) actions.push({ priority: "先核身份", object: "未识别计划来源行", evidence: `计划身份缺失花费占 ${anonymousShare}%`, change: "核对原始计划字段和导入映射；不按匿名桶做计划级预算调整", metric: "计划ID/名称覆盖与来源批次", observation: "下一次生成前", rollback: "身份未补齐则继续隐藏计划级结论", tableKey: "plans", ...(unknownPlan ? { target: { tableKey: "plans", groupKey: unknownPlan.key } } : {}) });
  if (current.coverage.complete && noKeywordShare !== null && noKeywordShare > 20) actions.push({ priority: "先分定向", object: "无关键词来源行", evidence: `无关键词行花费占 ${noKeywordShare}%`, change: "按营销场景和定向类型核对搜索与非搜索花费；仅对有词子集作关键词复核", metric: "有词/无词花费占比与词货归因", observation: "本周期复核后", rollback: "来源场景无法区分时不生成全店关键词效率结论", tableKey: "keywords", ...(unknownKeyword ? { target: { tableKey: "keywords", groupKey: unknownKeyword.key } } : {}) });
  if (comparable && currentOrderRate !== null && previousOrderRate !== null && currentOrderRate < previousOrderRate) {
    actions.push({ priority: "高：人工复核", object: "点击到归因订单行环节", evidence: `${previousOrderRate}%→${currentOrderRate}%`, change: "按花费前列计划核归因窗口、词匹配、商品与落地页变化；仅在可比且归因成熟后做人工小步试验", metric: "点击/归因订单行率/ROAS", observation: "至少7天并等待归因成熟", rollback: "样本不足或归因未成熟则停止判优；试验后可比效率继续下降时人工回退", tableKey: "plans" });
  }
  for (const plan of current.coverage.complete ? topPlans : []) {
    const clicks = plan.metrics.clicks, orders = plan.metrics.reportedOrderLines;
    if (clicks === null || orders === null || clicks < 30 || orders < 3) {
      actions.push({ priority: "观察", object: plan.name ?? plan.planId ?? "未提供计划", evidence: `点击 ${clicks ?? "缺字段"}、归因订单行 ${orders ?? "缺字段"}`, change: "样本量或归因成熟度不足；先核来源与继续观察，不据此停投或转移预算", metric: "点击/归因订单行/花费", observation: "至少7天或达到人工复核样本门槛", rollback: "字段或归因口径变化时撤回比较", tableKey: "plans", target: { tableKey: "plans", groupKey: plan.key } });
      continue;
    }
    const planRoas = ratio(plan.metrics.reportedGmvCents, plan.metrics.spendCents);
    const shopRoas = ratio(m.reportedGmvCents, m.spendCents);
    const spendShare = percent(plan.metrics.spendCents, m.spendCents);
    if (planRoas !== null && shopRoas !== null && shopRoas > 0 && spendShare !== null && spendShare >= 10 && planRoas < shopRoas * 0.8) {
      actions.push({ priority: "高：人工复核", object: plan.name ?? plan.planId ?? "未提供计划", evidence: `花费占 ${spendShare}%，归因ROAS ${rounded(planRoas)}，全店 ${rounded(shopRoas)}；点击 ${clicks}、订单行 ${orders}`, change: "核查词/搜索词、跟单SKU及落地页，再设计小比例人工试验；不据此直接停投", metric: "计划归因ROAS/点击到订单行率/花费占比", observation: "至少7天并等待归因成熟", rollback: "归因窗口或分组身份变化时撤回判断；试验劣于对照时人工回退", tableKey: "plans", target: { tableKey: "plans", groupKey: plan.key } });
    }
  }
  if (comparable) for (const tableKey of primaryKeys) {
    const kind = ({ plans: "计划", products: "商品", keywords: "关键词", searchTerms: "搜索词" })[tableKey];
    for (const pair of groupComparisons[tableKey]) {
      const group = pair.current ?? pair.previous;
      if (!group) continue;
      const target = { tableKey, groupKey: group.key };
      const label = group.name ?? group.id ?? stableIdentity(tableKey, group) ?? group.key;
      if (pair.state === "可比" && pair.current && pair.previous) {
        const now = pair.current.metrics, before = pair.previous.metrics;
        const currentRate = percent(now.reportedOrderLines, now.clicks);
        const previousRate = percent(before.reportedOrderLines, before.clicks);
        const currentShare = percent(now.spendCents, m.spendCents);
        const previousShare = percent(before.spendCents, previous!.summary.spendCents);
        if (now.clicks !== null && before.clicks !== null && now.clicks >= 30 && before.clicks >= 30
          && now.reportedOrderLines !== null && before.reportedOrderLines !== null
          && now.reportedOrderLines >= 3 && before.reportedOrderLines >= 3
          && currentShare !== null && previousShare !== null && currentShare >= 5 && previousShare >= 5
          && currentRate !== null && previousRate !== null && previousRate > 0
          && currentRate <= previousRate * 0.8 && previousRate - currentRate >= 2) {
          const evidence = `点击 ${before.clicks}→${now.clicks}、归因订单行 ${before.reportedOrderLines}→${now.reportedOrderLines}，点击到归因订单行率 ${previousRate}%→${currentRate}%`;
          findings.push({ title: `${kind}归因转化待复核：${label}`, text: `${evidence}。两期均达到观察样本门槛，仍须核对归因窗口、定向与商品变化；不能据此证明增量效果或利润下降。`, tableKey, target });
          actions.push({ priority: "高：人工复核", object: `${kind}：${label}`, evidence,
            change: "核对来源身份、归因成熟度、词货及落地页变化；仅在核实后设计小比例人工试验",
            metric: "点击/归因订单行率/归因ROAS", observation: "至少7天并等待归因成熟",
            rollback: "来源或归因口径变化时撤回结论；试验劣于对照时人工回退", tableKey, target });
        }
      } else if (pair.state === "新增" || pair.state === "消失") {
        const source = pair.state === "新增" ? pair.current : pair.previous;
        const periodSpend = pair.state === "新增" ? m.spendCents : previous!.summary.spendCents;
        const share = source ? percent(source.metrics.spendCents, periodSpend) : null;
        if (source && share !== null && share >= 10 && source.metrics.clicks !== null && source.metrics.clicks >= 30) {
          const evidence = `${pair.state}，${pair.state === "新增" ? "本期" : "前期"}花费占比 ${share}%、点击 ${source.metrics.clicks}`;
          findings.push({ title: `${kind}${pair.state}：${label}`, text: `${evidence}。仅表示该稳定身份在另一周期未出现；需核对改名、导入映射及定向变更，不能直接判定投放增减效果。`, tableKey, target });
          actions.push({ priority: "先核身份", object: `${kind}：${label}`, evidence,
            change: "核对原始身份字段及投放变更记录，确认是否真实新增或退出后再解释结构变化",
            metric: "对象身份/来源行/花费占比", observation: "本周期复核后",
            rollback: "发现改名或映射变化时撤回新增或消失判断", tableKey, target });
        }
      }
    }
  }
  if (comparable) for (const tableKey of ["keywords", "searchTerms"] as const) {
    const label = tableKey === "keywords" ? "关键词" : "搜索词";
    const observed = groupComparisons[tableKey].filter((pair) => pair.state === "可比" && pair.current && pair.previous
      && pair.current.metrics.spendCents !== null && pair.current.metrics.clicks !== null && pair.current.metrics.clicks >= 30
      && pair.previous.metrics.clicks !== null && pair.previous.metrics.clicks >= 30)
      .sort((left, right) => (right.current!.metrics.spendCents ?? 0) - (left.current!.metrics.spendCents ?? 0)).slice(0, 2);
    for (const pair of observed) {
      const now = pair.current!, before = pair.previous!;
      if (actions.some((item) => item.target?.tableKey === tableKey && item.target.groupKey === now.key)) continue;
      const evidence = `花费 ${money(now.metrics.spendCents)} 元；点击 ${before.metrics.clicks}→${now.metrics.clicks}，归因订单行 ${before.metrics.reportedOrderLines ?? "缺字段"}→${now.metrics.reportedOrderLines ?? "缺字段"}`;
      const target = { tableKey, groupKey: now.key };
      findings.push({ title: `${label}重点观察：${now.name ?? now.key}`, text: `${evidence}。这是有${label}身份的来源子集，两期点击达到观察门槛；订单行样本或归因成熟度仍需复核，不据此直接调预算。`, tableKey, target });
      actions.push({ priority: "观察：对象复核", object: `${label}：${now.name ?? now.key}`, evidence,
        change: "核对搜索意图、匹配方式、跟单SKU及归因窗口；保留小步人工试验空间，不据短周期直接停投",
        metric: "点击/归因订单行/匹配词货", observation: "至少7天并等待归因成熟",
        rollback: "身份或归因口径变化时撤回观察；试验不优于可比对照时人工回退", tableKey, target });
    }
  }
  if (!actions.length) actions.push({ priority: "例行复查", object: current.identity.shopName, evidence: "当前周期来源已对账", change: "复核花费前列对象及归因成熟度，保留现有策略待人工判断", metric: "CTR/点击到订单行率/ROAS", observation: "下一完整周期", rollback: "源版本变化时重新生成报告", tableKey: "plans" });
  const tables: ReportTable[] = [
    { key: "summary", title: "经营总览", note: "同一京准通推广口径；环比只在同店、等天数、两期完整且来源修订一致时展示。", columns: [
      { key: "metric", label: "指标", kind: "text" }, { key: "current", label: "本期", kind: "number" },
      { key: "previous", label: "前等长周期", kind: "number" }, { key: "change", label: "环比变化（%）", kind: "percent" },
      { key: "unit", label: "单位", kind: "text" },
    ], rows: [
      metricRow("推广花费", money(m.spendCents), p ? money(p.spendCents) : null, "元", comparable),
      metricRow("展现", m.impressions, p?.impressions ?? null, "次", comparable),
      metricRow("点击", m.clicks, p?.clicks ?? null, "次", comparable),
      metricRow("CTR", currentCtr, previousCtr, "%", comparable),
      metricRow("平均点击花费", rounded(ratio(m.spendCents, m.clicks) === null ? null : ratio(m.spendCents, m.clicks)! / 100), p ? rounded(ratio(p.spendCents, p.clicks) === null ? null : ratio(p.spendCents, p.clicks)! / 100) : null, "元", comparable),
      metricRow("归因订单行", m.reportedOrderLines, p?.reportedOrderLines ?? null, "行", comparable),
      metricRow("点击→归因订单行率", currentOrderRate, previousOrderRate, "%", comparable),
      metricRow("归因总订单金额", money(m.reportedGmvCents), p ? money(p.reportedGmvCents) : null, "元", comparable),
      metricRow("归因ROAS", currentRoas, previousRoas, "倍", comparable),
    ] },
    { key: "daily", title: "每日趋势", note: "前期按自然日序号对齐；不是同一自然日。缺日与缺字段留空。", columns: [
      { key: "date", label: "本期日期", kind: "text" }, { key: "spend", label: "花费（元）", kind: "money" },
      { key: "impressions", label: "展现", kind: "number" }, { key: "clicks", label: "点击", kind: "number" },
      { key: "orders", label: "归因订单行", kind: "number" }, { key: "gmv", label: "归因金额（元）", kind: "money" },
      { key: "previousDate", label: "前期日期", kind: "text" }, { key: "previousSpend", label: "前期花费（元）", kind: "money" },
      { key: "previousClicks", label: "前期点击", kind: "number" }, { key: "previousOrders", label: "前期订单行", kind: "number" },
    ], rows: current.daily.map((day, index) => [day.date, money(value(day.metrics, "spendCents")), day.metrics.impressions,
      day.metrics.clicks, day.metrics.reportedOrderLines, money(day.metrics.reportedGmvCents),
      comparable ? previous!.daily[index]!.date : null,
      comparable ? money(previous!.daily[index]!.metrics.spendCents) : null,
      comparable ? previous!.daily[index]!.metrics.clicks : null,
      comparable ? previous!.daily[index]!.metrics.reportedOrderLines : null]) },
    ...(["plans", "products", "keywords", "searchTerms", "keywordSku"] as const).map((key) => ({
      key, title: ({ plans: "计划诊断", products: "商品诊断", keywords: "关键词诊断", searchTerms: "搜索词诊断", keywordSku: "词货证据" })[key],
      note: key === "keywordSku"
        ? "同一来源事实的词货分组；未提供身份保留单独桶。比率先合计分子分母，再计算。"
        : "按稳定对象身份匹配前等长周期；新增/消失的另一侧留空，身份未知或无可比基线不算环比。率变化为百分点，其他环比为百分比。平台归因不代表净销售、利润或增量。",
      columns: key === "keywordSku" ? GROUP_COLUMNS : COMPARISON_COLUMNS,
      rows: key === "keywordSku" ? current.groups[key].map(metricGroup) : groupComparisons[key].map(comparisonRow),
    })),
    { key: "actions", title: "调整建议", note: "规则候选，待运营人员复核；无自动调价/投放。", columns: [
      { key: "priority", label: "优先级", kind: "text" }, { key: "object", label: "对象", kind: "text" },
      { key: "evidence", label: "依据", kind: "text" }, { key: "change", label: "建议核查或试验", kind: "text" },
      { key: "metric", label: "复查指标", kind: "text" }, { key: "observation", label: "观察期", kind: "text" },
      { key: "rollback", label: "停止/回退条件", kind: "text" },
    ], rows: actions.map((item) => [item.priority, item.object, item.evidence, item.change, item.metric, item.observation, item.rollback]) },
    { key: "coverage", title: "来源与口径", note: "日期/批次只证明所列来源；推广归因不是ERP净销售或利润。", columns: [
      { key: "date", label: "业务日", kind: "text" }, { key: "status", label: "本期覆盖", kind: "text" },
      { key: "rows", label: "来源行", kind: "number" }, { key: "batch", label: "来源批次", kind: "text" },
      { key: "batchOwner", label: "系统导入批次归属", kind: "text" },
      { key: "aggregateBatch", label: "聚合发布批次", kind: "text" },
      { key: "account", label: "来源账户昵称", kind: "text" },
      { key: "accountCoverage", label: "账户昵称行覆盖", kind: "text" },
    ], rows: currentDates.map((date) => {
      const source = current.sourceBatches.find((item) => item.date === date);
      return [date, source ? "已与系统聚合及批次对账" : "缺源", source?.rowCount ?? null,
        source?.batchIds.join("、") ?? null,
        source?.ownership.map((item) => `${item.status} · ${item.platform} · ${item.shopName} · 警告${item.warningCount}`).join("；") ?? null,
        source ? `${source.aggregateBatchId} · 警告${source.aggregateOwnership.warningCount}` : null,
        source?.accountNicknames.join("、") ?? null,
        source ? `${source.accountPresentRows}/${source.rowCount}` : null];
    }) },
  ];
  return {
    schemaVersion: "jd-promotion-report-v1", analysisType: "deterministic_review_draft",
    shopName: current.identity.shopName, period: current.period, previousPeriod: comparable ? previous!.period : null,
    complete: current.coverage.complete, comparisonAvailable: comparable, sourceRevision: current.sourceRevision,
    metrics: m, previousMetrics: p, coverage: current.coverage, findings, actions, tables, limitations,
  };
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}

function promotionDiagnosticRuntimeScript() {
  return String.raw`
(() => {
  const R = JSON.parse(document.getElementById('report').textContent);
  const $ = (id) => document.getElementById(id);
  const N = (tag, value) => {
    const node = ['svg', 'circle', 'polyline', 'text'].includes(tag)
      ? document.createElementNS('http://www.w3.org/2000/svg', tag)
      : document.createElement(tag);
    if (value !== undefined) node.textContent = String(value);
    return node;
  };
  const fmt = (value, kind) => value === null ? '—'
    : kind === 'money' ? '¥' + Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : kind === 'percent' ? Number(value).toFixed(2) + '%'
    : kind === 'ratio' ? Number(value).toFixed(2)
    : kind === 'number' ? Number(value).toLocaleString('zh-CN') : String(value);
  let selected = R.tables[0], page = 0, descending = true, focus = null, relation = null;
  const PAGE = 50;
  const status = N('div', R.complete
    ? '已覆盖完整 ' + R.coverage.requestedDates.length + ' 天 · 来源修订 ' + R.sourceRevision
    : '数据待补：' + R.coverage.missingDates.join('、') + '；不可视为完整周期');
  status.className = R.complete ? 'card' : 'warning';
  $('status').append(status);
  const metricCards = [
    ['推广花费', R.metrics.spendCents === null ? '—' : fmt(R.metrics.spendCents / 100, 'money')],
    ['展现', fmt(R.metrics.impressions, 'number')], ['点击', fmt(R.metrics.clicks, 'number')],
    ['归因订单行', fmt(R.metrics.reportedOrderLines, 'number')],
    ['归因总订单金额', R.metrics.reportedGmvCents === null ? '—' : fmt(R.metrics.reportedGmvCents / 100, 'money')],
  ];
  for (const [label, value] of metricCards) {
    const card = N('div'); card.className = 'card'; card.append(N('span', label), N('b', value)); $('kpis').append(card);
  }
  for (const finding of R.findings) {
    const card = N('article'); card.className = 'card'; card.append(N('h3', finding.title), N('p', finding.text));
    const button = N('button', '查看对应明细');
    button.onclick = () => show(R.tables.find((table) => table.key === (finding.target?.tableKey || finding.tableKey)) || R.tables[0], finding.target);
    card.append(button); $('findings').append(card);
  }
  for (const line of R.limitations) $('limits').append(N('li', line));
  function chart() {
    const rows = R.tables.find((table) => table.key === 'daily').rows;
    const values = rows.map((row) => row[1]);
    const max = Math.max(1, ...values.filter((value) => typeof value === 'number'));
    const svg = N('svg'); svg.setAttribute('viewBox', '0 0 680 220'); svg.setAttribute('class', 'chart');
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', '逐日推广花费');
    const points = [];
    rows.forEach((row, index) => {
      const x = 45 + (rows.length === 1 ? 0 : index * 590 / (rows.length-1));
      const value = row[1];
      if (typeof value === 'number') {
        const y = 175 - value / max * 135;
        points.push(x + ',' + y);
        const dot = N('circle'); dot.setAttribute('cx', String(x)); dot.setAttribute('cy', String(y));
        dot.setAttribute('r', '4'); dot.setAttribute('fill', '#17684e'); svg.append(dot);
      }
      const label = N('text', String(row[0]).slice(5)); label.setAttribute('x', String(x - 17));
      label.setAttribute('y', '205'); label.setAttribute('font-size', '11'); svg.append(label);
    });
    if (points.length) {
      const line = N('polyline'); line.setAttribute('points', points.join(' ')); line.setAttribute('fill', 'none');
      line.setAttribute('stroke', '#17684e'); line.setAttribute('stroke-width', '3'); svg.prepend(line);
    }
    $('chart').append(svg);
  }
  function related() {
    const area = $('related'); area.replaceChildren();
    if (focus !== null) {
      area.append(N('strong', '已定位来源键 ' + focus));
      const parts = JSON.parse(focus);
      const links = selected.key === 'plans' ? [
        ['planSku', 'planKey', focus, '此计划的跟单SKU'],
        ['planKeyword', 'planKey', focus, '此计划的关键词'],
      ] : selected.key === 'products' ? [
        ['keywordSku', 'skuId', parts[0], '此SKU关联关键词'],
        ['searchTermSku', 'skuId', parts[0], '此SKU关联搜索词'],
      ] : selected.key === 'keywords' ? [
        ['keywordSku', 'keyword', parts[0], '此关键词关联SKU'],
      ] : selected.key === 'searchTerms' ? [
        ['searchTermSku', 'searchTerm', parts[0], '此搜索词关联SKU'],
      ] : [];
      for (const [tableKey, columnKey, value, label] of links) {
        const next = R.tables.find((table) => table.key === tableKey);
        if (!next) continue;
        const button = N('button', label);
        button.onclick = () => show(next, null, { columnKey, value });
        area.append(button);
      }
    } else if (relation) {
      area.append(N('strong', '仅显示同一来源行关联的对象'));
    }
    if (focus !== null || relation) {
      const clear = N('button', '查看当前表全部'); clear.onclick = () => show(selected); area.append(clear);
    }
  }
  function show(table, target, relationFilter) {
    selected = table; page = 0; focus = target?.groupKey ?? null; relation = relationFilter ?? null;
    $('search').value = '';
    $('tabs').replaceChildren(...R.tables.map((item) => {
      const button = N('button', item.title); button.className = item.key === table.key ? 'active' : '';
      button.onclick = () => show(item); return button;
    }));
    $('note').textContent = table.note;
    $('sort').replaceChildren(N('option', '原始顺序'), ...table.columns.map((column, index) => {
      const option = N('option', column.label); option.value = String(index); return option;
    }));
    related(); refresh();
  }
  function selectedRows() {
    const query = $('search').value.trim().toLocaleLowerCase();
    const keyColumn = selected.columns.findIndex((column) => column.key === 'groupKey');
    const relationColumn = relation ? selected.columns.findIndex((column) => column.key === relation.columnKey) : -1;
    const rows = selected.rows.filter((row) =>
      (focus === null || keyColumn < 0 || row[keyColumn] === focus)
      && (!relation || relationColumn < 0 || row[relationColumn] === relation.value)
      && (!query || row.some((value) => value !== null && String(value).toLocaleLowerCase().includes(query))));
    const sort = $('sort').value;
    if (sort !== '') {
      const column = Number(sort);
      rows.sort((left, right) => {
        const a = left[column], b = right[column];
        if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
        const result = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), 'zh-CN');
        return descending ? -result : result;
      });
    }
    return rows;
  }
  function refresh() {
    const rows = selectedRows(), pageCount = Math.max(1, Math.ceil(rows.length / PAGE));
    page = Math.min(page, pageCount - 1);
    const header = N('tr'); selected.columns.forEach((column) => header.append(N('th', column.label)));
    if (selected.key === 'actions') header.append(N('th', '证据'));
    $('head').replaceChildren(header);
    const body = document.createDocumentFragment();
    for (const row of rows.slice(page * PAGE, (page + 1) * PAGE)) {
      const tr = N('tr');
      row.forEach((value, index) => {
        const td = N('td', fmt(value, selected.columns[index].kind));
        if (selected.columns[index].kind !== 'text') td.className = 'num';
        if (selected.key === 'chatInterpretation' && selected.columns[index].key === 'content') td.className = 'chat-text';
        tr.append(td);
      });
      if (selected.key === 'actions') {
        const action = R.actions[selected.rows.indexOf(row)];
        const cell = N('td'); const button = N('button', '定位');
        button.onclick = () => show(R.tables.find((table) => table.key === (action.target?.tableKey || action.tableKey)) || R.tables[0], action.target);
        cell.append(button); tr.append(cell);
      }
      body.append(tr);
    }
    $('body').replaceChildren(body);
    $('count').textContent = rows.length.toLocaleString() + ' / ' + selected.rows.length.toLocaleString()
      + ' 行 · 第 ' + (page + 1) + ' / ' + pageCount + ' 页';
    $('prev').disabled = page === 0; $('next').disabled = page + 1 >= pageCount;
  }
  $('search').oninput = () => { page = 0; refresh(); };
  $('sort').onchange = () => { page = 0; refresh(); };
  $('direction').onclick = () => { descending = !descending; $('direction').textContent = descending ? '降序' : '升序'; refresh(); };
  $('prev').onclick = () => { page--; refresh(); };
  $('next').onclick = () => { page++; refresh(); };
  $('csv').onclick = () => {
    const lines = [selected.columns.map((column) => column.label), ...selectedRows().map((row) => row.map((value) =>
      typeof value === 'string' && ['=', '+', '-', '@'].includes(value.trimStart()[0]) ? "'" + value : value === null ? '' : value))]
      .map((row) => row.map((value) => '"' + String(value).replaceAll('"', '""') + '"').join(','));
    const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob), anchor = N('a'); anchor.href = url; anchor.download = selected.title + '.csv';
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  chart(); show(selected); window.reportReady = true;
})();`;
}

/** Self-contained, offline HTML. Every visible number comes from the same table cells as XLSX. */
export function promotionDiagnosticHtml(report: PromotionDiagnosticReport) {
  const title = `${report.shopName} · ${report.period.startDate} 至 ${report.period.endDate} 推广深度诊断`;
  const mode = report.tables.some((table) => table.key === "chatInterpretation")
    ? "规则诊断与 AI 对话原文（文字未逐项核验） · 来源数值以确定性表为准 · 无自动投放操作"
    : report.tables.some((table) => table.key === "modelInterpretation")
      ? "规则诊断与单模型解释 · 来源与口径可核对 · 无自动投放操作"
      : "规则诊断草稿 · 来源与口径可核对 · 无自动投放操作";
  const encoded = JSON.stringify(report).replaceAll("<", "\\u003c").replaceAll("\u2028", "\\u2028").replaceAll("\u2029", "\\u2029");
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>${escapeHtml(title)}</title><style>
  :root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#173129;background:#f4f7f5}body{margin:0}header{background:#12382f;color:white;padding:32px max(20px,5vw)}h1{font-size:clamp(24px,4vw,38px);margin:8px 0}main{max-width:1380px;margin:auto;padding:24px}p{line-height:1.55}.muted{color:#65746e}.warning{background:#fff3d7;border:1px solid #e0ba62;padding:14px;border-radius:10px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card,section{background:white;border:1px solid #d8e3dd;border-radius:12px;padding:16px;margin:12px 0}.card b{font-size:23px;display:block}.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}button,input,select{font:inherit;padding:9px;border:1px solid #b7cac0;border-radius:7px;background:white}button{cursor:pointer}button:focus-visible,input:focus-visible{outline:2px solid #187f5a}nav{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0}.active{background:#17684e;color:white}.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #dce6e0;padding:9px;text-align:left;white-space:nowrap}th{background:#eef4f0;position:sticky;top:0}td.num{text-align:right;font-variant-numeric:tabular-nums}.pager{display:flex;justify-content:space-between;gap:8px;align-items:center}.chart{width:100%;max-height:220px}.badge{display:inline-block;border-radius:100px;background:#e9f5ec;padding:3px 9px;margin-right:6px}@media(max-width:600px){main{padding:12px}header{padding:22px 14px}}
  </style></head><body><header><small>BUSINESS REVIEW · 京东推广</small><h1>${escapeHtml(title)}</h1><p>${escapeHtml(mode)}</p></header><main><div id="status"></div><div id="kpis" class="grid"></div><section><h2>经营判断与复查方向</h2><div id="findings"></div></section><section><h2>逐日推广花费（元）</h2><div id="chart"></div></section><section><h2>明细与行动</h2><nav id="tabs"></nav><p id="note" class="muted"></p><div id="related" class="toolbar"></div><div class="toolbar"><input id="search" placeholder="搜索当前表" aria-label="搜索当前表"><select id="sort" aria-label="排序列"></select><button id="direction">降序</button><button id="csv">导出当前表CSV</button></div><div class="tablewrap"><table><thead id="head"></thead><tbody id="body"></tbody></table></div><div class="pager"><span id="count"></span><span><button id="prev">上一页</button> <button id="next">下一页</button></span></div></section><section><h2>口径与限制</h2><ul id="limits"></ul></section></main><script type="application/json" id="report">${encoded}</script><script>
  ${promotionDiagnosticRuntimeScript()}
  </script></body></html>`.replace("</style></head>", ".chat-text{white-space:pre-wrap;overflow-wrap:anywhere;max-width:850px;line-height:1.55}</style></head>");
}

export function promotionDiagnosticXlsx(report: PromotionDiagnosticReport) {
  const sheets: XlsxOutputSheet[] = report.tables.map((table) => ({
    name: table.title.slice(0, 25),
    rows: [[...table.columns.map((column) => column.label)], ...table.rows],
    headerStyle: true,
    freezeHeader: true,
    autoFilter: true,
    columnWidths: table.columns.map((column) => column.kind === "text" ? 28 : 18),
    columnKinds: table.columns.map((column) => column.kind),
  }));
  return createXlsxWorkbookBytes(sheets);
}
