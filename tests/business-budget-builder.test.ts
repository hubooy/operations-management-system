import test from "node:test";
import assert from "node:assert/strict";
import { canonical, eligible, makePlan, validateBinding, validateTargetPage, type BudgetRun, type EvidenceBinding, type SelectedTarget, type TargetPage } from "../lib/ai/business-budget-builder";
const digest = "a".repeat(64);
const run: BudgetRun = { id: "evidence-one", version: 4, status: "sealed", plan: { schemaVersion: "business-evidence-v2", sourceCount: 1, catalogDigest: digest }, sources: { promotion: { complete: true } } };
const binding: EvidenceBinding = { evidenceRunId: run.id, evidenceVersion: run.version, evidencePlanDigest: digest, catalogDigest: digest, sealedDigest: digest };
const fields = { totalBudgetCents: "100.01", reserveCents: "10", horizonDays: "30", observationDays: "7", reviewAfterSpendBps: "20.25", minimumClicks: "50", minimumOrderLines: "3" };
const scenarios = [{ name: "明确假设", cpcFactorBps: "100", orderRateFactorBps: "110", orderValueFactorBps: "100", contributionMarginBps: "" }];
const target: SelectedTarget = { source: { key: "promotion", ordinal: 1, domain: "netshop", query: { platform: "京东", shop: "精确店铺", dataset: "promotion", window: "current", startDate: "2026-08-01", endDate: "2026-08-31" } }, dimension: "sku", row: { id: digest, rowIndex: 0, entity: { sku: "sku-1" }, dimensionMissing: false, metrics: {} }, fields: { weight: "1", minBudgetCents: "0", maxBudgetCents: "100.01", ownerRole: "运营", minimumRoasBps: "2.1234" } };
function page(): TargetPage { return { schemaVersion: "business-budget-targets-v1", evidenceBinding: structuredClone(binding), sourceKey: target.source.key, dimension: target.dimension, sourceMetadata: { coverage: { status: "dates_missing" } }, rows: [structuredClone(target.row)], pagination: { offset: 0, limit: 20, total: 2, hasMore: true, nextOffset: 1 } }; }
test("explicit values retain exact cents/basis points and nullable margin without mutating input", () => {
  const plan = makePlan(fields, [target], scenarios);
  assert.equal(plan.totalBudgetCents, 10001); assert.equal(plan.reviewAfterSpendBps, 2025); assert.equal(plan.targets[0].minimumRoasBps, 21234); assert.equal(plan.scenarios[0].contributionMarginBps, null);
  plan.targets[0].ownerRole = "changed"; assert.equal(target.fields.ownerRole, "运营");
});
test("no initial business number or scenario factor is inferred", () => {
  for (const key of Object.keys(fields)) assert.throws(() => makePlan({ ...fields, [key]: "" }, [target], scenarios));
  for (const key of ["cpcFactorBps", "orderRateFactorBps", "orderValueFactorBps"]) assert.throws(() => makePlan(fields, [target], [{ ...scenarios[0], [key]: "" }]));
  for (const key of Object.keys(target.fields)) assert.throws(() => makePlan(fields, [{ ...target, fields: { ...target.fields, [key]: "" } }], scenarios));
});
test("reject excess precision, exponent, negative and unsafe input", () => {
  for (const raw of ["1.001", "1e3", "-1", "9007199254740993", "NaN", "Infinity"]) assert.throws(() => makePlan({ ...fields, totalBudgetCents: raw }, [target], scenarios));
});
test("preserve server identity, reject missing/duplicate/mixed targets", () => {
  assert.equal(makePlan(fields, [target], scenarios).targets[0].rowId, target.row.id);
  assert.throws(() => makePlan(fields, [target, target], scenarios));
  assert.throws(() => makePlan(fields, [{ ...target, row: { ...target.row, dimensionMissing: true } }], scenarios));
  assert.throws(() => makePlan(fields, [target, { ...target, dimension: "shop", row: { ...target.row, id: "b".repeat(64) } }], scenarios));
  assert.throws(() => makePlan(fields, [{ ...target, row: { ...target.row, rowIndex: -1 } }], scenarios));
});
test("explicit shop and dates are not normalized or guessed", () => {
  const next = structuredClone(target); next.source.key = "other"; next.source.query.shop += "另一家"; next.row.id = "b".repeat(64);
  assert.equal(makePlan(fields, [target, next], scenarios).targets.length, 2);
  next.source.query.startDate = "2026-08-02"; assert.throws(() => makePlan(fields, [target, next], scenarios));
});
test("minimum totals, observation, scenario counts and names remain bounded", () => {
  assert.throws(() => makePlan(fields, [{ ...target, fields: { ...target.fields, minBudgetCents: "100" } }], scenarios));
  assert.throws(() => makePlan({ ...fields, observationDays: "31" }, [target], scenarios));
  assert.throws(() => makePlan(fields, [target], [])); assert.throws(() => makePlan(fields, [target], [...scenarios, ...scenarios]));
  assert.throws(() => makePlan(fields, [target], Array.from({ length: 6 }, (_, i) => ({ ...scenarios[0], name: "s"+i }))));
  assert.throws(() => makePlan(fields, Array.from({ length: 101 }, () => target), scenarios));
});
test("only current promotion is eligible; other sources are explained", () => {
  assert.equal(eligible(target.source), "");
  assert.ok(eligible({ ...target.source, domain: "sales" }));
  assert.ok(eligible({ ...target.source, query: { ...target.source.query, window: "previous" } }));
});
test("every binding field is pinned including first response run/version/catalog", () => {
  validateBinding(binding, run);
  for (const key of Object.keys(binding)) {
    const other = { ...binding, [key]: key === "evidenceVersion" ? 5 : "b".repeat(64) } as EvidenceBinding;
    assert.throws(() => validateBinding(other, run, binding));
  }
  assert.throws(() => validateBinding({ ...binding, extra: 1 } as EvidenceBinding, run));
});
test("variable length complete prefix uses actual nextOffset", () => {
  validateTargetPage(page(), run, "promotion", "sku", 0, binding);
  const wrong = page(); wrong.pagination.nextOffset = 20; assert.throws(() => validateTargetPage(wrong, run, "promotion", "sku", 0));
});
test("empty table is visible and valid, empty unfinished page fails", () => {
  const empty = page(); empty.rows = []; empty.pagination = { offset: 0, limit: 20, total: 0, hasMore: false, nextOffset: null };
  validateTargetPage(empty, run, "promotion", "sku", 0);
  empty.pagination.total = 1; assert.throws(() => validateTargetPage(empty, run, "promotion", "sku", 0));
});
test("late page wrong source/dimension/index and altered seal fail closed", () => {
  assert.throws(() => validateTargetPage(page(), run, "other", "sku", 0));
  assert.throws(() => validateTargetPage(page(), run, "promotion", "shop", 0));
  const bad = page(); bad.rows[0].rowIndex = 20; assert.throws(() => validateTargetPage(bad, run, "promotion", "sku", 0));
  const other = page(); other.evidenceBinding.sealedDigest = "b".repeat(64); assert.throws(() => validateTargetPage(other, run, "promotion", "sku", 0, binding));
});
test("canonical object order matches explicit Unicode JSON rather than truncation", () => {
  assert.equal(canonical({ z: "运营", a: [1, null] }), '{"a":[1,null],"z":"运营"}');
});
test("100 targets and 5 distinct scenarios remain complete", () => {
  const targets = Array.from({ length: 100 }, (_, i) => ({ ...structuredClone(target), row: { ...target.row, rowIndex: i, id: i.toString(16).padStart(64, "0") } }));
  const plan = makePlan(fields, targets, Array.from({ length: 5 }, (_, i) => ({ ...scenarios[0], name: "情景"+i })));
  assert.equal(plan.targets.length, 100); assert.equal(plan.scenarios.length, 5); assert.equal(plan.targets[99].rowIndex, 99);
});
test("explicit coefficient and money limits are checked without clamping", () => {
  for (const value of ["9.99", "300.01"]) assert.throws(() => makePlan(fields, [target], [{ ...scenarios[0], cpcFactorBps: value }]));
  assert.equal(makePlan(fields, [target], [{ ...scenarios[0], cpcFactorBps: "10", contributionMarginBps: "0" }]).scenarios[0].contributionMarginBps, 0);
  assert.throws(() => makePlan(fields, [target], [{ ...scenarios[0], contributionMarginBps: "100.01" }]));
  assert.throws(() => makePlan({ ...fields, reserveCents: "100.02" }, [target], scenarios));
});
