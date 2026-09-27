import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("annual target template and export endpoints stay authenticated and shape-bounded", async () => {
  const [importRoute, exportRoute, sales, library] = await Promise.all([
    readFile(new URL("../app/api/finance/targets/import/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/finance/targets/export/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/finance/annual-target-workbook.ts", import.meta.url), "utf8"),
  ]);

  // Template download is a read: any authenticated unrestricted principal may fetch it.
  assert.match(importRoute, /export async function GET\(\) \{/);
  assert.match(importRoute, /requireAppPrincipal\(\["viewer", "analyst", "operator", "admin"\]\)/);
  assert.match(importRoute, /requireUnrestrictedDataScope\(principal, "经营目标"\)/);
  assert.match(importRoute, /annualTargetTemplateWorkbook\(shops\)/);
  assert.match(importRoute, /店铺年度目标导入模板\.xlsx/);
  // The template pre-fills known shop labels so users only fill in target values.
  assert.match(importRoute, /query: new URLSearchParams\(\{ view: "options" \}\)/);
  assert.match(importRoute, /service: "reader"/);
  assert.match(importRoute, /Promise<AnnualTargetTemplateShop\[\]>/);
  assert.match(importRoute, /\} catch \{\s*return \[\];/);

  // Export reads targets through the reader service and streams an xlsx attachment.
  assert.match(exportRoute, /requireAppPrincipal\(\["viewer", "analyst", "operator", "admin"\]\)/);
  assert.match(exportRoute, /requireUnrestrictedDataScope\(principal, "经营目标"\)/);
  assert.match(exportRoute, /FINANCE_TARGETS_PATH/);
  assert.match(exportRoute, /service: "reader"/);
  assert.match(exportRoute, /buildAnnualTargetExportWorkbook\(year, rows\)/);
  assert.match(exportRoute, /导出年份必须且只能是 YYYY/);
  assert.match(exportRoute, /年度目标数量超过/);

  // The template columns must match what the importer accepts.
  assert.match(library, /"店铺",\s*"负责人",\s*"销售目标",\s*"利润目标",\s*"大毛利率目标",\s*"推广费目标",/);

  // The page exposes template download and export for every reader, import stays admin-only.
  assert.match(sales, /href="\/api\/finance\/targets\/import" download>下载导入模板<\/a>/);
  assert.match(sales, /\/api\/finance\/targets\/export\?year=\$\{targetYear\}/);
  assert.match(sales, /\{canManageTargets && <><input ref=\{annualTargetFileRef\}/);
  assert.match(sales, /目标进度情况/);
});

test("annual progress table delete control reuses the audited removeTarget flow", async () => {
  const [sales, progress] = await Promise.all([
    readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/finance-annual-progress-view.tsx", import.meta.url), "utf8"),
  ]);

  // The progress view exposes an operations column only for managers and a delete
  // button only on rows that actually have a target; deletion is delegated to the
  // parent's audited removeTarget (confirm + reason + DELETE + refresh).
  assert.match(progress, /onDelete: \(row: AnnualShop\) => void/);
  assert.match(progress, /\{row\.target && <button type="button" className="danger" disabled=\{busy\} onClick=\{\(\) => onDelete\(row\)\}>删除<\/button>\}/);
  assert.match(sales, /onDelete=\{\(row\) => \{ if \(!row\.target\) return; void removeTarget\(row\.target\); \}\}/);
  assert.match(sales, /const removeTarget = async \(item: FinanceTarget\) =>/);
});
