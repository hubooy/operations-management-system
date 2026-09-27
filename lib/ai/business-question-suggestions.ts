/** Deterministic hints for the workbench. This module never grants source authority. */
export type QuestionPurpose = "promotion" | "sku" | "spu" | "b2b" | "market";
export type QuestionWindow = "current" | "previous" | "yearAgo";
export type QuestionDateRange = { startDate: string; endDate: string };
export type QuestionShop = { platform: string; shop: string };
export type QuestionMarket = { platform: string; category: string; scope: string; rankingDimension: string; priceBandFilter: string };
export type QuestionSourceScalar =
  | { domain: "netshop"; platform: string; shop: string; dataset: "promotion" | "sku" | "spu" | "b2b" | "master" }
  | { domain: "sales"; platform: string; shop: string; channel: string }
  | ({ domain: "market" } & QuestionMarket);
export type QuestionSuggestionInput = {
  question: string;
  /** An explicit date produced in Asia/Shanghai, never inferred from Date.now(). */
  shanghaiToday: string;
  form: { startDate: string; endDate: string; windows: readonly string[]; shops: readonly QuestionShop[]; markets: readonly QuestionMarket[] };
  /** Exact scalar identities from previously validated owning-directory pages. */
  directory: readonly QuestionSourceScalar[];
};
export type QuestionSuggestion = {
  schemaVersion: "business-question-suggestion-v1";
  authorityVerified: false;
  requiresUserConfirmation: true;
  dates: QuestionDateRange | null;
  dateBasis: "explicit_question" | "last_30_complete_days" | "current_form" | "unresolved";
  windows: QuestionWindow[];
  purposes: QuestionPurpose[];
  shopCandidates: QuestionShop[];
  marketCandidates: QuestionMarket[];
  unresolved: string[];
  missingSources: string[];
  comparison: { previousEqualLength: QuestionDateRange | null; previousMonthSameDates: QuestionDateRange | null; note: string };
};

const encoder = new TextEncoder();
const datePattern = /^\d{4}-\d{2}-\d{2}$/;
const windows: QuestionWindow[] = ["current", "previous", "yearAgo"];
const purposes: QuestionPurpose[] = ["promotion", "sku", "spu", "b2b", "market"];
const exactRange = /(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|～|—|－|\s+-\s+)\s*(\d{4}-\d{2}-\d{2})/u;
const dateToken = /\d{4}-\d{2}-\d{2}/gu;

function requireText(value: unknown, maxPoints: number, label: string): asserts value is string {
  if (typeof value !== "string" || !value || [...value].length > maxPoints || /[\p{Cc}\p{Cs}]/u.test(value))
    throw new Error(`${label}无效`);
}
function date(value: unknown): Date | null {
  if (typeof value !== "string" || !datePattern.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
    && parsed.getUTCFullYear() >= 2000 && parsed.getUTCFullYear() <= 2098 ? parsed : null;
}
function iso(value: Date): string { return value.toISOString().slice(0, 10); }
function shift(value: Date, days: number): Date { const copy = new Date(value); copy.setUTCDate(copy.getUTCDate() + days); return copy; }
function monthBefore(value: Date): Date {
  const year = value.getUTCFullYear(), month = value.getUTCMonth();
  const target = new Date(Date.UTC(year, month - 1, 1));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(value.getUTCDate(), last));
  return target;
}
function validRange(first: string, last: string, today: Date): QuestionDateRange | null {
  const a = date(first), b = date(last);
  if (!a || !b || a > b || b >= today || (b.getTime() - a.getTime()) / 86400000 + 1 > 93) return null;
  return { startDate: first, endDate: last };
}
function shopKey(value: QuestionShop): string { return JSON.stringify([value.platform, value.shop]); }
function marketKey(value: QuestionMarket): string {
  return JSON.stringify([value.platform, value.category, value.scope, value.rankingDimension, value.priceBandFilter]);
}
function checkedScalar(value: QuestionSourceScalar): QuestionSourceScalar {
  if (!value || typeof value !== "object" || !["netshop", "sales", "market"].includes(value.domain)) throw new Error("来源目录标量无效");
  const keys = value.domain === "netshop" ? ["domain", "platform", "shop", "dataset"]
    : value.domain === "sales" ? ["domain", "platform", "shop", "channel"]
      : ["domain", "platform", "category", "scope", "rankingDimension", "priceBandFilter"];
  if (Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) throw new Error("来源目录标量字段无效");
  for (const key of keys.slice(1)) requireText((value as unknown as Record<string, unknown>)[key], 200, "来源目录标量");
  if (value.domain === "netshop" && !["promotion", "sku", "spu", "b2b", "master"].includes(value.dataset)) throw new Error("来源目录数据集无效");
  return { ...value };
}
function mention(question: string, label: string): string | null {
  const found = question.match(new RegExp(`(?:${label})[：:]\\s*[“\"'‘]?([^，。；;\\s“”\"'‘’]+)[”\"'’]?`, "u"));
  return found?.[1] ?? null;
}
function unique<T>(items: readonly T[], key: (value: T) => string): T[] {
  return [...new Map(items.map(item => [key(item), item])).values()];
}

export function suggestBusinessQuestionScope(input: QuestionSuggestionInput): QuestionSuggestion {
  if (!input || typeof input !== "object") throw new Error("候选输入无效");
  requireText(input.question, 1000, "分析问题");
  if (encoder.encode(input.question).length > 4000) throw new Error("分析问题超过4000 UTF-8字节");
  const today = date(input.shanghaiToday);
  if (!today) throw new Error("上海业务日期无效");
  if (!input.form || !Array.isArray(input.form.windows) || !Array.isArray(input.form.shops) || !Array.isArray(input.form.markets)
    || input.form.windows.length > 3 || input.form.shops.length > 4 || input.form.markets.length > 7
    || !Array.isArray(input.directory) || input.directory.length > 1000) throw new Error("当前范围或来源目录无效");
  const directory = input.directory.map(checkedScalar);
  if (encoder.encode(JSON.stringify(directory)).length > 131072) throw new Error("来源目录标量超过容量");
  for (const item of input.form.shops) { requireText(item.platform, 100, "平台"); if (item.shop) requireText(item.shop, 100, "店铺"); }
  for (const item of input.form.markets) for (const value of Object.values(item)) requireText(value, 200, "市场条件");
  if (input.form.windows.some(value => !windows.includes(value as QuestionWindow))) throw new Error("当前比较窗口无效");

  const question = input.question;
  const unresolved: string[] = [], missingSources: string[] = [];
  const tokens = [...question.matchAll(dateToken)].map(match => match[0]);
  const explicit = question.match(exactRange);
  let dates: QuestionDateRange | null = null;
  let dateBasis: QuestionSuggestion["dateBasis"] = "unresolved";
  if (tokens.length) {
    if (tokens.length === 2 && explicit && tokens[0] === explicit[1] && tokens[1] === explicit[2]) {
      dates = validRange(explicit[1], explicit[2], today);
      if (dates) dateBasis = "explicit_question";
      else unresolved.push("明确日期须是过去完整的连续1—93天，且位于2000—2098年。");
      if (question.includes("近30天")) unresolved.push("问题同时写了明确日期和近30天；以明确日期形成候选，请人工确认。");
    } else unresolved.push("问题中的日期未构成唯一的YYYY-MM-DD起止区间，请人工确认。");
  } else if (question.includes("近30天")) {
    dates = validRange(iso(shift(today, -30)), iso(shift(today, -1)), today);
    if (dates) dateBasis = "last_30_complete_days";
    else unresolved.push("近30天超出可分析日期边界，请明确选择历史日期。");
    if (/(?:今天|今日)/u.test(question)) unresolved.push("问题同时要求近30个完整自然日和今天；今日来源覆盖尚未核验，本候选仅截至昨天，请人工确认。");
  } else {
    dates = validRange(input.form.startDate, input.form.endDate, today);
    if (dates) dateBasis = "current_form";
    else unresolved.push("没有明确的完整历史日期范围；请在工作台确认起止日期。");
    if (/(?:今天|今日|昨天|昨日|本月|上月|上周|最近\d+天|近\d+天)/u.test(question)) {
      dates = null; dateBasis = "unresolved";
      unresolved.push("问题含尚未支持的相对日期说法，请明确选择日期。");
    }
  }

  const selectedWindows = new Set<QuestionWindow>(["current", ...input.form.windows as QuestionWindow[]]);
  if (question.includes("环比")) selectedWindows.add("previous");
  if (question.includes("同比")) selectedWindows.add("yearAgo");
  const purposeSet = new Set<QuestionPurpose>();
  if (/(?:推广|投放|广告|关键词|搜索词|SPU|SKU)/iu.test(question)) {
    if (/(?:推广|投放|广告|关键词|搜索词)/u.test(question)) purposeSet.add("promotion");
    if (/SKU/iu.test(question)) purposeSet.add("sku");
    if (/SPU/iu.test(question)) purposeSet.add("spu");
  }
  if (/(?:B\s*端|B2B|企业购|批发)/iu.test(question)) purposeSet.add("b2b");
  if (/(?:市场|行业|竞品|榜单)/u.test(question)) purposeSet.add("market");
  if (!purposeSet.size) unresolved.push("问题未明确推广、SKU、SPU、B端或市场的数据目的，请人工确认。");

  const directoryShops = unique(directory.filter(item => item.domain !== "market")
    .map(item => ({ platform: item.platform, shop: item.shop })), shopKey);
  const labelledShop = mention(question, "店铺|网店");
  const shops = labelledShop
    ? directoryShops.filter(item => item.shop === labelledShop)
    : input.form.shops.filter(item => item.shop).flatMap(item => directoryShops.filter(source => shopKey(source) === shopKey(item)));
  const shopCandidates = unique(shops, shopKey);
  if (labelledShop && !shops.length) unresolved.push("问题里的店铺名称没有匹配到已验证来源目录的精确身份。");
  if (labelledShop && shops.length > 1) unresolved.push("同名店铺对应多个平台，须人工选择精确平台和店铺。");
  if (!labelledShop && input.form.shops.some(item => item.shop && !directoryShops.some(source => shopKey(source) === shopKey(item))))
    unresolved.push("当前表单店铺尚未匹配已验证的来源目录。");
  const directoryMarkets = unique(directory.filter((item): item is Extract<QuestionSourceScalar, {domain: "market"}> => item.domain === "market")
    .map(({ platform, category, scope, rankingDimension, priceBandFilter }) => ({ platform, category, scope, rankingDimension, priceBandFilter })), marketKey);
  const labelledCategory = mention(question, "类目|品类");
  const markets = labelledCategory
    ? directoryMarkets.filter(item => item.category === labelledCategory)
    : input.form.markets.flatMap(item => directoryMarkets.filter(source => marketKey(source) === marketKey(item)));
  const marketCandidates = unique(markets, marketKey);
  if (labelledCategory && !markets.length) unresolved.push("问题里的类目名称没有匹配到已验证市场目录的精确条件。");
  if (labelledCategory && markets.length > 1) unresolved.push("同名类目有多个榜单条件，须人工选择完整市场范围。");
  if (!labelledCategory && input.form.markets.some(item => !directoryMarkets.some(source => marketKey(source) === marketKey(item))))
    unresolved.push("当前表单市场条件尚未匹配已验证来源目录。");
  for (const purpose of purposes.filter(value => purposeSet.has(value))) {
    if (purpose === "market") {
      if (!marketCandidates.length) missingSources.push("市场目的缺少已验证的精确榜单条件。");
    } else if (!shopCandidates.length) missingSources.push(`${purpose}目的缺少已验证的精确店铺身份。`);
    else if (!directory.some(item => item.domain === "netshop" && item.dataset === purpose
      && shopCandidates.some(shop => shopKey(shop) === shopKey(item))))
      missingSources.push(`${purpose}目的在候选店铺中缺少对应规范来源。`);
  }
  let previousEqualLength: QuestionDateRange | null = null, previousMonthSameDates: QuestionDateRange | null = null;
  if (dates) {
    const start = date(dates.startDate)!, end = date(dates.endDate)!;
    const days = (end.getTime() - start.getTime()) / 86400000 + 1;
    previousEqualLength = { startDate: iso(shift(start, -days)), endDate: iso(shift(start, -1)) };
    if (start.getUTCFullYear() === end.getUTCFullYear() && start.getUTCMonth() === end.getUTCMonth())
      previousMonthSameDates = { startDate: iso(monthBefore(start)), endDate: iso(monthBefore(end)) };
  }
  return { schemaVersion: "business-question-suggestion-v1", authorityVerified: false, requiresUserConfirmation: true,
    dates, dateBasis, windows: windows.filter(value => selectedWindows.has(value)), purposes: purposes.filter(value => purposeSet.has(value)),
    shopCandidates, marketCandidates, unresolved, missingSources,
    comparison: { previousEqualLength, previousMonthSameDates,
      note: "经营分析当前环比为本期之前紧邻的等长区间；销售概览的同月自定义区间可能使用上月同期。旧报告口径按其原版本保留，请核对具体基期日期。" } };
}
