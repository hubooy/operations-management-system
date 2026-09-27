import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { canCloseMarketSkuEditor } from "../app/market-view";

function sourceSection(source: string, start: string, end: string): string {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `missing source section start: ${start}`);
  assert.notEqual(endIndex, -1, `missing source section end: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("market SKU editor cannot close while its save action is in flight", () => {
  assert.equal(canCloseMarketSkuEditor(""), true);
  assert.equal(canCloseMarketSkuEditor("infer_brand:sku-1"), true);
  assert.equal(canCloseMarketSkuEditor("update_sku_master"), false);
});

test("trend drawer uses the shared portal dialog with explicit initial focus", async () => {
  const source = await readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8");
  const trend = sourceSection(source, "function TrendDrawer", "function CompareWorkspace");

  assert.match(trend, /<Dialog open onClose=\{onClose\}/);
  assert.match(trend, /dialogId="market-trend-dialog"/);
  assert.match(trend, /className="market-trend-drawer"/);
  assert.match(trend, /initialFocusRef=\{closeButtonRef\}/);
  assert.match(trend, /ref=\{closeButtonRef\}[^>]*aria-label="关闭商品月度趋势"/);
  assert.doesNotMatch(trend, /modal-backdrop|role="dialog"|aria-modal|stopPropagation/);
});

test("SKU editor delegates escape and backdrop closure to Dialog and guards saving", async () => {
  const source = await readFile(new URL("../app/market-master-admin-panel.tsx", import.meta.url), "utf8");
  const editor = sourceSection(source, "{editingSku && <Dialog", "</Dialog>}");

  assert.match(editor, /onClose=\{closeSkuEditor\}/);
  assert.match(editor, /dialogId="market-sku-editor-dialog"/);
  assert.match(editor, /className="panel market-sku-editor"/);
  assert.match(editor, /initialFocusRef=\{skuEditorInitialFocusRef\}/);
  assert.match(editor, /ref=\{skuEditorInitialFocusRef\}/);
  assert.match(editor, /disabled=\{skuEditorSaving\}[^>]*onClick=\{closeSkuEditor\}[^>]*aria-label="关闭 SKU 全部数据编辑"/);
  assert.doesNotMatch(editor, /market-editor-backdrop|role="dialog"|aria-modal|role="presentation"|onMouseDown/);

  assert.match(source, /busyActionRef\.current = busyAction;/);
  assert.match(source, /if \(!canCloseMarketSkuEditor\(busyActionRef\.current\)\) return;/);
});

test("AI annotation workspace is loaded only when its nested tab is mounted", async () => {
  const source = await readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(source, /^import MarketAnnotationView from "\.\/market-annotation-view";/m);
  assert.match(source, /Component: MarketAnnotationView \} = createReloadableLazy\("market", \(\) => import\("\.\/market-annotation-view"\)\);/);
  assert.match(source, /databaseArea === "annotation" \? <Suspense fallback=/);
  assert.match(source, /role="status"[^>]*>.*正在加载 AI 标注工作区…/);
  assert.match(source, /<MarketAnnotationView currentUser=\{currentUser\} embedded \/><\/Suspense>/);
});
