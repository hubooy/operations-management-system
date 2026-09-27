import assert from "node:assert/strict";
import test from "node:test";
import { parseReportSkill } from "../lib/ai/report-skill-file";
import { isPublicAiPath } from "../lib/django/ai-service";

const skill = { id: "shop-analysis", name: "店铺诊断", enabled: true, description: "分析店铺变化", keywords: ["店铺"], domains: ["netshop"] };
const encode = (value: unknown, body = "先查询覆盖，再按授权数据分析。") => "---\n" + JSON.stringify(value) + "\n---\n" + body;
test("report skill import accepts portable text and rejects scripts, malformed types and path traversal", () => {
  assert.equal(parseReportSkill(encode(skill)).name, "店铺诊断");
  for (const value of [{ ...skill, scripts: ["run.py"] }, { ...skill, id: "../escape" }, { ...skill, enabled: "true" }, { ...skill, keywords: "店铺" }, { ...skill, domains: ["credentials"] }, { ...skill, name: {} }]) assert.throws(() => parseReportSkill(encode(value)));
  assert.throws(() => parseReportSkill(encode(skill, "a".repeat(2001))));
  assert.throws(() => parseReportSkill("arbitrary text"));
});
test("report routes allow only explicit resources, downloads and sends", () => {
  for (const path of ["/api/ai/report-library", "/api/ai/reports", "/api/ai/reports/report-1", "/api/ai/reports/report-1/content", "/api/ai/reports/report-1/send"]) assert.equal(isPublicAiPath(path), true, path);
  for (const path of ["/api/ai/reports/../secret", "/api/ai/reports/x/run", "/api/ai/reports/x/delete", "/api/ai/report-library/execute"]) assert.equal(isPublicAiPath(path), false, path);
});
