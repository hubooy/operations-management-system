import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import * as XLSX from "xlsx";
import { marketNaturalKey } from "../lib/market/import-identity";
import { parseMarketRows, parseRange, parseRangeBounds } from "../lib/market/parser";
import { aggregateMarketEstimates, annotateRankBounds } from "../lib/market/gmv-estimation";
import { beginLatestRequest, invalidateLatestRequest, invokeLatestRequest, settleLatestRequest } from "../lib/market/latest-request";
import { marketRankingPricePresentation } from "../lib/market/ranking-price-presentation";
import { remainingInferenceUnitsForJob } from "../lib/market/annotation-progress";

async function marketUiSource() {
  const [marketView, masterAdmin] = await Promise.all([
    readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/market-master-admin-panel.tsx", import.meta.url), "utf8"),
  ]);
  return `${marketView}\n${masterAdmin}`;
}

function csvBytes(value: string) {
  return new TextEncoder().encode(value);
}

function legacyXlsBytes(rows: Array<Array<string | number>>) {
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(rows), "sheet");
  return new Uint8Array(XLSX.write(workbook, { type: "array", bookType: "biff8" }) as ArrayBuffer);
}

function xlsx1904Bytes(rows: Array<Array<string | number>>) {
  const workbook = XLSX.utils.book_new();
  workbook.Workbook = { WBProps: { date1904: true } };
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(rows), "sheet");
  return new Uint8Array(XLSX.write(workbook, { type: "array", bookType: "xlsx" }) as ArrayBuffer);
}

test("AI 标注设置首次加载和任务切换不会读取空进度", () => {
  assert.equal(remainingInferenceUnitsForJob(undefined, null), 0);
  assert.equal(remainingInferenceUnitsForJob({ id: "job-a", remainingInferenceCount: 7 }, null), 7);
  assert.equal(remainingInferenceUnitsForJob(
    { id: "job-a", remainingInferenceCount: 7 },
    { job: { id: "job-a" }, remainingInferenceUnits: 3 },
  ), 3);
  assert.equal(remainingInferenceUnitsForJob(
    { id: "job-b", remainingInferenceCount: 5 },
    { job: { id: "job-a" }, remainingInferenceUnits: 3 },
  ), 5);
});

test("市场榜单 CSV 映射商品、周期和经营指标", () => {
  const result = parseMarketRows({
    bytes: csvBytes([
      "周期起,周期止,类目,店铺类型,排名,SKUID,商品名称,品牌,成交金额,成交件数,访客数,成交转化率",
      "2026-07-01,2026-07-20,商用净水设备,自营,1,10001,商用净水机,TERUISI,12500.50,42,1680,2.5%",
    ].join("\n")),
    fileName: "市场商品榜单.csv",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-20",
  });

  assert.equal(result.sheetName, "CSV");
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0]?.skuCode, "10001");
  assert.equal(result.rows[0]?.category, "商用净水设备");
  assert.equal(result.rows[0]?.gmvCents, 1_250_050);
  assert.equal(result.rows[0]?.conversionBps, 250);
  assert.equal(result.rows[0]?.rankingDimension, "SKU");
  assert.equal(result.rows[0]?.operationMode, "自营");
  assert.equal(result.rows[0]?.naturalKey, marketNaturalKey({
    periodStart: "2026-07-01", periodEnd: "2026-07-20", category: "商用净水设备",
    scope: "自营", priceBandFilter: "全部", rankingDimension: "SKU", skuCode: "10001",
  }));
});

test("市场商品与自有商品关联字段具备独立索引", async () => {
  const [salesModels, netshopDatabase] = await Promise.all([
    readFile(new URL("../backend/sales/models.py", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/database.ts", import.meta.url), "utf8"),
  ]);
  assert.match(salesModels, /fields=\["product_code", "business_date"\],[\s\S]*name="sales_product_date_idx"/);
  assert.match(netshopDatabase, /netshop_rows_sku_id_idx[\s\S]*ON netshop_rows \(sku_id\)/);
  assert.match(netshopDatabase, /netshop_rows_spu_id_idx[\s\S]*ON netshop_rows \(spu_id\)/);
  assert.match(netshopDatabase, /netshop_rows_product_code_idx[\s\S]*ON netshop_rows \(product_code\)/);
});

test("旧版 XLS 商品榜单可直接解析并生成稳定的 SPU 市场标识", () => {
  const bytes = legacyXlsBytes([
    ["序号", "排名变化", "日期", "渠道", "行业名称", "商品名称", "所属店铺", "成交金额", "访客数", "搜索点击次数", "关注人数", "成交单量"],
    [1, "新入榜", "20260721", "整体", "商用净水设备", "商用净水机 A", "示例旗舰店", "￥6万 ~ ￥8万", "100 ~ 200", "50 ~ 100", "0.0", "0 ~ 5"],
  ]);
  const first = parseMarketRows({
    bytes,
    fileName: "商品榜单_商用净水设备_整体SPU_20260721.xls",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-01",
  });
  const second = parseMarketRows({
    bytes,
    fileName: "商品榜单_商用净水设备_整体SPU_20260721.xls",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-01",
  });
  assert.equal(first.rows.length, 1);
  assert.equal(first.rows[0]?.skuCode, second.rows[0]?.skuCode);
  assert.match(first.rows[0]?.skuCode ?? "", /^JD-MKT-SPU-[A-F0-9]{16}$/);
  assert.equal(first.rows[0]?.periodStart, "2026-07-21");
  assert.equal(first.rows[0]?.periodEnd, "2026-07-21");
  assert.equal(first.rows[0]?.category, "商用净水设备");
  assert.equal(first.rows[0]?.scope, "整体SPU");
  assert.equal(first.rows[0]?.rank, 1);
  assert.equal(first.rows[0]?.gmvCents, 7_000_000);
  assert.equal(first.rows[0]?.visitors, 150);
  assert.equal(first.warnings.length, 1);
  assert.match(first.warnings[0]?.message ?? "", /稳定市场标识/);
});

test("市场上传入口声明支持 XLS、XLSX 和 CSV", async () => {
  const [route, view] = await Promise.all([
    readFile(new URL("../app/api/market/import/route.ts", import.meta.url), "utf8"),
    marketUiSource(),
  ]);
  assert.match(route, /\(xls\|xlsx\|csv\)/);
  assert.match(route, /MarketImportRowLimitError[\s\S]*status: 413/);
  assert.match(view, /accept="\.xls,\.xlsx,\.csv"/);
  assert.match(view, /5000 条数据/);
});

test("市场分析按商品榜单、行业汇报、竞品对比、系统和 AI 设置拆分为四个工作区", async () => {
  const [view, masterRoute, backendAdmin, styles] = await Promise.all([
    marketUiSource(),
    readFile(new URL("../app/api/market/master/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/market/admin.py", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  for (const label of ["商品榜单", "行业汇报", "竞品对比", "系统和 AI 设置"]) assert.match(view, new RegExp(label));
  assert.match(view, /moduleView: ModuleViewKey<"market">/);
  assert.match(view, /const activeSection: MarketSectionKey = moduleView/);
  assert.match(view, /onModuleViewChange\(section\)/);
  assert.match(view, /activeSection === "ranking"/);
  assert.match(view, /activeSection === "overview"/);
  assert.match(view, /activeSection === "compare"/);
  assert.match(view, /activeSection === "settings"/);
  assert.match(view, /if \(activeSection === "settings" \|\| activeSection === "compare"\) \{[\s\S]+setLoading\(false\);[\s\S]+setError\(""\);[\s\S]+return;/);
  assert.match(view, /activeSection !== "settings" && activeSection !== "compare" && loading && !data/);
  assert.match(view, /data: MarketSettingsStatus \| null; onImported: \(\) => void/);
  assert.match(view, /<CompareWorkspace/);
  assert.match(view, /useState<MarketCompareSelection\[\]>\(\[\]\)/);
  assert.match(view, /selections\.map\(\(item\) => <button/);
  assert.match(view, /params\.append\("selection", JSON\.stringify\(selection\)\)/);
  assert.match(view, /params\.set\("q", query\.trim\(\)\)/);
  assert.match(view, /const requestId = beginLatestRequest\(requestGeneration\)/);
  assert.match(view, /requestId !== requestGeneration\.current/);
  assert.match(view, /invalidateLatestRequest\(requestGeneration\); controller\.abort\(\)/);
  assert.match(view, /const currentResult = result\?\.requestKey === request\.requestKey \? result : null/);
  assert.match(view, /const data = currentResult\?\.payload \?\? result\?\.payload \?\? null/);
  assert.match(view, /const loading = Boolean\(request\.url && result\?\.requestKey !== request\.requestKey\)/);
  assert.match(view, /setResult\(\{ requestKey: request\.requestKey, payload, error: "" \}\)/);
  assert.match(view, /当前筛选范围无数据：\{missingSelections\.map/);
  assert.match(view, /marketCompareSelectionKey\(item\)/);
  assert.doesNotMatch(view, /compareIds\.map\(\(sku\) => items\.find/);
  assert.match(view, /<MarketMasterAdminPanel currentUser=\{currentUser\}/);
  assert.match(view, /<MarketAnnotationView currentUser=\{currentUser\}/);
  assert.match(view, /市场分析 → 系统和 AI 设置/);
  assert.match(view, /view=system_kpis/);
  for (const label of ["市场商品身份", "待确认价格", "待 AI 标注总量", "已生成 AI 结果", "同图直接复用", "新图仅识别价格", "完整分类和价格", "暂不可自动识别"]) assert.match(view, new RegExp(label));
  assert.match(view, /四项合计与总量一致/);
  assert.match(styles, /\.market-image-cache-card\{grid-column:5;grid-row:1\/span 2\}/);
  assert.match(masterRoute, /payload: \{ operation: "master", view, params: queryParams\(params\) \}/);
  assert.match(masterRoute, /service: "reader"/);
  assert.match(backendAdmin, /if view == "system_kpis":[\s\S]+return system_kpis\(\)/);
});

test("SKU 数据库与 AI 标注使用最新身份缓存且页面不读取完整目录", async () => {
  const [adminService, annotationService, annotationRoute, backendAnnotations, annotationView, migration] = await Promise.all([
    readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/annotation-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/annotations/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/market/annotations.py", import.meta.url), "utf8"),
    readFile(new URL("../app/market-annotation-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0038_market_master_identities.sql", import.meta.url), "utf8"),
  ]);
  assert.match(adminService, /JOIN market_ranking_entries source ON source\.id=identity\.latest_entry_id/);
  assert.match(annotationService, /FROM market_master_identities identity/);
  assert.match(annotationRoute, /"candidate_counts", "review", "catalog"\]\.includes\(view\)/);
  assert.match(backendAnnotations, /if view == "review":[\s\S]+return _review\(params\)/);
  assert.match(backendAnnotations, /if view == "catalog":[\s\S]+return \{"catalog": _catalog\(params\)\}/);
  assert.match(annotationRoute, /includeCatalog: params\.get\("includeCatalog"\) === "1"/);
  assert.doesNotMatch(annotationView, /includeCatalog|IntersectionObserver|catalogRequested|view: "catalog"|完整市场 SKU 库检索/);
  assert.doesNotMatch(annotationView, /void load\(item\.id, search, searchPage, 1\)/);
  assert.match(migration, /ROW_NUMBER\(\) OVER/);
});

test("市场榜单与系统设置呈现商品链接、上榜期数、主图价格复核和系统 AI 算力", async () => {
  const [view, database] = await Promise.all([
    marketUiSource(),
    readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8"),
  ]);
  assert.match(view, /market-product-title-link/);
  assert.match(view, /上榜 \{count\(item\.periodCount\)\} 期/);
  assert.match(database, /COUNT\(DISTINCT p\.period_start \|\| '\|' \|\| p\.period_end\)/);
  for (const label of ["SKU 数据库", "AI 标注", "品牌确认", "映射配置", "数据配置"]) assert.match(view, new RegExp(label));
  assert.match(view, /运营管理系统 AI 算力/);
  assert.match(view, /fetch\("\/api\/ai\/models"/);
  assert.match(view, /market-price-review-table/);
  assert.match(view, /market-review-image/);
  assert.match(view, /action: "infer_brand"/);
  assert.match(view, /action: "confirm_brand"/);
});

test("SKU 数据库和品牌确认提供卡片、全页 AI 识别与批量确认入口", async () => {
  const source = await marketUiSource();
  const annotation = await readFile(new URL("../app/market-annotation-view.tsx", import.meta.url), "utf8");
  const [service, gmvTotals] = await Promise.all([
    readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/gmv-total.ts", import.meta.url), "utf8"),
  ]);
  assert.match(source, /AI 一键识别价格（最多100条）/);
  assert.match(source, /pendingPriceSource/);
  assert.match(source, /AI 识别价/);
  assert.match(source, /非 AI 识别价/);
  assert.match(source, /pendingPricePageSize/);
  assert.match(source, /待确认价格页码/);
  assert.match(source, /masterCategories\.forEach\(\(value\) => params\.append\("pendingPriceCategory", value\)\)/);
  assert.match(source, /pendingPriceSources\.forEach\(\(value\) => params\.append\("pendingPriceSource", value\)\)/);
  assert.match(source, /view: "database_secondary"/);
  assert.match(source, /pendingPricePage: String\(pendingPricePage\)/);
  assert.match(source, /pendingPricePageSize: String\(pendingPricePageSize\)/);
  assert.match(source, /masterCandidatePriceSources\.forEach\(\(value\) => params\.append\("priceSource", value\)\)/);
  assert.doesNotMatch(source, /params\.set\("priceSource", pendingPriceSource\)/);
  assert.match(source, /pendingPricePage, pendingPricePageSize, pendingPriceSources/);
  assert.doesNotMatch(source, /setPriceCategory\(\(current\) => current \|\| payload\.priceRecognition/);
  assert.match(source, /SKU 数据库每页条数/);
  assert.match(source, /setMasterCategories\(values\); setPriceCategory\(values\.length === 1 \? values\[0\] : ""\); setSubcategoryFilters\(\[\]\); setPage\(1\); setPendingPricePage\(1\);/);
  assert.match(source, /primaryLoadControllerRef\.current\?\.abort\(\)/);
  assert.match(source, /secondaryLoadControllerRef\.current\?\.abort\(\)/);
  assert.match(source, /generation !== primaryLoadGenerationRef\.current/);
  assert.match(source, /generation !== secondaryLoadGenerationRef\.current/);
  assert.match(source, /setData\(\(current\) => \(\{ \.\.\.\(current \?\? emptyMarketMasterWorkspace\(\)\), \.\.\.primary \}\)\)/);
  assert.match(source, /setPage\(primary\.masterData\.pagination\.page\)/);
  assert.match(source, /setPendingPricePage\(payload\.pendingPrices\.pagination\.page\)/);
  assert.match(source, /latestLoadRef\.current = reloadLatest/);
  assert.match(source, /const loadLatest = useCallback\(\(\) => invokeLatestRequest\(latestLoadRef\), \[\]\)/);
  assert.match(source, /setSubcategoryFilters\(\[\]\); setPage\(1\); setPendingPricePage\(1\);/);
  assert.match(source, /key=\{`pending-price-\$\{row\.id\}`\}/);
  assert.match(source, /人工确认市场定位价（元）/);
  assert.match(source, /Math\.round\(priceYuan \* 100\)/);
  assert.match(annotation, /主图价格（元）/);
  assert.doesNotMatch(annotation, /主图价格（分）/);
  assert.match(source, /AI 一键识别品牌（所有页）/);
  assert.match(source, /一键确认全部候选/);
  assert.match(source, /暂停识别/);
  assert.match(source, /remainingCount/);
  assert.match(source, /run_brand_recognition_job_batch/);
  assert.match(source, /create_brand_recognition_job/);
  assert.match(source, /品牌种子词典/);
  assert.match(source, /未知品牌 SKU 清单/);
  assert.match(source, /refresh_brand_seeds/);
  assert.match(source, /match_brand_seeds/);
  assert.match(source, /market-master-product-grid/);
  assert.match(service, /market_brand_suggestions/);
  assert.match(service, /PARTITION BY m\.category, m\.scope, m\.ranking_dimension, m\.sku_code/);
  assert.match(gmvTotals, /period_kind='monthly'/);
  assert.match(gmvTotals, /period_kind='daily'/);
  assert.match(gmvTotals, /coverage_days DESC/);
  assert.match(service, /market_sku_gmv_totals/);
  assert.match(service, /gmv_total_cents DESC/);
  const adminSource = await readFile(new URL("../app/market-master-admin-panel.tsx", import.meta.url), "utf8");
  const panel = adminSource.slice(adminSource.indexOf("export function MarketMasterAdminPanel"));
  assert.equal(panel.match(/await loadLatest\(\)/g)?.length, 9);
  assert.doesNotMatch(panel, /await load\(\)/);
});

test("market workspace request generations drop stale debounce successes and stale network failures", async () => {
  const generation = { current: 0 };
  let resolveOldSuccess: (value: string) => void = () => undefined;
  const oldSuccessSource = new Promise<string>((resolve) => { resolveOldSuccess = resolve; });
  const oldSuccessId = beginLatestRequest(generation);
  const oldSuccess = settleLatestRequest(generation, oldSuccessId, () => oldSuccessSource);
  invalidateLatestRequest(generation);
  resolveOldSuccess("old filters");
  assert.deepEqual(await oldSuccess, { current: false });

  let rejectOldRequest: (reason: Error) => void = () => undefined;
  const oldFailureSource = new Promise<string>((_resolve, reject) => { rejectOldRequest = reject; });
  const oldFailureId = beginLatestRequest(generation);
  const oldFailure = settleLatestRequest(generation, oldFailureId, () => oldFailureSource);
  invalidateLatestRequest(generation);
  const currentId = beginLatestRequest(generation);
  assert.deepEqual(await settleLatestRequest(generation, currentId, async () => "new filters"), { current: true, value: "new filters" });
  rejectOldRequest(new Error("old network failure"));
  assert.deepEqual(await oldFailure, { current: false });

  const currentFailureId = beginLatestRequest(generation);
  await assert.rejects(
    settleLatestRequest(generation, currentFailureId, async () => { throw new Error("current network failure"); }),
    /current network failure/,
  );
});

test("an old market POST completion refreshes through the latest filter load closure", async () => {
  const calls: string[] = [];
  const latestLoad = { current: async () => { calls.push("filter-A"); } };
  const completePostStartedUnderA = async () => invokeLatestRequest(latestLoad);

  latestLoad.current = async () => { calls.push("filter-B"); };
  await invokeLatestRequest(latestLoad);
  await completePostStartedUnderA();

  assert.deepEqual(calls, ["filter-B", "filter-B"]);
});

test("SKU 数据库合并价格与 AI 入库，按需加载，并提供细分品类设置和概括时间筛选", async () => {
  const [view, annotation, service, route, backendAdmin, styles] = await Promise.all([
    marketUiSource(),
    readFile(new URL("../app/market-annotation-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/master/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/market/admin.py", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  for (const label of ["SKU 数据库与价格审核", "细分品类", "价格状态", "入库状态", "编辑 SKU 全部数据", "编辑 SKU 数据", "细分品类设置", "保存并刷新全部关联数据", "全局统计周期", "源表价格区间中位数兜底"]) assert.match(view, new RegExp(label));
  assert.match(view, /const marketStartDate = customStartDate/);
  assert.match(view, /const marketEndDate = customEndDate/);
  assert.match(view, /market-overview-period market-global-period/);
  assert.match(view, /当前市场周期和筛选条件下暂无商品数据/);
  assert.match(view, /databaseArea === "annotation"/);
  assert.match(view, /<MarketAnnotationView currentUser=\{currentUser\} embedded/);
  assert.match(annotation, /storageStatus/);
  assert.match(annotation, /itemSegment/);
  assert.match(annotation, /reviewView === "gallery"/);
  assert.match(annotation, /批量入库/);
  assert.match(annotation, /全选筛选结果（跨页/);
  assert.match(annotation, /AI 标注每页条数/);
  assert.match(annotation, /AI 标注页码/);
  assert.match(annotation, /aria-label="AI 标注三级类目多选"/);
  assert.match(annotation, /<summary aria-label=\{`\$\{label\}筛选`\}>\{summary\}<\/summary>/);
  assert.match(annotation, /reviewCategories\.forEach\(\(value\) => params\.append\("itemCategory", value\)\)/);
  assert.match(annotation, /data\.taxonomy\.filter\(\(item\) => !reviewCategories\.length \|\| reviewCategories\.includes\(item\.category\)\)/);
  assert.match(annotation, /setItemSegments\(\[\]\)/);
  assert.match(annotation, /已汇总 \{data\.reviewSummary\.jobCount\} 个任务/);
  assert.match(annotation, /aggregateJobs: true/);
  assert.match(annotation, /全部 AI 结果/);
  assert.match(annotation, /未生成 AI 结果（含失败）/);
  assert.match(annotation, /此筛选不会调用模型/);
  assert.match(annotation, /annotationProductHref/);
  assert.match(annotation, /https:\/\/item\.jd\.com\/\$\{sku\}\.html/);
  assert.match(annotation, /annotation-image-link/);
  assert.match(annotation, /annotation-review-table-wrap/);
  assert.match(annotation, /打开商品链接/);
  assert.match(annotation, /taxonomy/);
  assert.match(annotation, /readOnly/);
  assert.match(view, /market-master-database-table/);
  assert.match(view, /market-master-table-image/);
  assert.match(view, /打开商品链接/);
  assert.match(service, /WITH filtered AS MATERIALIZED/);
  assert.match(service, /pagination_sentinel/);
  assert.doesNotMatch(service, /COUNT\(\*\) OVER\(\) full_count/);
  assert.match(service, /wantsDatabase/);
  assert.match(service, /updateMarketSkuMasterData/);
  assert.match(service, /saveMarketSubcategorySettings/);
  assert.match(service, /FROM market_subcategory_taxonomy t/);
  assert.match(route, /parseMarketMasterView\(params\)/);
  assert.match(route, /domain: "master"/);
  assert.match(backendAdmin, /if action == "update_sku_master":[\s\S]+return _update_sku_master\(payload, principal\)/);
  assert.match(styles, /\.annotation-review-table-wrap\s*\{[\s\S]*?overflow-x:\s*clip/);
  assert.match(styles, /\.annotation-review-table\s*\{[\s\S]*?min-width:\s*0;[\s\S]*?table-layout:\s*fixed/);
  assert.match(styles, /\.annotation-job-list\s*\{[\s\S]*?max-height:\s*240px;[\s\S]*?display:\s*grid;[\s\S]*?overflow-y:\s*auto/);
  assert.match(styles, /\.annotation-task-setup\s*\{[\s\S]*?grid-template-columns:/);
  assert.match(styles, /\.annotation-current-run\s*\{[\s\S]*?grid-template-columns:/);
  assert.match(backendAdmin, /if action == "save_subcategory_settings"/);
});

test("视觉模型错误保留安全的供应商详情并给出状态码诊断", async () => {
  const source = await readFile(new URL("../lib/market/annotation-model.ts", import.meta.url), "utf8");
  assert.match(source, /modelErrorDetail/);
  assert.match(source, /模型供应商限流或额度不足/);
  assert.match(source, /请求被接口拒绝/);
  assert.match(source, /replace\(\/\\b\(sk-\|key-\)/);
  assert.match(source, /throw modelCallError\("视觉", response\.status, data, response\.headers\.get\("retry-after"\)\)/);
});

test("market imports automatically match enabled brand seeds", async () => {
  const [service, matcher] = await Promise.all([
    readFile(new URL("../lib/market/import-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/brand-seeds.ts", import.meta.url), "utf8"),
  ]);
  assert.match(service, /matchImportedMarketBrands/);
  assert.match(service, /refreshSystemMarketBrandSeeds/);
  assert.match(service, /brandMatch\.systemSeedSnapshot/);
  assert.match(service, /brandMatch\.rows/);
  assert.match(matcher, /title_prefix/);
  assert.match(matcher, /title_anywhere/);
  assert.match(matcher, /京东自营/);
});

test("市场数据使用默认周期并阻止同周期重复 SKU", () => {
  const result = parseMarketRows({
    bytes: csvBytes([
      "商品编号,商品名称,成交金额",
      "SKU-8,竞品 A,100~300",
      "SKU-8,重复竞品,900",
    ].join("\n")),
    fileName: "sku.csv",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-22",
    defaultCategory: "切片机",
    defaultScope: "POP",
  });

  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0]?.gmvCents, 20_000);
  assert.equal(result.rows[0]?.periodEnd, "2026-07-22");
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0]?.message ?? "", /重复 SKU/);
});

test("市场区间保留原文、中值和上下界，并把榜单价格段纳入自然键", () => {
  assert.deepEqual(parseRangeBounds("￥8,000 ~ ￥1万"), [8_000, 10_000]);
  assert.equal(parseRange("￥8,000 ~ ￥1万"), 9_000);
  assert.deepEqual(parseRangeBounds("8% ~ 10%"), [0.08, 0.1]);
  assert.equal(parseRange("8% ~ 10%"), 0.09);
  const result = parseMarketRows({
    bytes: csvBytes("商品编号,成交金额,成交件数,访客数,价格带筛选\nSKU-1,￥8万~￥10万,5~10,600~800,0-500"),
    fileName: "range.csv",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-31",
    defaultCategory: "净水设备",
    defaultScope: "POP",
  });
  const row = result.rows[0]!;
  assert.equal(row.gmvRaw, "￥8万~￥10万");
  assert.equal(row.gmvCents, 9_000_000);
  assert.equal(row.gmvLowCents, 8_000_000);
  assert.equal(row.gmvHighCents, 10_000_000);
  assert.equal(row.quantityLow, 5);
  assert.equal(row.quantityHigh, 10);
  assert.equal(row.visitorsLow, 600);
  assert.equal(row.visitorsHigh, 800);
  assert.equal(row.priceBandFilter, "0-500");
  assert.equal(row.naturalKey, marketNaturalKey({
    periodStart: "2026-07-01", periodEnd: "2026-07-31", category: "净水设备",
    scope: "POP", priceBandFilter: "0-500", rankingDimension: "SKU", skuCode: "SKU-1",
  }));
});

test("市场自然键对字段内分隔符无歧义", () => {
  const result = parseMarketRows({
    bytes: csvBytes([
      "周期起,周期止,类目,口径,商品编号,成交金额",
      "2026-07-01,2026-07-31,a|b,c,SKU-X,100",
      "2026-07-01,2026-07-31,a,b|c,SKU-X,200",
    ].join("\n")),
    fileName: "separator.csv",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-31",
  });
  assert.equal(result.rows.length, 2);
  assert.notEqual(result.rows[0]?.naturalKey, result.rows[1]?.naturalKey);
  assert.equal(result.warnings.length, 0);
});

test("市场 SKU 截断不会留下孤立 UTF-16 代理项", () => {
  const inputSku = `${"A".repeat(79)}😀Z`;
  const result = parseMarketRows({
    bytes: csvBytes(`商品编号,成交金额\n${inputSku},100`),
    fileName: "unicode-sku.csv",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-31",
  });
  const skuCode = result.rows[0]?.skuCode ?? "";
  assert.equal(skuCode, `${"A".repeat(79)}😀`);
  assert.equal(Array.from(skuCode).length, 80);
  assert.equal(skuCode.includes("�"), false);
});

test("市场周期拒绝不存在的自然日和倒序日期", () => {
  const result = parseMarketRows({
    bytes: csvBytes([
      "周期起,周期止,商品编号,成交金额",
      "2026-02-28,2026-03-01,SKU-OK,100",
      "2026-02-30,2026-03-01,SKU-BAD-DAY,100",
      "2026-03-02,2026-03-01,SKU-REVERSED,100",
    ].join("\n")),
    fileName: "dates.csv",
    defaultStartDate: "2026-02-01",
    defaultEndDate: "2026-03-31",
  });
  assert.deepEqual(result.rows.map((row) => row.skuCode), ["SKU-OK"]);
  assert.equal(result.warnings.length, 2);
  assert.throws(() => parseMarketRows({
    bytes: csvBytes("商品编号,成交金额\nSKU-X,100"),
    fileName: "invalid-default.csv",
    defaultStartDate: "2026-02-30",
    defaultEndDate: "2026-03-01",
  }), /周期无效/);
});

test("市场 XLSX 按工作簿的 1904 日期系统解释序列日期", () => {
  const serial = Math.floor((Date.UTC(2026, 6, 1) - Date.UTC(1904, 0, 1)) / 86_400_000);
  const result = parseMarketRows({
    bytes: xlsx1904Bytes([
      ["日期", "商品编号", "成交金额"],
      [serial, "SKU-1904", 100],
    ]),
    fileName: "date-1904.xlsx",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-01",
  });
  assert.equal(result.rows[0]?.periodStart, "2026-07-01");
  assert.equal(result.rows[0]?.periodEnd, "2026-07-01");
});

test("市场同步导入在解析阶段拒绝超过 5000 条数据", () => {
  const lines = ["商品编号,成交金额"];
  for (let index = 0; index < 5_001; index += 1) lines.push(`SKU-${index},100`);
  assert.throws(() => parseMarketRows({
    bytes: csvBytes(lines.join("\n")),
    fileName: "too-many.csv",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-31",
  }), /最多导入 5000 条/);
});

test("排名约束使用真实 GMV 锚点做几何插值并反推自洽指标", () => {
  const base = {
    category: "净水设备", periodStart: "2026-07-01", periodEnd: "2026-07-31", scope: "全部", priceBandFilter: "全部", rankingDimension: "SKU",
    priceMidCents: 10_000, priceLowCents: 8_000, priceHighCents: 12_000,
    quantityMid: 50, quantityLow: 1, quantityHigh: 200, visitorsMid: 1_000, conversionLowBps: 10, conversionHighBps: 5_000,
  } as const;
  const rows = annotateRankBounds([
    { ...base, id: "a", rank: 1, gmvMidCents: 1_000_000, gmvLowCents: 900_000, gmvHighCents: 1_100_000, realGmvCents: 1_000_000 },
    { ...base, id: "b", rank: 2, gmvMidCents: 600_000, gmvLowCents: 100_000, gmvHighCents: 900_000 },
    { ...base, id: "c", rank: 3, gmvMidCents: 100_000, gmvLowCents: 90_000, gmvHighCents: 110_000, realGmvCents: 100_000 },
  ]);
  assert.equal(rows[0]?.effectiveGmvCents, 1_000_000);
  assert.equal(rows[1]?.effectiveGmvCents, 316_228);
  assert.equal(rows[2]?.effectiveGmvCents, 100_000);
  assert.ok(rows[0]!.effectiveGmvCents >= rows[1]!.effectiveGmvCents);
  assert.ok(rows[1]!.effectiveGmvCents >= rows[2]!.effectiveGmvCents);
  assert.equal(rows[1]?.estimatedQuantity, 32);
  assert.equal(rows[1]?.averageTransactionPriceCents, 9_882);
  assert.equal(aggregateMarketEstimates(rows, 3).effectiveGmvCents, 1_416_228);
});

test("正式主图价不参与成交件数和成交均价计算", () => {
  const input = {
    id: "separate-prices", category: "净水设备", periodStart: "2026-07-01", periodEnd: "2026-07-31", scope: "全部",
    rank: 1, gmvMidCents: 100_000, gmvLowCents: 100_000, gmvHighCents: 100_000,
    priceMidCents: 10_000, quantityMid: 10, quantityLow: 1, quantityHigh: 100, visitorsMid: 100,
    manualPriceCents: 50_000,
  };
  const [row] = annotateRankBounds([input]);
  assert.equal(row?.estimatedQuantity, 10);
  assert.equal(row?.averageTransactionPriceCents, 10_000);
});

test("商品榜单识别到正式主图价后以主图价展示成交均价", () => {
  assert.deepEqual(marketRankingPricePresentation({
    officialMarketPriceCents: 259_900,
    calculatedAverageTransactionPriceCents: 188_800,
    calculatedDiscountBps: 2736,
    calculatedDiscountReference: true,
  }), {
    averageTransactionPriceCents: 259_900,
    discountBps: 0,
    discountReference: false,
  });
  assert.deepEqual(marketRankingPricePresentation({
    officialMarketPriceCents: null,
    calculatedAverageTransactionPriceCents: 188_800,
    calculatedDiscountBps: null,
    calculatedDiscountReference: false,
  }), {
    averageTransactionPriceCents: 188_800,
    discountBps: null,
    discountReference: false,
  });
});

test("真实锚点超出榜单区间时保留真实值且不按粗区间截断", () => {
  const [row] = annotateRankBounds([{
    id: 1, category: "净水设备", periodStart: "2026-07-01", periodEnd: "2026-07-31", scope: "全部", rank: 1,
    gmvMidCents: 100_000, gmvLowCents: 90_000, gmvHighCents: 110_000, realGmvCents: 300_000,
    priceMidCents: 10_000, quantityMid: 10, quantityLow: 1, quantityHigh: 10, visitorsMid: 100,
  }]);
  assert.equal(row?.effectiveGmvCents, 300_000);
  assert.equal(row?.gmvOutOfBand, true);
  assert.equal(row?.estimatedQuantity, 30);
});

test("市场导入缺少商品编号列时拒绝文件", () => {
  assert.throws(() => parseMarketRows({
    bytes: csvBytes("商品名称,成交金额\n竞品 A,100"),
    fileName: "bad.csv",
    defaultStartDate: "2026-07-01",
    defaultEndDate: "2026-07-22",
  }), /商品编号/);
});

test("参考模块的日期区间、品牌名称和中文金额单位可以直接导入", () => {
  const result = parseMarketRows({
    bytes: csvBytes([
      "日期区间,排名,SKUID,商品名称,品牌名称,成交金额,成交商品件数,访客数",
      '2026-06-17 ~ 2026-06-23,1,JD-100,竞品净水机,品牌甲,"￥8,000 ~ ￥1万",10~50,100~300',
    ].join("\n")),
    fileName: "京东交易榜单.csv",
    defaultStartDate: "2026-01-01",
    defaultEndDate: "2026-01-02",
  });

  assert.equal(result.rows[0]?.periodStart, "2026-06-17");
  assert.equal(result.rows[0]?.periodEnd, "2026-06-23");
  assert.equal(result.rows[0]?.brand, "品牌甲");
  assert.equal(result.rows[0]?.gmvCents, 900_000);
  assert.equal(result.rows[0]?.quantity, 30);
  assert.equal(result.rows[0]?.visitors, 200);
});

test("京东商智月度 TOP SKU CSV 识别原图、商品链接和区间中位数", () => {
  const result = parseMarketRows({
    bytes: csvBytes([
      "日期区间,榜单单位,排名,商品名称,SKUID,图片URL(原图),商品链接,成交金额,成交单量,访客数,搜索点击次数,店铺名称,店铺类型",
      "2026-01-01_2026-01-31,SKU,1,商用净饮水机,10047090465567,https://img10.360buyimg.com/imgzone/jfs/t1/a.jpg,https://item.jd.com/10047090465567.html,￥8万~￥10万,5~10,600~800,600~800,示例旗舰店,pop",
    ].join("\n")),
    fileName: "京东商智_交易榜单_SKU_商用净饮水设备_2026-01至2026-06.csv",
    defaultStartDate: "2026-01-01",
    defaultEndDate: "2026-06-30",
    defaultCategory: "商用净饮水设备",
    defaultScope: "整体SKU",
  });
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0]?.periodStart, "2026-01-01");
  assert.equal(result.rows[0]?.periodEnd, "2026-01-31");
  assert.equal(result.rows[0]?.imageUrl, "https://img10.360buyimg.com/imgzone/jfs/t1/a.jpg");
  assert.equal(result.rows[0]?.productUrl, "https://item.jd.com/10047090465567.html");
  assert.equal(result.rows[0]?.gmvCents, 9_000_000);
  assert.equal(result.rows[0]?.quantity, 8);
  assert.equal(result.rows[0]?.visitors, 700);
  assert.equal(result.rows[0]?.searchClicks, 700);
});

test("市场图片缓存使用受限京东抓取、R2、鉴权路由并接入标注目录", async () => {
  const [cache, cacheRoute, backendImages, imageRoute, schemaCore, database, view, annotation] = await Promise.all([
    readFile(new URL("../lib/market/image-cache.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/images/cache/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/market/images.py", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/images/[hash]/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/schema-core.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8"),
    marketUiSource(),
    readFile(new URL("../lib/market/annotation-service.ts", import.meta.url), "utf8"),
  ]);
  assert.match(cache, /fetchAnnotationImage/);
  assert.match(cache, /SALES_IMPORT_FILES/);
  assert.match(cache, /MAX_CACHE_BATCH = 8/);
  assert.match(cache, /CACHE_CONCURRENCY = 4/);
  assert.match(cache, /runScheduledMarketImageCacheBatch/);
  assert.match(cacheRoute, /requireAppPrincipal\(\["admin"\]\)/);
  assert.match(cacheRoute, /export async function GET/);
  assert.match(cacheRoute, /operation: "image_cache_job"/);
  assert.match(cacheRoute, /action: "create_image_cache_job"/);
  assert.match(cacheRoute, /requestDjangoMarketService/);
  assert.match(backendImages, /if action == "create_image_cache_job"/);
  assert.doesNotMatch(cacheRoute, /runScheduledMarketImageCacheBatch|cacheMarketImages/);
  assert.match(imageRoute, /requireAppPrincipal\(\)/);
  assert.match(imageRoute, /x-content-type-options/);
  assert.match(schemaCore, /CREATE TABLE IF NOT EXISTS market_image_cache/);
  assert.match(database, /\/api\/market\/images\//);
  assert.match(view, /pollMarketImageCacheJob/);
  assert.match(view, /启动后台图片缓存/);
  assert.match(view, /停止查看/);
  assert.doesNotMatch(view, /round\s*<\s*50|stopImageCacheRef/);
  assert.match(annotation, /market_image_cache/);
});
