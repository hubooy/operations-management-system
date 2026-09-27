import assert from "node:assert/strict";
import test from "node:test";
import { allowPromotionReportDetailBytes, projectPromotionReport, PROMOTION_PROFILE } from "../lib/ai/business-promotion-view";

const roles = ["commerce", "promotion", "market_b2b", "independent_review", "report"];
function detail(content = false) {
  const nodes = [...roles.map((key, i) => ({ key, type: "agent", status: content ? "completed" : i === 0 ? "running" : "pending" })),
    { key: "human_review", type: "human_review", status: content ? "waiting_review" : "pending" }];
  return { item: { id: "report-a" }, snapshot: { schemaVersion: "business-report-v1", executionProfile: PROMOTION_PROFILE },
    workflow: { status: content ? "waiting_review" : "running", nodes },
    ...(content ? { screening: { schemaVersion: "business-promotion-completed-screening-v1", coverage: [{ kind: "source" }],
      readProofs: Object.fromEntries(roles.map(role => [role, { role, jobId: role }])),
      limitations: ["归因成交不是ERP净销售"], candidateDisclosure: { fullCandidatesIncluded: false, crossPartitionAmountsAdditive: false } },
      professionalAnalyses: Object.fromEntries(roles.slice(0, 3).map(role => [role, { role, summary: `${role}合成摘要` }])) } : {}) };
}

test("before content, workflow nodes are visible but scan is explicitly unverified", () => {
  const value = projectPromotionReport(detail());
  assert.equal(value.scan, "unverified"); assert.equal(value.coverageCount, null);
  assert.equal(value.nodes.length, 6); assert.equal(value.nodes[0].status, "运行中");
  assert.deepEqual(value.professionals, []); assert.equal(value.humanReview, "等待前序");
});

test("validated completed content shows bounded coverage and three professional summaries", () => {
  const value = projectPromotionReport(detail(true));
  assert.equal(value.scan, "verified"); assert.equal(value.coverageCount, 1);
  assert.equal(value.humanReview, "待人工复核");
  assert.deepEqual(value.limitations, ["归因成交不是ERP净销售"]);
  assert.deepEqual(value.professionals.map(role => role.role), roles.slice(0, 3));
});

test("wrong profile, missing roles, bad proof or disclosure never looks complete", () => {
  const wrong = detail(true); wrong.snapshot.executionProfile = "business-agent-screening-reference-v1";
  assert.throws(() => projectPromotionReport(wrong));
  const missing = detail(true); missing.workflow.nodes.pop(); assert.throws(() => projectPromotionReport(missing));
  const badProof = detail(true); badProof.screening!.readProofs.promotion.role = "commerce";
  assert.throws(() => projectPromotionReport(badProof));
  const disclosure = detail(true); disclosure.screening!.candidateDisclosure.fullCandidatesIncluded = true;
  assert.throws(() => projectPromotionReport(disclosure));
});

test("larger complete detail allowance is exact-profile and success bound", () => {
  const value = detail(true), bytes = 3 * 1024 * 1024;
  assert.equal(allowPromotionReportDetailBytes(value, bytes, true, 5 * 1024 * 1024), true);
  assert.equal(allowPromotionReportDetailBytes(value, bytes, false, 5 * 1024 * 1024), false);
  assert.equal(allowPromotionReportDetailBytes(value, 6 * 1024 * 1024, true, 5 * 1024 * 1024), false);
  const wrong = detail(true); wrong.snapshot.executionProfile = "unknown";
  assert.equal(allowPromotionReportDetailBytes(wrong, bytes, true, 5 * 1024 * 1024), false);
});
