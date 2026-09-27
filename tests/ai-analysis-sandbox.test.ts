import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  AiAnalysisSandboxError,
  describeAiAnalysisDatasets,
  runDeterministicAnalysisTransform,
} from "./legacy/ai/analysis-sandbox";

test("analysis sandbox advertises bounded deterministic execution, not arbitrary code", () => {
  const description = describeAiAnalysisDatasets();
  assert.equal(description.executionEnvironment, "deterministic_json_ast");
  assert.equal(description.arbitraryCode, false);
  assert.equal(description.networkAccess, "none_during_transform");
  assert.deepEqual(description.datasets.map((dataset) => dataset.id), [
    "sales_category",
    "netshop_product_daily",
    "netshop_promotion",
  ]);
  assert.ok(description.limits.maximumSteps <= 8);
});

test("analysis sandbox runs filter, derive, group and sort without eval", () => {
  const result = runDeterministicAnalysisTransform([
    { category: "A", sales: 1200, cost: 600 },
    { category: "A", sales: 800, cost: 500 },
    { category: "B", sales: 300, cost: 200 },
  ], [
    { op: "filter", field: "sales", operator: "gte", numberValue: 500 },
    { op: "derive", as: "profit", operator: "subtract", leftField: "sales", rightField: "cost" },
    { op: "group", groupBy: ["category"], metrics: [
      { aggregate: "sum", field: "sales", as: "salesTotal" },
      { aggregate: "sum", field: "profit", as: "profitTotal" },
      { aggregate: "count", as: "rows" },
    ] },
    { op: "sort", field: "salesTotal", direction: "desc" },
  ]);
  assert.deepEqual(result.rows, [{ category: "A", salesTotal: 2000, profitTotal: 900, rows: 2 }]);
  assert.deepEqual(result.stepsApplied.map((step) => step.op), ["filter", "derive", "group", "sort"]);
});

test("analysis sandbox converts division by zero to null and keeps result JSON-safe", () => {
  const result = runDeterministicAnalysisTransform([{ sales: 100, units: 0 }], [
    { op: "derive", as: "unitPrice", operator: "divide", leftField: "sales", rightField: "units" },
  ]);
  assert.deepEqual(result.rows, [{ sales: 100, units: 0, unitPrice: null }]);
  assert.doesNotThrow(() => JSON.stringify(result));
});

test("analysis sandbox rejects prototype fields and unknown operations", () => {
  assert.throws(
    () => runDeterministicAnalysisTransform([{ sales: 1 }], [{ op: "select", fields: ["__proto__"] }]),
    (error: unknown) => error instanceof AiAnalysisSandboxError && error.code === "invalid_analysis_plan",
  );
  assert.throws(
    () => runDeterministicAnalysisTransform([{ sales: 1 }], [{ op: "javascript", source: "return process.env" }]),
    (error: unknown) => error instanceof AiAnalysisSandboxError && error.code === "invalid_analysis_plan",
  );
});

test("analysis sandbox capabilities are declared once in the central registry", async () => {
  const source = await readFile(new URL("../lib/ai/tool-registry.ts", import.meta.url), "utf8");
  assert.equal(source.match(/name: "describe_analysis_datasets"/g)?.length, 1);
  assert.equal(source.match(/name: "run_analysis_plan"/g)?.length, 1);
  assert.match(source, /name: "run_analysis_plan"[\s\S]*?risk: "read_only"[\s\S]*?scopePolicy: "principal_scope"/);
});

test("analysis sandbox mutation route requires browser same-origin proof", async () => {
  const route = await readFile(new URL("../app/api/ai/sandbox/route.ts", import.meta.url), "utf8");
  assert.match(route, /export const POST = forwardAiRequest/);
  const gate = await readFile(new URL("../lib/ai/django-route.ts", import.meta.url), "utf8");
  assert.match(gate, /requireAiSameOriginWrite\(request\)/);
  assert.match(gate, /requireAppPrincipal\(read \|\| datasetQuery \? undefined : \["admin", "operator", "analyst"\]\)/);
});
