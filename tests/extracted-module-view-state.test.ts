import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("extracted top-level views use the shell-controlled module view contract", async () => {
  const [operations, market, n8n] = await Promise.all([
    source("../app/operations-view.tsx"),
    source("../app/market-view.tsx"),
    source("../app/n8n-workflow-view.tsx"),
  ]);

  assert.match(operations, /moduleView: ModuleViewKey<"workflow">/);
  assert.match(operations, /onModuleViewChange: \(view: ModuleViewKey<"workflow">\)/);
  assert.match(operations, /onModuleViewChange\(tab\)/);
  assert.doesNotMatch(operations, /window\.history|addEventListener\("popstate"/);

  assert.match(market, /moduleView: ModuleViewKey<"market">/);
  assert.match(market, /onModuleViewChange: \(view: ModuleViewKey<"market">\)/);
  assert.match(market, /onModuleViewChange\(section\)/);
  assert.doesNotMatch(market, /window\.history|addEventListener\("popstate"/);

  assert.match(n8n, /moduleView: ModuleViewKey<"n8n_workflows">/);
  assert.match(n8n, /onModuleViewChange: \(view: ModuleViewKey<"n8n_workflows">\)/);
  assert.match(n8n, /onModuleViewChange\(key\)/);
  assert.doesNotMatch(n8n, /window\.history|addEventListener\("popstate"/);
});

test("extracted view tabs expose roving focus and linked tab panels", async () => {
  const [operations, market, n8n, annotation] = await Promise.all([
    source("../app/operations-view.tsx"),
    source("../app/market-view.tsx"),
    source("../app/n8n-workflow-view.tsx"),
    source("../app/market-annotation-view.tsx"),
  ]);

  for (const view of [operations, market, n8n, annotation]) {
    assert.match(view, /role="tablist"/);
    assert.match(view, /role="tab"/);
    assert.match(view, /aria-controls=/);
    assert.match(view, /aria-selected=/);
    assert.match(view, /tabIndex=/);
    assert.match(view, /ArrowRight/);
    assert.match(view, /ArrowLeft/);
    assert.match(view, /role="tabpanel"/);
  }

  assert.match(market, /market-settings-panel-/);
  assert.match(market, /market-database-panel-/);
  assert.match(annotation, /annotation-review-panel-list/);
  assert.match(annotation, /annotation-review-panel-gallery/);
});

test("sales category retains only its analytical filters while the shell owns sales category view", async () => {
  const salesCategory = await source("../app/sales-category-view.tsx");
  const ownedKeys = salesCategory.match(/const categoryOwnedUrlKeys = \[([\s\S]*?)\] as const;/)?.[1] ?? "";

  assert.ok(ownedKeys.length > 0);
  assert.doesNotMatch(ownedKeys, /["']view["']/);
  assert.match(salesCategory, /new URL\(window\.location\.href\)/);
  assert.match(salesCategory, /window\.addEventListener\("popstate", onPopState\)/);
  assert.match(salesCategory, /role="status"/);
  assert.match(salesCategory, /role="alert"/);
});
