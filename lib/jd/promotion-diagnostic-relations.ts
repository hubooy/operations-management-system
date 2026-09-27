import type {
  DiagnosticGroup,
  DiagnosticPeriod,
  PromotionDiagnosticReport,
  ReportColumn,
  ReportTable,
} from "./promotion-diagnostic-report";

type RelationKind = "planSku" | "planKeyword" | "searchTermSku";
type RelatedGroup = DiagnosticGroup & { planKey?: string };
export type DiagnosticWithRelations = DiagnosticPeriod & {
  groups: DiagnosticPeriod["groups"] & Record<RelationKind, RelatedGroup[]>;
};
export type RelationLink = { tableKey: string; columnKey: string; value: string | null; label: string };

const METRIC_COLUMNS: ReportColumn[] = [
  { key: "rowCount", label: "来源行", kind: "number" },
  { key: "spend", label: "花费（元）", kind: "money" },
  { key: "impressions", label: "展现", kind: "number" },
  { key: "clicks", label: "点击", kind: "number" },
  { key: "orders", label: "归因订单行", kind: "number" },
  { key: "gmv", label: "归因总订单金额（元）", kind: "money" },
  { key: "roas", label: "归因ROAS", kind: "ratio" },
  { key: "groupKey", label: "分组身份（来源键）", kind: "text" },
];

function metricCells(group: DiagnosticGroup) {
  const metrics = group.metrics;
  return [group.rowCount, metrics.spendCents === null ? null : metrics.spendCents / 100,
    metrics.impressions, metrics.clicks, metrics.reportedOrderLines,
    metrics.reportedGmvCents === null ? null : metrics.reportedGmvCents / 100,
    metrics.spendCents && metrics.reportedGmvCents !== null
      ? Math.round(metrics.reportedGmvCents / metrics.spendCents * 100) / 100 : null,
    group.key];
}

function relationTable(
  key: RelationKind,
  title: string,
  identityColumns: ReportColumn[],
  groups: RelatedGroup[],
  identities: (group: RelatedGroup) => Array<string | null>,
): ReportTable {
  return {
    key, title,
    note: "只表示同一来源行中确实同时出现的身份；与其他视角不可相加。空身份保留独立桶。",
    columns: [...identityColumns, ...METRIC_COLUMNS],
    rows: groups.map((group) => [...identities(group), ...metricCells(group)]),
  };
}

export function appendPromotionRelations(report: PromotionDiagnosticReport, current: DiagnosticPeriod): PromotionDiagnosticReport {
  const source = current as DiagnosticWithRelations;
  for (const name of ["planSku", "planKeyword", "searchTermSku"] as const) {
    const groups = source.groups[name];
    if (!Array.isArray(groups) || groups.reduce((sum, group) => sum + group.rowCount, 0) !== current.coverage.rowCount
      || new Set(groups.map((group) => group.key)).size !== groups.length) {
      throw new Error(`推广关系 ${name} 未与来源行对平`);
    }
  }
  const planKeys = new Set(current.groups.plans.map((group) => group.key));
  if ([...source.groups.planSku, ...source.groups.planKeyword].some((group) => !group.planKey || !planKeys.has(group.planKey))) {
    throw new Error("推广计划关系引用了不存在的计划");
  }
  const keywordSku = report.tables.find((table) => table.key === "keywordSku");
  if (!keywordSku) throw new Error("推广报告缺少词货证据表");
  const keywordSkuTable: ReportTable = {
    ...keywordSku,
    columns: [
      { key: "keyword", label: "关键词", kind: "text" },
      { key: "skuId", label: "跟单SKU", kind: "text" },
      ...METRIC_COLUMNS,
    ],
    rows: current.groups.keywordSku.map((group) => [group.keyword ?? null, group.skuId ?? null, ...metricCells(group)]),
  };
  const relations = [
    relationTable("planSku", "计划关联SKU", [
      { key: "planKey", label: "计划来源键", kind: "text" },
      { key: "skuId", label: "跟单SKU", kind: "text" },
    ], source.groups.planSku, (group) => [group.planKey ?? null, group.skuId ?? null]),
    relationTable("planKeyword", "计划关联关键词", [
      { key: "planKey", label: "计划来源键", kind: "text" },
      { key: "keyword", label: "关键词", kind: "text" },
    ], source.groups.planKeyword, (group) => [group.planKey ?? null, group.keyword ?? null]),
    relationTable("searchTermSku", "搜索词关联SKU", [
      { key: "searchTerm", label: "搜索词", kind: "text" },
      { key: "skuId", label: "跟单SKU", kind: "text" },
    ], source.groups.searchTermSku, (group) => [group.searchTerm ?? null, group.skuId ?? null]),
  ];
  return {
    ...report,
    tables: report.tables.flatMap((table) => table.key === "keywordSku" ? [keywordSkuTable, ...relations] : [table]),
  };
}

export function linksForPromotionTarget(current: DiagnosticPeriod, tableKey: string, groupKey: string): RelationLink[] {
  const group = current.groups[tableKey as keyof DiagnosticPeriod["groups"]]?.find((item) => item.key === groupKey);
  if (!group) return [];
  if (tableKey === "plans") return [
    { tableKey: "planSku", columnKey: "planKey", value: group.key, label: "此计划的跟单SKU" },
    { tableKey: "planKeyword", columnKey: "planKey", value: group.key, label: "此计划的关键词" },
  ];
  if (tableKey === "products") return [
    { tableKey: "keywordSku", columnKey: "skuId", value: group.skuId ?? null, label: "此SKU关联关键词" },
    { tableKey: "searchTermSku", columnKey: "skuId", value: group.skuId ?? null, label: "此SKU关联搜索词" },
  ];
  if (tableKey === "keywords") return [
    { tableKey: "keywordSku", columnKey: "keyword", value: group.keyword ?? null, label: "此关键词关联SKU" },
  ];
  if (tableKey === "searchTerms") return [
    { tableKey: "searchTermSku", columnKey: "searchTerm", value: group.searchTerm ?? null, label: "此搜索词关联SKU" },
  ];
  return [];
}
