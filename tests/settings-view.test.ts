import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import {
  canEditOperatingSettings,
  canEditDingTalkSettings,
  nextMarketSettingsPane,
  nextSettingsTab,
  marketStatusMatchesCurrentRequest,
  shouldLoadMarketSettingsStatus,
} from "../app/settings-view";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("settings tabs implement deterministic automatic-activation roving", () => {
  assert.equal(nextSettingsTab("parameters", "ArrowRight"), "master");
  assert.equal(nextSettingsTab("parameters", "ArrowLeft"), "permissions");
  assert.equal(nextSettingsTab("permissions", "ArrowRight"), "parameters");
  assert.equal(nextSettingsTab("master", "ArrowDown"), "warehouses");
  assert.equal(nextSettingsTab("warehouses", "ArrowRight"), "dingtalk");
  assert.equal(nextSettingsTab("master", "ArrowUp"), "parameters");
  assert.equal(nextSettingsTab("master", "Home"), "parameters");
  assert.equal(nextSettingsTab("master", "End"), "permissions");
  assert.equal(nextSettingsTab("master", "Enter"), null);
});

test("DingTalk robot settings allow operators and administrators to manage delivery", () => {
  assert.equal(canEditDingTalkSettings({ role: "admin" }), true);
  assert.equal(canEditDingTalkSettings({ role: "operator" }), true);
  assert.equal(canEditDingTalkSettings({ role: "analyst" }), false);
  assert.equal(canEditDingTalkSettings({ role: "viewer" }), false);
  assert.equal(canEditDingTalkSettings(null), false);
});

test("market settings panes implement deterministic automatic-activation roving", () => {
  assert.equal(nextMarketSettingsPane("master-data", "ArrowRight"), "imports");
  assert.equal(nextMarketSettingsPane("master-data", "ArrowLeft"), "annotation");
  assert.equal(nextMarketSettingsPane("imports", "ArrowDown"), "annotation");
  assert.equal(nextMarketSettingsPane("annotation", "ArrowRight"), "master-data");
  assert.equal(nextMarketSettingsPane("imports", "Home"), "master-data");
  assert.equal(nextMarketSettingsPane("imports", "End"), "annotation");
  assert.equal(nextMarketSettingsPane("imports", "Enter"), null);
});

test("market settings status is requested only for the import and task pane", () => {
  assert.equal(shouldLoadMarketSettingsStatus("master", "master-data"), false);
  assert.equal(shouldLoadMarketSettingsStatus("master", "annotation"), false);
  assert.equal(shouldLoadMarketSettingsStatus("parameters", "imports"), false);
  assert.equal(shouldLoadMarketSettingsStatus("permissions", "imports"), false);
  assert.equal(shouldLoadMarketSettingsStatus("master", "imports"), true);
});

test("market settings status belongs only to the current request and reload scope", () => {
  assert.equal(marketStatusMatchesCurrentRequest("request-2", "request-2", 2, 2), true);
  assert.equal(marketStatusMatchesCurrentRequest("", "", 2, 2), false);
  assert.equal(marketStatusMatchesCurrentRequest("request-2", "request-1", 2, 2), false);
  assert.equal(marketStatusMatchesCurrentRequest("request-2", "request-2", 1, 2), false);
});

test("only administrators can edit operating settings", async () => {
  assert.equal(canEditOperatingSettings({ role: "admin" }), true);
  assert.equal(canEditOperatingSettings({ role: "operator" }), false);
  assert.equal(canEditOperatingSettings({ role: "viewer" }), false);
  assert.equal(canEditOperatingSettings({ role: "analyst" }), false);
  assert.equal(canEditOperatingSettings(null), false);

  const settings = await source("../app/settings-view.tsx");
  assert.match(settings, /if \(!canEdit \|\| !settings \|\| saving\) return;/);
  assert.match(settings, /<input[^>]+disabled=\{!canEdit\}/);
  assert.match(settings, /role="switch"[\s\S]*?disabled=\{!canEdit\}/);
  assert.match(settings, /disabled=\{!canEdit \|\| saving \|\| settingsState === "loading"\}/);
  assert.match(settings, /当前为只读模式/);
  assert.match(settings, /仅管理员可修改并保存系统设置/);
});

test("market settings code is lazy and each internal pane mounts only its own work", async () => {
  const settings = await source("../app/settings-view.tsx");
  assert.doesNotMatch(settings, /^import .* from "\.\/market-view"/m);
  assert.doesNotMatch(settings, /^import .* from "\.\/market-annotation-view"/m);
  assert.match(settings, /createReloadableLazy\("settings", \(\) => import\("\.\/market-master-admin-panel"\)\)/);
  assert.match(settings, /createReloadableLazy<Market(?:DataImport|Workflow)PanelProps>\("settings", \(\) => import\("\.\/market-view"\)/);
  assert.match(settings, /createReloadableLazy\("settings", \(\) => import\("\.\/market-annotation-view"\)\)/);
  assert.match(settings, /useState<MarketSettingsPane>\("master-data"\)/);
  assert.match(settings, /marketPane === "master-data" && <section[\s\S]*?<LazyMarketMasterAdminPanel/);
  assert.match(settings, /marketPane === "imports" && <section[\s\S]*?<LazyMarketDataImportPanel[\s\S]*?<LazyMarketWorkflowPanel/);
  assert.match(settings, /marketPane === "annotation" && <section[\s\S]*?<LazyMarketAnnotationView/);
  assert.match(settings, /if \(!shouldLoadMarketSettingsStatus\(activeTab, marketPane\)\) \{[\s\S]*?invalidateMarketSettingsStatus\(\);[\s\S]*?return;[\s\S]*?loadMarketSettingsStatus\(reloadScope\)/);
  assert.match(settings, /fetch\("\/api\/market\/master\?view=settings_status", \{ cache: "no-store", signal: controller\.signal \}\)/);
  assert.doesNotMatch(settings, /fetch\("\/api\/market\/overview"/);
});

test("settings and market requests abort superseded work and reject late responses", async () => {
  const settings = await source("../app/settings-view.tsx");
  assert.match(settings, /settingsControllerRef\.current\?\.abort\(\)/);
  assert.match(settings, /generation !== settingsGenerationRef\.current \|\| controller\.signal\.aborted/);
  assert.match(settings, /fetch\("\/api\/settings", \{ cache: "no-store", signal: controller\.signal \}\)/);
  assert.match(settings, /method: "PUT",[\s\S]*?signal: controller\.signal/);
  assert.match(settings, /generation !== saveGenerationRef\.current \|\| controller\.signal\.aborted/);
  assert.match(settings, /marketControllerRef\.current\?\.abort\(\)/);
  assert.match(settings, /generation !== marketGenerationRef\.current \|\| controller\.signal\.aborted/);
  assert.match(settings, /const requestKey = `market-settings:\$\{reloadScope\}:\$\{generation\}`/);
  const marketStatusLoader = settings.slice(
    settings.indexOf("const loadMarketSettingsStatus"),
    settings.indexOf("const invalidateMarketSettingsStatus"),
  );
  assert.doesNotMatch(marketStatusLoader, /setMarketData\(null\)/);
  assert.match(marketStatusLoader, /setMarketState\("loading"\);[\s\S]*?setMarketStatusRequestKey\(requestKey\)/);
  assert.match(settings, /setMarketData\(payload\);[\s\S]*?setMarketStatusScopeKey\(requestKey\);[\s\S]*?setMarketStatusReloadScope\(reloadScope\)/);
  assert.match(settings, /return \(\) => \{[\s\S]*?window\.clearTimeout\(timer\);[\s\S]*?invalidateMarketSettingsStatus\(\)/);
  assert.match(settings, /const currentMarketData = shouldLoadMarketSettingsStatus\(activeTab, marketPane\)[\s\S]*?marketStatusIsCurrent \|\| marketState === "loading" \|\| marketState === "error" \|\| marketStatusReloadScope < marketReloadKey/);
  assert.match(settings, /\{currentMarketData && <Suspense[\s\S]*?<LazyMarketDataImportPanel[^>]+data=\{currentMarketData\}[\s\S]*?<LazyMarketWorkflowPanel data=\{currentMarketData\}/);
  assert.doesNotMatch(settings, /<LazyMarketDataImportPanel[^>]+data=\{marketData\}/);
  assert.match(settings, /系统设置加载失败/);
  assert.match(settings, />重新加载<\/button>/);
  assert.match(settings, /导入与任务数据加载失败/);
});

test("settings tab and panel semantics are linked and keyboard operable", async () => {
  const settings = await source("../app/settings-view.tsx");
  assert.match(settings, /role="tablist" aria-label="系统设置工作区"/);
  assert.match(settings, /role="tab"/);
  assert.match(settings, /aria-selected=\{activeTab === tab\}/);
  assert.match(settings, /tabIndex=\{activeTab === tab \? 0 : -1\}/);
  assert.match(settings, /onKeyDown=\{\(event\) => handleTabKeyDown\(event, tab\)\}/);
  assert.match(settings, /role="tabpanel"/);
  assert.match(settings, /aria-labelledby="settings-tab-parameters"/);
  assert.match(settings, /aria-labelledby="settings-tab-master"/);
  assert.match(settings, /aria-labelledby="settings-tab-warehouses"/);
  assert.match(settings, /aria-labelledby="settings-tab-dingtalk"/);
  assert.match(settings, /aria-labelledby="settings-tab-permissions"/);
  assert.match(settings, /role="tablist" aria-label="主数据与映射工作区"/);
  assert.match(settings, /aria-selected=\{marketPane === pane\}/);
  assert.match(settings, /aria-controls=\{`settings-master-panel-\$\{pane\}`\}/);
  assert.match(settings, /tabIndex=\{marketPane === pane \? 0 : -1\}/);
  assert.match(settings, /onKeyDown=\{\(event\) => handleMarketPaneKeyDown\(event, pane\)\}/);
  assert.match(settings, /aria-labelledby="settings-master-tab-master-data"/);
  assert.match(settings, /aria-labelledby="settings-master-tab-imports"/);
  assert.match(settings, /aria-labelledby="settings-master-tab-annotation"/);
  assert.match(settings, /role="switch"/);
  assert.match(settings, /aria-checked=\{settings\[key\]\}/);
});

test("DingTalk robot is a dedicated system settings workspace", async () => {
  const [settings, robot, followup, navigation] = await Promise.all([
    source("../app/settings-view.tsx"),
    source("../app/dingtalk-robot-settings.tsx"),
    source("../app/new-product-sales-followup-view.tsx"),
    source("../app/shell/navigation-catalog.ts"),
  ]);
  assert.match(navigation, /settings: \{ defaultView: "parameters", views: \["parameters", "master", "warehouses", "dingtalk", "permissions"\] \}/);
  assert.match(settings, /dingtalk: "钉钉机器人"/);
  assert.match(settings, /<LazyDingTalkRobotSettings canWrite=\{canEditDingTalk\}/);
  for (const label of ["钉钉机器人", "Stream 模式", "机器人名称", "目标群名称", "图片投递方式", "保存机器人配置"]) {
    assert.match(robot, new RegExp(label));
  }
  assert.match(robot, /new-product-weekly-report-config/);
  assert.match(robot, /本机 DWS 安全凭据库/);
  assert.match(robot, /安全边界固定为唯一企业机器人“志高助手”/);
  assert.match(robot, /安全边界固定为唯一群聊“测试群聊”/);
  assert.match(followup, /\?module=settings&view=dingtalk/);
  assert.doesNotMatch(followup, /const saveConfig/);
});

test("page lazy-loads the extracted settings implementation", async () => {
  const page = await source("../app/page.tsx");
  assert.doesNotMatch(page, /function SettingsView\(/);
  assert.match(page, /Component: SettingsView \} = createReloadableLazy\("settings", \(\) => import\("\.\/settings-view"\)\)/);
  assert.match(page, /settings: \([^\n]+<SettingsView/);
  assert.doesNotMatch(page, /type OperatingSettings =/);
  assert.doesNotMatch(page, /MarketMasterAdminPanel|MarketDataImportPanel|MarketWorkflowPanel|MarketAnnotationView/);
});

test("warehouse mapping is a dedicated lazy-loaded system settings workspace", async () => {
  const [settings, mapping, config] = await Promise.all([
    source("../app/settings-view.tsx"),
    source("../app/warehouse-mapping-settings.tsx"),
    source("../config/inventory-warehouse-mapping.json"),
  ]);
  assert.match(settings, /createReloadableLazy\("settings", \(\) => import\("\.\/warehouse-mapping-settings"\)\)/);
  assert.match(settings, /warehouses: "仓库映射"/);
  assert.match(settings, /activeTab === "warehouses"[\s\S]*?<LazyWarehouseMappingSettings/);
  for (const label of ["仓库归类明细", "自定义仓库类型", "系统归类", "计入库存", "确认状态", "待确认", "搜索仓库映射", "导出映射", "导入映射", "编辑"]) {
    assert.match(mapping, new RegExp(label));
  }
  assert.match(config, /"膳师傅仓库"[\s\S]*?"label": "代发仓"[\s\S]*?"includeInInventory": true/);
});
