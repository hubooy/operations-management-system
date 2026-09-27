/** Passive UI projection. This never issues a read/coverage authorization. */
export const SCREENING_PROFILE = "business-agent-screening-reference-v1";
export const REPORT_DETAIL_BYTES = 5 * 1024 * 1024;
export const ORDINARY_RESPONSE_BYTES = 2 * 1024 * 1024;
export const screeningRoles = ["commerce", "promotion", "market_b2b", "independent_review", "report"] as const;
export const roleNames: Record<string, string> = { commerce: "店铺与商品", promotion: "推广与搜索", market_b2b: "市场与 B 端", independent_review: "独立复核", report: "报告整合" };
export const preparationNames: Record<string, string> = { queued_scan: "等待后台规则筛查", scanning: "正在完整规则筛查", queued_admission: "筛查已保存，等待容量检查", checking_capacity: "正在检查完整模型容量", analyzing: "多 Agent 分析中", waiting_review: "等待人工复核", completed: "已完成", failed: "已停止，请核验错误", cancelled: "已取消", paused: "已暂停" };
const labels: Record<string, string> = { current: "本期", previous: "环比", yearAgo: "同比", shop: "店铺", sku: "SKU", spu: "SPU", category: "品类", keyword: "关键词", daily: "分日", native: "原生", mapped: "商品关联", netshop: "网店", sales: "ERP 销售", market: "市场", planned: "已列入计划", executed: "已执行", unsupported: "不支持", missing_source: "缺少来源", not_executed: "未执行", dates_present: "查询日期有记录", missing_dates: "缺少日期记录", no_records: "无记录" };
const metricNames: Record<string,string> = {
  spendCents:"推广花费",reportedGmvCents:"平台上报归因成交金额",impressions:"展现数",clicks:"点击数",reportedOrderLines:"平台上报订单行",cartQuantity:"加购数量",
  paymentCents:"商品成交金额",paymentQuantity:"成交商品件数",productDayVisitors:"商品分日访客数",pageViews:"商品浏览量",reportedOrders:"上报成交单量",
  directGmvCents:"直接归因订单金额",indirectGmvCents:"间接归因订单金额",newCustomerGmvCents:"新客归因订单金额",
  netSalesCents:"净销售额",positiveSalesCents:"正向销售额",refundCents:"退款金额",costCents:"源成本",grossProfitCents:"净销售减源成本",reportedGrossProfitCents:"源报告毛利",feeCents:"分摊费用",
  netQuantity:"净销量",positiveQuantity:"正向销量",returnQuantity:"退货数量",netSalesExcludingAccessoriesCents:"剔除配件净销售额",ctr:"点击率",cpcCents:"平均点击成本",roas:"推广产出比",orderLineConversionRate:"订单行转化率",
};
const fieldNames: Record<string,string> = {value:"本期值",baseline:"基期值",difference:"差额",ratio:"本期比率",changeRate:"变化比例",percentagePoints:"百分点变化"};
const dictionaryName=(dictionary:Record<string,string>,key:string)=>Object.hasOwn(dictionary,key)?dictionary[key]:key;
type ObjectValue = Record<string, unknown>;
function object(value: unknown): ObjectValue { if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("筛查详情结构无效。"); return value as ObjectValue; }
function string(value: unknown, max = 2000): string { if (typeof value !== "string" || Array.from(value).length > max) throw new Error("筛查详情文本无效。"); return value; }
function count(value: unknown, max = 2_000_000): number { if (!Number.isSafeInteger(value) || (value as number) < 0 || (value as number) > max) throw new Error("筛查详情数量无效。"); return value as number; }
function bool(value: unknown): boolean { if (typeof value !== "boolean") throw new Error("筛查详情完整性标识无效。"); return value; }
function list(value: unknown, max: number): unknown[] { if (!Array.isArray(value) || Array.from(value).length > max) throw new Error("筛查详情列表超限或无效。"); return value; }
const name = (value: unknown) => typeof value === "string" ? Object.hasOwn(labels,value) ? labels[value] : value : "未提供";
const maybe = (value: unknown) => value == null ? "未提供" : string(value);
function identity(value: unknown): string {
  const query = object(value);
  return ["platform", "shop", "dataset", "channel", "category", "scope", "rankingDimension", "priceBandFilter", "startDate", "endDate", "window"]
    .filter(key => query[key] !== undefined && query[key] !== "").map(key => ["dataset","window"].includes(key) ? name(string(query[key], 500)) : string(query[key],500)).join(" · ");
}
export function entityLabel(value: unknown): string {
  if(value==null) return "未提供对象";
  const entity=object(value);
  return ["platform","shopName","shop","channel","category","skuId","sku","spuId","spu","keyword","searchTerm","date","brand","merchantCode"].filter(key=>entity[key]!=null && entity[key]!=="").map(key=>string(entity[key],1000)).join(" · ") || "身份字段缺失";
}
function dateDetails(value: ObjectValue, period: unknown): string {
  const p=object(period), present=list(value.presentDates,93).map(v=>string(v,10)), missing=list(value.missingDates,93).map(v=>string(v,10));
  return `${string(p.startDate,10)} 至 ${string(p.endDate,10)} · ${name(value.status)}；有记录 ${present.length} 天；缺日 ${missing.length ? missing.join("、") : "无"}`;
}
export function isReportDetailPath(path: string): boolean { return /^\/api\/ai\/reports\/[A-Za-z0-9_-]{1,160}$/.test(path); }
export function allowReportDetailBytes(data: unknown, bytes: number, ok: boolean): boolean {
  if (!Number.isSafeInteger(bytes) || bytes < 0) return false;
  if (bytes <= ORDINARY_RESPONSE_BYTES) return true;
  if (!ok || bytes > REPORT_DETAIL_BYTES) return false;
  if (!data || typeof data !== "object" || Array.isArray(data)) return false;
  const snapshot = (data as ObjectValue).snapshot;
  return Boolean(snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) && (snapshot as ObjectValue).executionProfile === SCREENING_PROFILE && (snapshot as ObjectValue).schemaVersion === "business-report-v1");
}
export type CoverageLine = { title: string; status: string; details: string };
export type PartitionLine = { title: string; scanned: number; matched: number; retained: number; omitted: number; supported: boolean; reason: string; eligibility: string };
export type Professional = { role: string; summary: string; findings: { title: string; kind: string; explanation: string; facts: string[]; action: string[] }[] };
export type ScreeningView = { sources: CoverageLine[]; families: CoverageLine[]; requested: CoverageLine[]; tables: CoverageLine[]; partitions: PartitionLine[]; proofs: { role: string; complete: boolean; pages: number; expected: number; budgetRequired: boolean; budgetStarted: boolean; budgetComplete: boolean }[]; planned: boolean; executed: boolean; dates: boolean; professionals: Professional[]; limitations: string[] };
export function projectScreening(value: unknown, professionals: unknown, reportId: string): ScreeningView {
  const data = object(value), reference = object(data.reference), authority = object(data.authority);
  if (data.schemaVersion !== "business-screening-content-v1" || reference.reportId !== reportId || authority.entityDailyCoverageVerified !== false) throw new Error("筛查详情与当前报告或日期口径不一致。");
  const sourceList = list(data.sources, 48);
  const sources = sourceList.map(item => { const source = object(item); return { title: string(source.key,160), status: name(source.domain), details: identity(source.query) }; });
  const tableBindings = list(data.tableBindings, 256).map(object);
  if(new Set(sources.map(s=>s.title)).size!==sources.length || new Set(tableBindings.map(b=>string(b.tableKey,160))).size!==tableBindings.length) throw new Error("来源或分析表身份重复。");
  const titleFor = (key: unknown) => { const binding = tableBindings.find(item => item.tableKey === key); return binding ? `${maybe(binding.sourceKey)} · ${name(binding.dimension)} · ${name(binding.mode)}${binding.baselineKey ? ` · 基期 ${maybe(binding.baselineKey)}` : ""}` : (()=>{throw new Error("分析表身份缺失。");})(); };
  const families: CoverageLine[] = [], requested: CoverageLine[] = [], tables: CoverageLine[] = [], partitions: PartitionLine[] = [];
  for (const item of list(data.coverage, 2048)) {
    const record = object(item), row = object(record.value);
    if (record.kind === "family") families.push({ title: name(row.domain), status: name(row.family), details: identity(row.query) });
    else if (record.kind === "requested") requested.push({ title: `${name(row.dimension)} · ${name(row.window)} · ${name(row.mode)}`, status: name(row.status), details: `${row.sourceKey == null ? "缺少来源" : maybe(row.sourceKey)}；${row.reason == null || row.reason === "" ? "服务端未列额外原因" : maybe(row.reason)}` });
    else if (record.kind === "table") {
      const source = object(row.sourceCoverage), base = row.baselineCoverage == null ? null : object(row.baselineCoverage);
      tables.push({ title: titleFor(row.tableKey), status: `已扫描 ${count(row.scannedRows,250000)} / ${count(row.expectedRows,250000)} 行`, details: `本期日期：${dateDetails(source,row.sourcePeriod)}；基期日期：${base ? dateDetails(base,row.baselinePeriod) : "未选择"}；日期可比：${bool(row.dateCoverageComparable) ? "是（仅来源）" : "否或不足"}` });
    } else if (record.kind === "partition") {
      const matched = count(row.matchedRows,250000), retained = count(row.retainedRows,250000), omitted = count(row.omittedRows,250000);
      if (matched !== retained + omitted) throw new Error("候选保留与省略数量不一致。");
      partitions.push({ title: `${titleFor(row.tableKey)} · ${string(row.ruleId,160)}`, scanned: count(row.scannedRows,250000), matched, retained, omitted, supported: bool(row.supported), reason: row.unavailableReason == null ? "" : string(row.unavailableReason), eligibility: `可判定 ${count(row.eligibleRows,250000)}；不可判定 ${count(row.ineligibleRows,250000)}${Object.entries(object(row.ineligibleReasons)).map(([reason,amount])=>`；${string(reason,160)}：${count(amount,250000)}`).join("")}` });
    } else throw new Error("筛查覆盖包含未知记录，不能静默省略。");
  }
  if (tables.length !== count(authority.tableCount,256) || partitions.length !== count(authority.partitionCount,64)) throw new Error("筛查表或分区覆盖不完整。");
  const proofData = object(data.readProofs);
  if (Object.keys(proofData).length !== 5) throw new Error("五个 Agent 读取证明不完整。");
  const proofs = screeningRoles.map(role => {
    const proof = object(proofData[role]), page = object(proof.package), budget = object(proof.budget);
    if (proof.role !== role || proof.screeningId !== reference.id) throw new Error("读取证明不属于当前角色或筛查。");
    return { role, complete: bool(page.complete), pages: count(page.pages,8), expected: count(page.expectedPages,8), budgetRequired: bool(budget.required), budgetStarted: bool(budget.started), budgetComplete: bool(budget.complete) };
  });
  const professionalData = object(professionals);
  const analyses = screeningRoles.slice(0,3).map(role => {
    const analysis = object(professionalData[role]);
    if (analysis.schemaVersion !== "business-screening-diagnosis-v1" || analysis.reportId !== reportId || analysis.role !== role) throw new Error("专业分析报告身份不符。");
    return { role, summary: string(analysis.summary), findings: list(analysis.findings,2).map(item => {
      const finding = object(item);
      const facts = list(finding.facts,6).map(raw => { const fact = object(raw), ref = object(fact.reference); const metric = maybe(fact.metric ?? ref.metric); if (typeof fact.value !== "number" || !Number.isFinite(fact.value)) throw new Error("引用数值无效。"); const field=maybe(fact.field ?? ref.field); const unit=field==="percentagePoints" ? " 个百分点" : field==="changeRate" || ["ctr","orderLineConversionRate"].includes(metric) ? "（比例，1 表示 100%）" : metric.endsWith("Cents") ? " 分" : metric==="roas" ? " 倍" : "（原单位）"; return `${entityLabel(fact.entity)}；${dictionaryName(metricNames,metric)} · ${dictionaryName(fieldNames,field)}：${fact.value}${unit}${fact.partial === true ? "；含指标缺值，仅为部分值" : ""}`; });
      const action = finding.action == null ? [] : Object.entries(object(finding.action)).filter(([key]) => ["object","change","prerequisites","successMetric","observationDays","rollback","priority","ownerRole","budgetImpact"].includes(key)).map(([key,value]) => `${({object:"对象",change:"调整",prerequisites:"前提",successMetric:"观察指标",observationDays:"观察天数",rollback:"回退",priority:"优先级",ownerRole:"负责人",budgetImpact:"预算影响"} as Record<string,string>)[key]}：${typeof value === "number" ? count(value,90) : string(value,600)}`);
      return { title: string(finding.title,160), kind: string(finding.kind,40), explanation: string(finding.explanation,1200), facts, action };
    }) };
  });
  return { sources, families, requested, tables, partitions, proofs, planned: bool(authority.requestedCoveragePlanned), executed: bool(authority.requestedTablesExecutedComplete), dates: bool(authority.requestedSourceDateCoverageComplete), professionals: analyses, limitations: list(data.limitations,20).map(value => string(value)) };
}
