import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  canManageFinanceTargets,
  validateFinanceTargetDeletionReason,
} from "../app/module-view-shared";

const salesPath = new URL("../app/sales-module-view.tsx", import.meta.url);

test("finance target write controls are restricted to administrators", async () => {
  assert.equal(canManageFinanceTargets({ role: "admin" }), true);
  assert.equal(canManageFinanceTargets({ role: "viewer" }), false);
  assert.equal(canManageFinanceTargets({ role: "analyst" }), false);
  assert.equal(canManageFinanceTargets({ role: "operator" }), false);
  assert.equal(canManageFinanceTargets(null), false);

  const sales = await readFile(salesPath, "utf8");
  assert.match(sales, /function SalesView\(\{[^\n]+currentUser[^\n]+\}: \{[^\n]+currentUser: CurrentUser \| null;/);
  assert.match(sales, /const canManageTargets = canManageFinanceTargets\(currentUser\);/);
  assert.match(sales, /<FinanceTargetSettingsView canManageTargets=\{canManageTargets\}/);
  assert.match(sales, /\{canManageTargets \? <section className="panel finance-target-form-panel">/);
  assert.match(sales, /\{canManageTargets \? <div className="finance-target-row-actions">/);
  assert.match(sales, /仅管理员可新增、编辑或删除经营目标；你仍可查看全部目标并使用分页。/);
  assert.match(sales, /if \(!canManageTargets \|\| saving \|\| importing \|\| deletingTargetId !== null\) return;/);
});

test("finance target deletion requires confirmation and a bounded reason", async () => {
  assert.deepEqual(validateFinanceTargetDeletionReason(null), { status: "cancelled" });
  assert.deepEqual(validateFinanceTargetDeletionReason(""), { status: "invalid" });
  assert.deepEqual(validateFinanceTargetDeletionReason("   "), { status: "invalid" });
  assert.deepEqual(validateFinanceTargetDeletionReason("x".repeat(201)), { status: "invalid" });
  assert.deepEqual(validateFinanceTargetDeletionReason("  月度目标录入有误  "), {
    status: "accepted",
    reason: "月度目标录入有误",
  });

  const sales = await readFile(salesPath, "utf8");
  const removeTarget = sales.slice(sales.indexOf("const removeTarget = async"), sales.indexOf("return <div className=\"finance-target-page\">"));
  assert.ok(removeTarget.includes("window.confirm("));
  assert.ok(removeTarget.indexOf("window.confirm(") < removeTarget.indexOf("window.prompt("));
  assert.match(removeTarget, /const reasonResult = validateFinanceTargetDeletionReason\(providedReason\);/);
  assert.match(removeTarget, /if \(reasonResult\.status === "cancelled"\) return;/);
  assert.match(removeTarget, /if \(reasonResult\.status === "invalid"\) \{[\s\S]*?return;/);
  assert.match(removeTarget, /const query = new URLSearchParams\(\{[\s\S]*?id: item\.id,[\s\S]*?expectedVersion: String\(item\.version\),[\s\S]*?reason: reasonResult\.reason,/);
  assert.match(removeTarget, /fetch\(`\/api\/finance\/targets\?\$\{query\.toString\(\)\}`, \{ method: "DELETE" \}\)/);
  assert.match(removeTarget, /response\.status === 409[\s\S]*?await loadTargets\(\)/);
  assert.doesNotMatch(removeTarget, /finance\/targets\?id=\$\{encodeURIComponent/);
});

test("the page has no customer-service delete control that could omit an audit reason", async () => {
  const page = await readFile(new URL("../app/customer-service-view.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(page, /fetch\([^\n]*\/api\/customer-service\/conversations[^\n]*\{\s*method:\s*"DELETE"/);
});

test("finance target form preserves the platform+shop composite identity and discloses truncated options", async () => {
  const [sales, shared, route] = await Promise.all([
    readFile(salesPath, "utf8"),
    readFile(new URL("../app/module-view-shared.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/finance/targets/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(shared, /export type FinanceTarget = \{[\s\S]*?platform: string;[\s\S]*?shopName: string;/);
  assert.match(sales, /shopKey: item\.platform && item\.shopName \? JSON\.stringify\(\[item\.platform, item\.shopName\]\)/);
  assert.match(sales, /platform: form\.platform,[\s\S]*?shopName: form\.shopName/);
  assert.match(sales, /label: `\$\{item\.platform\} · \$\{item\.name\}`/);
  assert.match(sales, /options\.pagination\?\.shops\.truncated[\s\S]*?店铺选项已设上限/);
  assert.match(route, /const platform = String\(body\.platform/);
  assert.match(route, /月度或年度目标必须选择有效平台/);
});

test("finance target list is decoupled from slow admin-only options while full stays compatible", async () => {
  const [sales, route] = await Promise.all([
    readFile(salesPath, "utf8"),
    readFile(new URL("../app/api/finance/targets/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(route, /type FinanceTargetReadView = "full" \| "items" \| "options" \| "annual"/);
  assert.match(route, /if \(values\.length === 0\) return "full"/);
  assert.match(route, /view 必须且只能是 full、items、options 或 annual/);
  assert.match(route, /if \(view === "items"\) \{[\s\S]*?createDjangoFinanceService[\s\S]*?return requireDjangoRecord/);
  assert.match(route, /financeOptions/);
  assert.match(route, /const \[financeResult, salesCategories\] = await Promise\.all\(/, "default full response must remain compatible");

  assert.match(sales, /finance\/targets\?view=items&page=\$\{targetPage\}&pageSize=100/);
  assert.match(sales, /if \(!canManageTargets\) return;[\s\S]*?finance\/targets\?view=options/);
  assert.match(sales, /if \(loading \|\| optionsLoadedRef\.current\) return;/, "option scan must start only after the target list settles");
  assert.match(sales, /optionsRequestGenerationRef[\s\S]*?generation !== optionsRequestGenerationRef\.current/);
  assert.match(sales, /optionsRequestControllerRef\.current\?\.abort\(\)/);
  assert.match(sales, /管理选项加载失败[\s\S]*?重试加载/);
});

test("finance initial missing month fallback is explicit and manual selection disables it", async () => {
  const [sales, route, shared] = await Promise.all([
    readFile(salesPath, "utf8"),
    readFile(new URL("../app/api/finance/analysis/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/module-view-shared.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(route, /getAll\("initialMonthFallback"\)/);
  assert.match(route, /fallbackValues\[0\] !== "latest_completed"/);
  assert.match(route, /fallbackToLatestCompletedMonth/);
  assert.match(sales, /allowInitialMonthFallback && selectedMonths !== null && selectedMonths\.length > 0/);
  assert.match(sales, /query\.set\("initialMonthFallback", "latest_completed"\)/);
  assert.match(sales, /const selectMonthsStrictly[\s\S]*?setAllowInitialMonthFallback\(false\);[\s\S]*?setSelectedMonths\(months\)/);
  assert.match(sales, /已显示最新可用财报[\s\S]*?手动选择月份后将严格按选择读取/);
  assert.match(shared, /fallbackApplied\?: boolean/);
});
