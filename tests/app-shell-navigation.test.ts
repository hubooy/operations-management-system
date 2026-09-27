import assert from "node:assert/strict";
import test from "node:test";

import {
  importSourceKeys,
  isImportSourceKey,
  isModuleKey,
  moduleKeys,
  navGroups,
  navItems,
} from "../app/shell/navigation-catalog";
import {
  normalizeShellLocation,
  parseShellLocation,
  serializeShellLocation,
  type ShellLocationState,
} from "../app/shell/navigation-contract";

test("navigation catalog preserves the twelve modules and task-oriented group order", () => {
  assert.equal(moduleKeys.length, 12);
  assert.equal(navItems.length, 12);
  assert.deepEqual(
    navGroups.map((group) => ({ label: group.label, keys: [...group.keys] })),
    [
      {
        label: "协同执行",
        keys: ["workflow", "n8n_workflows"],
      },
      {
        label: "经营分析",
        keys: ["dashboard", "shop", "market", "customer_service", "sales"],
      },
      {
        label: "商品与供应链",
        keys: ["inventory", "product", "import"],
      },
      {
        label: "系统与智能",
        keys: ["settings", "ai"],
      },
    ],
  );
  assert.equal(navItems.find((item) => item.key === "product")?.label, "商品经营");
  assert.equal(navItems.find((item) => item.key === "n8n_workflows")?.label, "自动化中心");
  assert.deepEqual(new Set(navItems.map((item) => item.key)), new Set(moduleKeys));
  assert.equal(isModuleKey("inventory"), true);
  assert.equal(isModuleKey("unknown"), false);
});

test("import source catalog exposes every current legal value", () => {
  assert.deepEqual(importSourceKeys, [
    "sales",
    "inventory",
    "products",
    "sku_shipping_rates",
    "inventory_age",
    "combos",
    "finance",
    "jd_sku",
    "jd_sku_images",
    "jd_sku_daily",
    "jd_spu_daily",
    "tmall_product_master",
    "tmall_product_assets",
    "tmall_product_daily",
    "tmall_promotion",
    "customer_service",
  ]);
  assert.equal(isImportSourceKey("jd_sku_daily"), true);
  assert.equal(isImportSourceKey("market"), false);
});

test("dashboard is canonical at root and invalid or duplicate modules fail closed", () => {
  assert.deepEqual(parseShellLocation("/"), {
    module: "dashboard",
    view: "overview",
    period: { kind: "current_month" },
  });
  assert.equal(normalizeShellLocation("/?module=dashboard"), "/");
  assert.equal(normalizeShellLocation("/?module=not-real"), "/");
  assert.equal(normalizeShellLocation("/?module=sales&module=inventory"), "/");
});

test("module and import source parse, serialize, and normalize safely", () => {
  assert.deepEqual(parseShellLocation("/?module=import&source=jd_sku_daily"), {
    module: "import",
    view: "files",
    source: "jd_sku_daily",
    period: { kind: "current_month" },
  });
  assert.equal(normalizeShellLocation("/?module=sales&source=jd_sku_daily"), "/?module=sales");
  assert.equal(normalizeShellLocation("/?module=import&source=unknown"), "/?module=import");
  assert.equal(serializeShellLocation({ module: "inventory", period: { kind: "last7" } }), "/?module=inventory&period=last7");
});

test("period contract supports relative, calendar month, and custom ranges", () => {
  const cases: Array<[string, ShellLocationState["period"], string]> = [
    ["/?period=today", { kind: "today" }, "/?period=today"],
    ["/?period=yesterday", { kind: "yesterday" }, "/?period=yesterday"],
    ["/?period=last7", { kind: "last7" }, "/?period=last7"],
    ["/?period=last15", { kind: "last15" }, "/?period=last15"],
    ["/?period=current_month", { kind: "current_month" }, "/"],
    ["/?period=calendar_month&month=2026-08", { kind: "calendar_month", month: "2026-08" }, "/?period=calendar_month&month=2026-08"],
    ["/?period=custom&from=2026-08-01&to=2026-08-15", { kind: "custom", from: "2026-08-01", to: "2026-08-15" }, "/?period=custom&from=2026-08-01&to=2026-08-15"],
  ];
  for (const [input, period, canonical] of cases) {
    assert.deepEqual(parseShellLocation(input).period, period);
    assert.equal(normalizeShellLocation(input), canonical);
  }
});

test("malformed period details normalize to current month without stale fields", () => {
  for (const input of [
    "/?period=unknown&month=2026-08&from=2026-08-01&to=2026-08-02",
    "/?period=calendar_month&month=2026-13",
    "/?period=custom&from=2026-02-30&to=2026-03-01",
    "/?period=custom&from=2026-08-15&to=2026-08-01",
    "/?period=custom&from=2026-08-01&from=2026-08-02&to=2026-08-15",
  ]) {
    assert.deepEqual(parseShellLocation(input).period, { kind: "current_month" });
    assert.equal(normalizeShellLocation(input), "/");
  }
});

test("normalization preserves unknown query parameters, pathname, and hash", () => {
  assert.equal(
    normalizeShellLocation("/?campaign=summer&module=dashboard&period=current_month#summary"),
    "/?campaign=summer#summary",
  );
  assert.equal(
    serializeShellLocation(
      { module: "market", period: { kind: "calendar_month", month: "2026-07" } },
      "/?campaign=summer&module=sales&source=jd_sku#ranking",
    ),
    "/?campaign=summer&module=market&period=calendar_month&month=2026-07#ranking",
  );
  assert.equal(
    normalizeShellLocation("https://example.com/console?tenant=1&module=inventory#stock"),
    "/console?tenant=1&module=inventory#stock",
  );
});

test("canonical serialization is stable across parse and normalize", () => {
  const input = "/?tenant=alpha&module=import&source=tmall_promotion&period=custom&from=2026-08-01&to=2026-08-12#upload";
  const canonical = normalizeShellLocation(input);
  assert.equal(serializeShellLocation(parseShellLocation(canonical), canonical), canonical);
  assert.equal(normalizeShellLocation(canonical), canonical);
});
