/** Client checks for a read-only market sample. These do not grant source authority. */
export type MarketSource = { key: string; domain: string; query: Record<string, string> };
export type MarketPair = { current: MarketSource; baseline: MarketSource };
export type MarketSelector = {
  priceBandSourceKey: string; rankCurrentSourceKey: string; rankBaselineKey: string;
  currentObservationDate: string; baselineObservationDate: string;
  bands: { key: string; lowerCents: number; upperExclusiveCents: number | null }[];
};
export type MarketPreviewColumn = { key: string; label: string; kind: string };
export type MarketPreviewTable = { view: string; title: string; rowCount: number; columns: MarketPreviewColumn[] };
export type MarketPreview = {
  schemaVersion: string; reportId: string; sourceReportId: string; workflowStatus: string; pauseReason: string;
  marketManifestDigest: string; observationCoverage: unknown; tables: MarketPreviewTable[];
  page: null | { view: string; offset: number; limit: number; total: number; columns: MarketPreviewColumn[]; rows: unknown[][] };
  resultDigest: string;
  materialAdmitted: false; agentReadPersisted: false; actualAgentBound: false; authorityVerified: false;
  renderer: false; requestMaterialReplayed: true; marketTopSampleOnly: true;
  priceSummaryAndMembersAdditive: false; marketAndOwnSalesAdditive: false;
};
const id = /^[A-Za-z0-9_-]{1,160}$/;
const sha = /^[a-f0-9]{64}$/;
export const MARKET_V2_VIEWS = ["price_band_summary", "price_band_members", "rank_entry_exit"] as const;
export type MarketView = typeof MARKET_V2_VIEWS[number];

function fail(message = "市场样本预览回执与当前报告或页不一致，请重新读取。"): never { throw new Error(message); }
function record(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
function nonnegative(value: unknown): value is number { return Number.isSafeInteger(value) && (value as number) >= 0; }
function columns(value: unknown): value is MarketPreviewColumn[] {
  return Array.isArray(value) && value.length > 0 && value.length <= 40 && value.every(column => record(column)
    && typeof column.key === "string" && column.key.length > 0 && column.key.length <= 100
    && typeof column.label === "string" && column.label.length > 0 && column.label.length <= 200
    && typeof column.kind === "string" && column.kind.length > 0 && column.kind.length <= 40);
}
const identity = (query: Record<string, string>) => JSON.stringify(Object.entries(query)
  .filter(([key]) => key !== "window").sort(([a], [b]) => a.localeCompare(b)));

/** Both sources must come from one sealed directory and share exact original query fields. */
export function marketPairs(sources: readonly MarketSource[]): MarketPair[] {
  if (!Array.isArray(sources) || sources.length > 48) fail("市场来源目录超出固定容量，请刷新。");
  const keys = new Set<string>();
  for (const source of sources) {
    if (!source || !id.test(source.key) || keys.has(source.key) || typeof source.domain !== "string"
      || !record(source.query) || Object.values(source.query).some(value => typeof value !== "string"))
      fail("市场来源目录身份无效，请刷新。");
    keys.add(source.key);
  }
  const current = sources.filter(source => source.domain === "market" && source.query.platform === "京东" && source.query.window === "current");
  const baseline = sources.filter(source => source.domain === "market" && ["previous", "yearAgo"].includes(source.query.window));
  return current.flatMap(now => baseline.filter(previous => previous.key !== now.key
    && identity(previous.query) === identity(now.query)).map(previous => ({ current: now, baseline: previous })));
}

export function marketPriceBoundaryCents(value: string): number {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?$/.test(value))
    fail("价格分界须为不超过两位小数的非负元金额。");
  const [yuan, fraction = ""] = value.split(".");
  const cents = Number(yuan) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(cents) || cents <= 0) fail("价格分界须大于 0 元。");
  return cents;
}

/** Same indexed day in the preceding equal-length window, or clamped prior-year day. */
export function correspondingMarketBaselineDate(currentDate: string, startDate: string, endDate: string, window: string): string {
  const iso = (value: string) => typeof value === "string" && /^(?:19|20)\d{2}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
  if (!iso(currentDate) || !iso(startDate) || !iso(endDate) || startDate > currentDate || currentDate > endDate
    || !["previous", "yearAgo"].includes(window)) fail("市场观察日或比较窗口无效。");
  const start = new Date(`${startDate}T00:00:00Z`), end = new Date(`${endDate}T00:00:00Z`);
  const current = new Date(`${currentDate}T00:00:00Z`);
  const days = Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
  if (days < 1 || days > 93) fail("市场比较窗口必须为 1—93 天。");
  if (window === "previous") {
    current.setUTCDate(current.getUTCDate() - days);
    return current.toISOString().slice(0, 10);
  }
  const year = current.getUTCFullYear() - 1, month = current.getUTCMonth();
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return new Date(Date.UTC(year, month, Math.min(current.getUTCDate(), lastDay))).toISOString().slice(0, 10);
}

export function marketSelector(pair: MarketPair, currentDate: string, baselineDate: string, boundaryYuan: string): MarketSelector {
  const known = marketPairs([pair.current, pair.baseline]);
  if (known.length !== 1) fail("本期与基期市场来源不匹配。");
  const expectedBaseline = correspondingMarketBaselineDate(currentDate, pair.current.query.startDate,
    pair.current.query.endDate, pair.baseline.query.window);
  if (baselineDate !== expectedBaseline)
    fail("基期观察日必须是本期观察日按固定比较规则对应的日期。");
  const boundary = marketPriceBoundaryCents(boundaryYuan);
  return { priceBandSourceKey: pair.current.key, rankCurrentSourceKey: pair.current.key,
    rankBaselineKey: pair.baseline.key, currentObservationDate: currentDate, baselineObservationDate: baselineDate,
    bands: [{ key: "below_boundary", lowerCents: 0, upperExclusiveCents: boundary },
      { key: "at_or_above_boundary", lowerCents: boundary, upperExclusiveCents: null }] };
}

export function validateParkedCreate(raw: unknown, sourceReportId: string) {
  if (!record(raw) || !record(raw.item) || !id.test(sourceReportId) || !id.test(String(raw.item.id ?? ""))
    || raw.item.workflowStatus !== "paused" || raw.item.pauseReason !== "market_material_not_admitted"
    || raw.item.reportGenerationSupported !== false || raw.item.agentDispatchSupported !== false
    || raw.item.marketMaterialReady !== false || typeof raw.replayed !== "boolean"
    || raw.materialAdmitted !== false || raw.agentReadPersisted !== false || raw.actualAgentBound !== false
    || raw.authorityVerified !== false || raw.renderer !== false || raw.requestMaterialReplayed !== true)
    fail("市场停放报告创建回执无效，不能作为正式报告。");
  return raw.item.id as string;
}

export function validateMarketPreview(raw: unknown, expected: { reportId: string; sourceReportId: string; view?: MarketView; offset?: number }): MarketPreview {
  if (!record(raw) || raw.schemaVersion !== "business-market-v2-parked-preview-v1"
    || raw.reportId !== expected.reportId || raw.sourceReportId !== expected.sourceReportId
    || raw.workflowStatus !== "paused" || raw.pauseReason !== "market_material_not_admitted"
    || typeof raw.marketManifestDigest !== "string" || !sha.test(raw.marketManifestDigest)
    || typeof raw.resultDigest !== "string" || !sha.test(raw.resultDigest)
    || !record(raw.observationCoverage) || !Array.isArray(raw.tables) || raw.tables.length !== 3
    || raw.materialAdmitted !== false || raw.agentReadPersisted !== false || raw.actualAgentBound !== false
    || raw.authorityVerified !== false || raw.renderer !== false || raw.requestMaterialReplayed !== true
    || raw.marketTopSampleOnly !== true || raw.priceSummaryAndMembersAdditive !== false
    || raw.marketAndOwnSalesAdditive !== false) fail();
  const byView = new Map<string, MarketPreviewTable>();
  for (const table of raw.tables) {
    if (!record(table) || !MARKET_V2_VIEWS.includes(table.view as MarketView) || byView.has(table.view as string)
      || typeof table.title !== "string" || !table.title || !nonnegative(table.rowCount) || !columns(table.columns)) fail();
    byView.set(table.view as string, table as MarketPreviewTable);
  }
  if (byView.size !== 3) fail();
  if (expected.view === undefined) { if (raw.page !== null) fail(); }
  else {
    const page = raw.page;
    if (!record(page)) fail();
    const pageOffset = page.offset, pageTotal = page.total, pageColumns = page.columns, rows = page.rows;
    if (page.view !== expected.view || !nonnegative(pageOffset) || pageOffset !== expected.offset || page.limit !== 20
      || !nonnegative(pageTotal) || pageTotal !== byView.get(expected.view)?.rowCount
      || !columns(pageColumns) || JSON.stringify(pageColumns) !== JSON.stringify(byView.get(expected.view)?.columns)
      || !Array.isArray(rows) || rows.length > 20 || pageOffset + rows.length > pageTotal
      || rows.some((row: unknown) => !Array.isArray(row) || row.length !== pageColumns.length)
      || rows.length !== Math.min(20, pageTotal - pageOffset)) fail();
  }
  return raw as MarketPreview;
}
