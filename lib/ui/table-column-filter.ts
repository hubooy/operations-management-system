export function normalizeTableCellValue(value: string) {
  return value.replace(/\s+/g, " ").trim().slice(0, 180);
}

function parseDisplayedCount(value: string | undefined) {
  if (!value) return null;
  const parsed = Number(value.replace(/,/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

/** Detects server paging/truncation summaries without relying on DOM rows. */
export function tableSummaryShowsPartialDataset(summary: string, renderedRows: number) {
  const normalized = summary.replace(/\s+/g, " ").trim();
  if (/(?:展示|显示)(?:数据)?前\s*[\d,]+|(?:仅展示|仅显示)\s*[\d,]+|当前页/.test(normalized)) return true;

  const ratio = /(?:显示|已加载|本页)\s*([\d,]+)(?:\s*(?:条|项|行))?\s*\/\s*(?:共\s*)?([\d,]+)/.exec(normalized);
  const returned = parseDisplayedCount(ratio?.[1]);
  const available = parseDisplayedCount(ratio?.[2]);
  if (returned !== null && available !== null && available > returned) return true;

  const reportedTotals = [...normalized.matchAll(/(?:优先处理|符合条件|共计|合计|总计|共)\s*([\d,]+)\s*(?:条|项|行|个|款|件|种|家|笔)/g)]
    .map((match) => parseDisplayedCount(match[1]))
    .filter((value): value is number => value !== null);
  return reportedTotals.some((total) => total > renderedRows);
}

const columnFilterConcepts = [
  ["product", ["货品", "商品", "产品", "sku", "spu", "规格代码", "商品id"]],
  ["shop", ["店铺", "网店", "商城"]],
  ["platform", ["平台", "渠道"]],
  ["category", ["品类", "类目", "分类"]],
  ["ranking_scope", ["榜单范围", "榜单口径", "ranking scope"]],
  ["warehouse", ["仓库", "rdc", "dc"]],
  ["brand", ["品牌"]],
  ["supplier", ["供应商"]],
  ["owner", ["责任人", "负责人", "跟进人", "客服"]],
  ["status", ["状态", "风险", "进度"]],
  ["period", ["月份", "月度", "日期", "时间", "周期"]],
  ["source", ["来源", "数据源"]],
] as const;

function normalizeFilterControlLabel(value: string) {
  return normalizeTableCellValue(value)
    .toLocaleLowerCase("zh-CN")
    .replace(/，打开(?:全量)?列筛选/g, "")
    .replace(/[（(][^）)]*[）)]/g, "")
    .replace(/(?:可多选|全部清除|全部|搜索|筛选|选择|用于|按)/g, "")
    .replace(/[\s/／·:：、,，\-—_]+/g, "");
}

function filterConcepts(value: string) {
  const normalized = normalizeFilterControlLabel(value);
  const concepts = new Set<string>();
  for (const [concept, aliases] of columnFilterConcepts) {
    if (aliases.some((alias) => normalized.includes(alias))) concepts.add(concept);
  }
  return concepts;
}

/**
 * Scores an existing business filter against a table header. A positive score
 * means the control can replace the page-local column filter. Exact and
 * distinctive label matches intentionally outrank broad search controls.
 */
export function scoreTableFilterControl(headerLabel: string, controlLabel: string) {
  const control = normalizeFilterControlLabel(controlLabel);
  if (!control) return 0;
  const normalizedHeaderLabel = normalizeFilterControlLabel(headerLabel);
  if (/^(?:(?:sku|spu|商品|货品|产品)?(?:主图|图片|图))$/.test(normalizedHeaderLabel)) return 0;
  const headerParts = [headerLabel, ...headerLabel.split(/[\/／·]/)]
    .map((value) => ({ raw: value, normalized: normalizeFilterControlLabel(value) }))
    .filter((value, index, values) => value.normalized && values.findIndex((candidate) => candidate.normalized === value.normalized) === index);
  const controlConcepts = filterConcepts(controlLabel);
  return Math.max(0, ...headerParts.map(({ raw, normalized: header }) => {
    if (header === control) return 120;
    let score = 0;
    const headerConcepts = filterConcepts(raw);
    const sharedConcepts = [...headerConcepts].filter((concept) => controlConcepts.has(concept));
    // Context prefixes such as "库龄仓库" must not make the unrelated
    // "库龄" metric look filterable. Partial-label matching is only safe when
    // both labels also describe the same business dimension.
    if (sharedConcepts.length > 0 && (header.includes(control) || control.includes(header))) {
      score += 72 + Math.min(18, Math.min(header.length, control.length));
    }
    for (const concept of sharedConcepts) {
      score += concept === "status" ? 18 : 32;
    }
    // A generic control that happens to mention many dimensions (usually a
    // keyword search box) remains a fallback behind a dedicated dropdown.
    if (controlConcepts.size > 2) score -= (controlConcepts.size - 2) * 7;
    return Math.max(0, score);
  }));
}

export function tableRowMatchesColumnFilters(
  values: readonly (string | readonly string[])[],
  filters: ReadonlyMap<number, ReadonlySet<string>>,
) {
  for (const [columnIndex, selectedValues] of filters) {
    const value = values[columnIndex] ?? "";
    const candidates = Array.isArray(value) ? value : [value];
    if (selectedValues.size > 0 && !candidates.some((candidate) => selectedValues.has(candidate))) return false;
  }
  return true;
}
