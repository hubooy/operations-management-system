import assert from "node:assert/strict";
import test from "node:test";
import { promotionSelectionPayload, validatePromotionChoices } from "../lib/ai/business-promotion-choices";

const principal = "a".repeat(64);
function detail() {
  return { id: "evidence-a", version: 4, status: "sealed", workbenchAnalysisEnabled: true,
    plan: { schemaVersion: "business-evidence-v2", analysisRequest: { question: "词货分析" } },
    sources: { ads: { complete: true }, previous: { complete: true }, other: { complete: true } },
    promotionSupported: true,
    promotionChoices: [{ sourceKey: "ads", platform: "京东" as const, shop: "精确店铺", startDate: "2026-08-01", endDate: "2026-08-31",
      baselineChoices: [{ sourceKey: "previous", window: "previous" as const }] }] };
}
const selection = { principalKey: principal, runId: "evidence-a", version: 4, sourceKey: "ads", baselineKey: "previous" };

test("exact current and optional baseline survive signed-detail validation and are copied into creation payload", () => {
  const given = detail(), original = structuredClone(given);
  const choices = validatePromotionChoices(given);
  assert.deepEqual(choices, given.promotionChoices);
  choices[0].shop = "mutated";
  assert.deepEqual(given, original);
  assert.deepEqual(promotionSelectionPayload(given, selection, principal), { sourceKey: "ads", baselineKey: "previous" });
  assert.deepEqual(promotionSelectionPayload(given, { ...selection, baselineKey: "" }, principal), { sourceKey: "ads" });
});

test("absent rollout flag and disabled flag cannot turn an old detail into authority", () => {
  const old = detail(); delete (old as Partial<typeof old>).promotionSupported; delete (old as Partial<typeof old>).promotionChoices;
  assert.deepEqual(validatePromotionChoices(old), []);
  assert.throws(() => promotionSelectionPayload(old, selection, principal));
  const disabled = detail(); disabled.promotionSupported = false; disabled.promotionChoices = [];
  assert.deepEqual(validatePromotionChoices(disabled), []);
  assert.throws(() => promotionSelectionPayload(disabled, selection, principal));
});

test("actor, run, version, source and baseline changes fail closed", () => {
  const given = detail();
  for (const changed of [{ ...selection, principalKey: "b".repeat(64) }, { ...selection, runId: "other" },
    { ...selection, version: 5 }, { ...selection, sourceKey: "other" }, { ...selection, baselineKey: "other" }])
    assert.throws(() => promotionSelectionPayload(given, changed, principal));
});

test("unsealed, incomplete, duplicate and foreign source metadata are rejected", () => {
  const cases = [
    () => { const x = detail(); x.status = "collecting"; return x; },
    () => { const x = detail(); x.sources.ads.complete = false; return x; },
    () => { const x = detail(); x.sources.previous.complete = false; return x; },
    () => { const x = detail(); x.promotionChoices.push(structuredClone(x.promotionChoices[0])); return x; },
    () => { const x = detail(); x.promotionChoices[0].platform = "天猫" as "京东"; return x; },
    () => { const x = detail(); x.promotionChoices[0].baselineChoices[0].window = "current" as "previous"; return x; },
    () => { const x = detail(); Object.assign(x.promotionChoices[0], { extra: true }); return x; },
  ];
  for (const make of cases) assert.throws(() => validatePromotionChoices(make()));
});
