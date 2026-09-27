import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { getInventoryCleanupStrategy } from "../app/inventory-workbench-panels";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("滞销清理策略按风险状态给出有界的人工执行建议", () => {
  assert.equal(getInventoryCleanupStrategy("stagnant").label, "促销 / 调拨 / 清退");
  assert.equal(getInventoryCleanupStrategy("slow").label, "控补货并提动销");
  assert.equal(getInventoryCleanupStrategy("aged").label, "调拨 / 人工复核");
  assert.equal(getInventoryCleanupStrategy("no_stock").label, "等待快照确认");
  assert.equal(getInventoryCleanupStrategy("healthy").label, "持续观察");
});

test("库存五个工作页补充缺口明细、全量汇总和执行闭环", async () => {
  const [view, panels] = await Promise.all([
    source("../app/inventory-module-view.tsx"),
    source("../app/inventory-workbench-panels.tsx"),
  ]);

  assert.doesNotMatch(view, /InventoryOverviewRulePanel|库存决策规则/);
  assert.match(view, /<InventoryAgeSummaryPanel/);
  assert.match(view, /<InventoryPlanWorkflowPanel summary=\{overview\.planSummary\}/);
  assert.match(view, /<InventoryStalePlaybookPanel/);
  assert.match(view, /<InventoryInboundActionPanel/);
  assert.match(view, /品牌 \/ 品类/);
  assert.match(view, /京东仓周转/);
  assert.match(view, /导出当前页 CSV/);

  assert.doesNotMatch(panels, /库存决策规则/);
  assert.match(panels, /10 个标准区间/);
  assert.match(panels, /系统建议 → 人工确认 → 采购执行 → 库存快照复盘/);
  assert.match(panels, /系统只创建运营事项，不会自动删除、调减或重导库存/);
  assert.match(panels, /当前不展示伪造差异/);
});

test("库存健康明细与备货计划钉钉协作使用统一受控口径", async () => {
  const [view, query, route, importRoute, importWorkbook, dingTalkRoute, groupRoute, model, service, groupService] = await Promise.all([
    source("../app/inventory-module-view.tsx"),
    source("../backend/inventory/query.py"),
    source("../app/api/inventory/replenishment/route.ts"),
    source("../app/api/inventory/replenishment/import/route.ts"),
    source("../lib/inventory/replenishment-workbook.ts"),
    source("../app/api/inventory/replenishment/dingtalk/route.ts"),
    source("../app/api/inventory/replenishment/dingtalk/group/route.ts"),
    source("../backend/inventory/models.py"),
    source("../backend/inventory/dingtalk_sync.py"),
    source("../backend/inventory/dingtalk_group_message.py"),
  ]);

  for (const label of [
    "货品编号", "货品名称", "品牌", "分类", "供应商", "京东仓库", "京东仓销量",
    "供应商库存", "供应商销量", "供应商周转", "售后仓库存", "广东仓库存", "样品仓库存", "菜鸟仓库存",
    "海外仓库存", "虚拟仓库存", "异常仓库存", "自营库存",
    "总库存金额", "总周转天数", "建议补货", "预警", "创建备货计划",
  ]) assert.match(view, new RegExp(label));
  for (const label of [
    "对应采购", "入库库房", "现有库存", "近30天总销量", "备货数量",
    "预计消耗周期\(天\)", "下单日期", "备货类型", "对应运营", "部门", "预计到货日", "是否验货", "备注",
  ]) assert.ok(view.includes(label.replaceAll("\\(", "(").replaceAll("\\)", ")")), `missing ${label}`);

  assert.doesNotMatch(view, /<h2>销量近30天<\/h2>/);
  assert.match(view, /<h2>库存健康明细（近30天）<\/h2>/);
  assert.equal((view.match(/<InventoryThirtyDayTable samples=\{overview\.mapping\.samples\}/g) ?? []).length, 1);
  assert.doesNotMatch(view, /InventoryKpiCard label="可用库存"/);
  assert.doesNotMatch(view, /InventoryKpiCard label=\{overview\.metrics\.stockValueComplete \? "库存货值"/);
  assert.doesNotMatch(view, /InventoryKpiCard label="平均可售天数"/);
  assert.doesNotMatch(view, /InventoryKpiCard label="待补货货品"/);
  assert.doesNotMatch(view, /库存数据质量门禁已暂停精确补货/);
  assert.match(view, /仅统计京东仓、天猫仓、广东仓、自营仓/);
  assert.match(view, /item\.warehouseLabel/);
  assert.match(view, /item\.includedInInventory \? "计入库存" : "排除库存"/);
  for (const label of ["无库存可用", "紧急补货", "补货预警", "积压风险", "低周转", "库存健康"]) {
    assert.match(view, new RegExp(label));
  }
  assert.match(view, /库存周转大于 180 天；工厂代发仓不计入/);
  assert.match(query, /HEALTH_WAREHOUSE_CATEGORIES/);
  assert.match(query, /\{"jd", "cainiao", "guangdong", "selfOperated"\}/);
  assert.match(view, /department: "志高项目组"/);
  assert.match(query, /mapping_samples = _mapping_samples\(workbench_filtered, 30/);
  assert.match(query, /"supplier": row\.supplier\.strip\(\) or/);
  assert.match(route, /"manual"/);
  assert.match(model, /expected_arrival_date = models\.DateField/);
  assert.match(model, /requires_inspection = models\.BooleanField/);
  assert.match(view, /syncPlanToDingTalk/);
  assert.match(view, /requestPlanDingTalkSync\(payload\.item\.id\)/);
  assert.match(view, /确认并提交钉钉/);
  assert.match(view, /expectedConsumptionDays: planDraft\.expectedConsumptionDays/);
  assert.match(view, /step=\{0\.1\} value=\{planDraft\.expectedConsumptionDays \?\? ""\}/);
  assert.match(route, /"expectedConsumptionDays"/);
  assert.match(view, /钉钉已提交/);
  assert.match(view, /更新钉钉记录/);
  assert.match(dingTalkRoute, /INVENTORY_REPLENISHMENT_DINGTALK_PATH/);
  assert.match(service, /运营管理系统备货计划ID/);
  assert.match(service, /legacy_markers/);
  assert.match(service, /TERUISI备货计划ID/);
  assert.match(service, /_verify_record/);
  assert.match(view, /下载导入模板/);
  assert.match(view, /导入备货计划/);
  assert.match(view, /一键确认并提交钉钉（/);
  assert.match(view, /批量提交钉钉表（/);
  assert.match(view, /全选本页草稿和已确认备货计划/);
  assert.match(view, /confirmAndSyncDraftPlans/);
  assert.match(view, /preservedConfirmedIds/);
  assert.match(view, /发送钉钉群（/);
  assert.match(view, /确认发送/);
  assert.match(groupRoute, /志高\/特睿思备货计划群/);
  assert.doesNotMatch(groupRoute, /WORKFLOW_NEW_PRODUCT_WEEKLY_REPORT_CONFIG_PATH/);
  assert.match(groupRoute, /INVENTORY_REPLENISHMENT_DINGTALK_GROUP_PATH/);
  assert.match(groupService, /\*\*▸ \{supplier\}（\{len\(items\)\} 条）\*\*/);
  assert.match(groupService, /--markdown/);
  assert.match(groupService, /providerMessageSha256/);
  assert.match(groupService, /--at-user-ids/);
  assert.match(groupService, /ReplenishmentGroupDelivery/);
  assert.match(importRoute, /parseReplenishmentWorkbook/);
  assert.match(importRoute, /replenishment-import:/);
  assert.match(importWorkbook, /单次最多导入/);
  assert.match(importWorkbook, /留空默认“已确认”/);
});

test("库存工作台继续披露既有数据质量与业务边界", async () => {
  const [panels, docs] = await Promise.all([
    source("../app/inventory-workbench-panels.tsx"),
    source("../docs/INVENTORY_MANAGEMENT.md"),
  ]);

  assert.match(docs, /`刷刷仓` 始终排除/);
  assert.match(docs, /成本不完整时页面必须标注覆盖率/);
  assert.match(docs, /正向销量/);
  assert.match(panels, /人工确认 · 不自动下单/);
  assert.match(panels, /计算口径，不替代京东原生口径/);
});
