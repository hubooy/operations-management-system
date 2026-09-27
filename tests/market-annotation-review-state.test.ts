import assert from "node:assert/strict";
import test from "node:test";
import { canSelectAnnotationReviewItem } from "../lib/market/annotation-review-state";

test("finished review items remain selectable independently of the recent-job window", () => {
  const item = { status: "review_pending", reviewJobReady: true, reviewSegments: ["台式"] };
  assert.equal(canSelectAnnotationReviewItem(item, "台式"), true);
  assert.equal(canSelectAnnotationReviewItem(item, "字典已删除"), false);
  assert.equal(canSelectAnnotationReviewItem({ ...item, status: "inferencing" }, "台式"), false);
  assert.equal(canSelectAnnotationReviewItem({ ...item, status: "committed" }, "台式"), false);
  assert.equal(canSelectAnnotationReviewItem({ ...item, reviewJobReady: false }, "台式"), false);
  assert.equal(canSelectAnnotationReviewItem({ ...item, snapshotValid: false }, "台式"), false);
  assert.equal(canSelectAnnotationReviewItem({ ...item, snapshotValid: true }, "台式"), true);
});
