import assert from "node:assert/strict";
import test from "node:test";

import {
  getDefaultModuleView,
  getModuleViews,
  isModuleViewKey,
  moduleKeys,
  moduleViewCatalog,
  type ModuleKey,
  type ModuleViewKey,
} from "../app/shell/navigation-catalog";
import {
  normalizeModuleView,
  parseModuleView,
  serializeModuleViewLocation,
} from "../app/shell/module-view-contract";
import {
  normalizeShellLocation,
  parseShellLocation,
  serializeShellLocation,
  updateModuleViewLocation,
} from "../app/shell/navigation-contract";

const expectedViews = {
  n8n_workflows: {
    defaultView: "jackyun",
    views: ["jackyun", "tmall", "jd", "jd_market", "jd_promotion", "jd_promotion_cut_meat"],
  },
  dashboard: { defaultView: "overview", views: ["overview"] },
  shop: { defaultView: "analysis", views: ["analysis", "outlets", "platforms", "products", "promotion"] },
  market: { defaultView: "ranking", views: ["ranking", "overview", "compare", "settings"] },
  customer_service: { defaultView: "conversations", views: ["conversations"] },
  sales: { defaultView: "overview", views: ["overview", "channel", "category", "finance", "targets"] },
  inventory: { defaultView: "overview", views: ["overview", "age", "plan", "stale", "inbound", "guangdong"] },
  product: { defaultView: "overview", views: ["overview", "calculator"] },
  workflow: { defaultView: "plan", views: ["plan", "inspection", "reviews", "launch", "launch-followup", "variables"] },
  import: { defaultView: "files", views: ["files", "history", "chains"] },
  settings: { defaultView: "parameters", views: ["parameters", "master", "warehouses", "dingtalk", "permissions"] },
  ai: { defaultView: "assistant", views: ["assistant", "agents", "memory", "space", "management", "scheduled", "configuration", "reports", "skills", "pipelines"] },
} as const;

test("module view registry covers every shell module with a unique, legal default", () => {
  assert.deepEqual(moduleViewCatalog, expectedViews);
  assert.deepEqual(Object.keys(moduleViewCatalog), [...moduleKeys]);
  for (const moduleKey of moduleKeys) {
    const views = getModuleViews(moduleKey);
    assert.ok(views.includes(getDefaultModuleView(moduleKey)));
    assert.equal(new Set(views).size, views.length);
  }
  assert.equal(isModuleViewKey("sales", "finance"), true);
  assert.equal(isModuleViewKey("sales", "ranking"), false);
  assert.equal(isModuleViewKey("ai", "sandbox"), false);
  assert.equal(parseModuleView("ai", "/?module=ai&view=sandbox"), "assistant");
});

test("every registered view round-trips as a refresh-safe deep link", () => {
  for (const moduleKey of moduleKeys) {
    for (const view of getModuleViews(moduleKey)) {
      const url = serializeModuleViewLocation(moduleKey, view, `/console?module=${moduleKey}&tenant=alpha#workspace`);
      assert.equal(parseModuleView(moduleKey, url), view);
      assert.equal(new URL(url, "https://example.test").searchParams.get("tenant"), "alpha");
      assert.ok(url.endsWith("#workspace"));
    }
  }
});

test("missing, unknown, cross-module, and duplicate view values fail closed to the module default", () => {
  for (const input of [
    "/?module=market",
    "/?module=market&view=not-real",
    "/?module=market&view=finance",
    "/?module=market&view=compare&view=settings",
  ]) {
    assert.equal(parseModuleView("market", input), "ranking");
  }
  assert.equal(normalizeModuleView("workflow", undefined), "plan");
  assert.equal(normalizeModuleView("workflow", "inspection"), "inspection");
  assert.equal(normalizeShellLocation("/?module=market&view=compare&view=settings"), "/?module=market");
});

test("view serialization replaces duplicates, omits defaults, and preserves non-shell URL state", () => {
  assert.equal(
    serializeModuleViewLocation(
      "market",
      "compare",
      "/console?tenant=alpha&view=overview&view=settings&campaign=summer#ranking",
    ),
    "/console?tenant=alpha&campaign=summer&view=compare#ranking",
  );
  assert.equal(
    serializeModuleViewLocation("market", "ranking", "/console?tenant=alpha&view=compare#ranking"),
    "/console?tenant=alpha#ranking",
  );
  assert.equal(
    serializeModuleViewLocation(
      "sales",
      "not-real" as ModuleViewKey<"sales">,
      "/console?tenant=alpha&view=finance#sales",
    ),
    "/console?tenant=alpha#sales",
  );
});

test("integrated shell parsing keeps module, view, period, unrelated query, and hash stable", () => {
  const deepLink = "/console?tenant=alpha&module=market&view=compare&period=custom&from=2026-08-01&to=2026-08-18#matrix";
  assert.deepEqual(parseShellLocation(deepLink), {
    module: "market",
    view: "compare",
    period: { kind: "custom", from: "2026-08-01", to: "2026-08-18" },
  });
  assert.equal(normalizeShellLocation(deepLink), deepLink);
  assert.equal(serializeShellLocation(parseShellLocation(deepLink), deepLink), deepLink);
});

test("legacy shell callers that omit view preserve it only when staying in the same module", () => {
  assert.equal(
    serializeShellLocation(
      { module: "market", period: { kind: "last15" } },
      "/console?tenant=alpha&module=market&view=compare&period=last7#matrix",
    ),
    "/console?tenant=alpha&module=market&view=compare&period=last15#matrix",
  );
  assert.equal(
    serializeShellLocation(
      { module: "sales", period: { kind: "last15" } },
      "/console?tenant=alpha&module=market&view=compare&period=last7#matrix",
    ),
    "/console?tenant=alpha&module=sales&period=last15#matrix",
  );
});

test("pure view transitions produce independent history entries that parse on back and forward", () => {
  const rankingUrl = "/console?tenant=alpha&module=market&period=last7#ranking";
  const overviewUrl = updateModuleViewLocation(rankingUrl, "market", "overview");
  const compareUrl = updateModuleViewLocation(overviewUrl, "market", "compare");

  assert.equal(rankingUrl, "/console?tenant=alpha&module=market&period=last7#ranking");
  assert.equal(overviewUrl, "/console?tenant=alpha&module=market&view=overview&period=last7#ranking");
  assert.equal(compareUrl, "/console?tenant=alpha&module=market&view=compare&period=last7#ranking");
  assert.equal(parseShellLocation(rankingUrl).view, "ranking");
  assert.equal(parseShellLocation(overviewUrl).view, "overview");
  assert.equal(parseShellLocation(compareUrl).view, "compare");
  assert.equal(parseShellLocation(overviewUrl).view, "overview");
});

test("import view coexists with source and period while preserving unrelated query and hash", () => {
  const input = "/console?tenant=alpha&module=import&view=history&source=jd_sku_daily&period=calendar_month&month=2026-08#upload";
  assert.deepEqual(parseShellLocation(input), {
    module: "import",
    view: "history",
    source: "jd_sku_daily",
    period: { kind: "calendar_month", month: "2026-08" },
  });

  const next = updateModuleViewLocation(input, "import", "chains");
  assert.equal(
    next,
    "/console?tenant=alpha&module=import&view=chains&source=jd_sku_daily&period=calendar_month&month=2026-08#upload",
  );
  assert.deepEqual(parseShellLocation(next), {
    module: "import",
    view: "chains",
    source: "jd_sku_daily",
    period: { kind: "calendar_month", month: "2026-08" },
  });
});

test("typed view updates reject a view owned by another module at runtime", () => {
  const transition = updateModuleViewLocation(
    "/console?tenant=alpha&module=sales&view=finance#sales",
    "workflow",
    "finance" as ModuleViewKey<"workflow">,
  );
  assert.equal(transition, "/console?tenant=alpha&module=workflow#sales");
  assert.equal(parseShellLocation(transition).view, "plan");
});

test("legacy salesTab links migrate once to canonical view without overriding an explicit view", () => {
  assert.equal(parseShellLocation("/?module=sales&salesTab=category").view, "category");
  assert.equal(
    normalizeShellLocation("/?module=sales&salesTab=category"),
    "/?module=sales&view=category",
  );
  assert.equal(
    normalizeShellLocation("/?module=sales&view=finance&salesTab=category"),
    "/?module=sales&view=finance",
  );
  assert.equal(
    normalizeShellLocation("/?module=sales&salesTab=category&salesTab=finance"),
    "/?module=sales",
  );
});

test("module changes remove stale shell view and legacy salesTab but retain sales category namespace", () => {
  assert.equal(
    serializeShellLocation(
      { module: "inventory", view: "plan", period: { kind: "current_month" } },
      "/?module=sales&view=category&salesTab=finance&salesCategory=商用净水&salesPage=2#detail",
    ),
    "/?salesCategory=%E5%95%86%E7%94%A8%E5%87%80%E6%B0%B4&salesPage=2&module=inventory&view=plan#detail",
  );
});

test("module-generic helpers retain their module-specific return type and runtime value", () => {
  function defaultFor<M extends ModuleKey>(module: M): ModuleViewKey<M> {
    return getDefaultModuleView(module);
  }
  const salesDefault: ModuleViewKey<"sales"> = defaultFor("sales");
  assert.equal(salesDefault, "overview");
});
