/** Read-only UI hints from the signed evidence detail. Creation revalidates. */
export type PromotionChoice = { sourceKey: string; platform: "京东"; shop: string; startDate: string; endDate: string;
  baselineChoices: { sourceKey: string; window: "previous" | "yearAgo" }[] };
export type PromotionSelection = { principalKey: string; runId: string; version: number; sourceKey: string; baselineKey: string };
type Detail = { id: string; version: number; status: string; workbenchAnalysisEnabled?: boolean;
  plan: { schemaVersion?: string; analysisRequest?: { question: string } };
  sources: Record<string, { complete: boolean }>;
  promotionSupported?: boolean; promotionChoices?: PromotionChoice[] };
const key = /^[A-Za-z0-9_-]{1,160}$/, iso = /^\d{4}-\d{2}-\d{2}$/;
function bad(): never { throw new Error("推广专项来源详情无效，请刷新当前封存任务；未启动分析。"); }

export function validatePromotionChoices(detail: Detail): PromotionChoice[] {
  if (detail.promotionSupported === undefined && detail.promotionChoices === undefined) return [];
  const choices = detail.promotionChoices;
  if (typeof detail.promotionSupported !== "boolean" || !Array.isArray(choices) || choices.length > 48
    || detail.promotionSupported !== (choices.length > 0)) bad();
  if (detail.promotionSupported && (detail.status !== "sealed" || detail.plan.schemaVersion !== "business-evidence-v2"
    || detail.workbenchAnalysisEnabled !== true || !detail.plan.analysisRequest?.question)) bad();
  const seen = new Set<string>();
  return choices.map(item => {
    if (!item || Object.keys(item).sort().join(",") !== "baselineChoices,endDate,platform,shop,sourceKey,startDate"
      || typeof item.sourceKey !== "string" || !key.test(item.sourceKey) || seen.has(item.sourceKey)
      || item.platform !== "京东" || typeof item.shop !== "string" || !item.shop || item.shop.length > 100
      || typeof item.startDate !== "string" || !iso.test(item.startDate)
      || typeof item.endDate !== "string" || !iso.test(item.endDate) || item.startDate > item.endDate
      || detail.sources[item.sourceKey]?.complete !== true || !Array.isArray(item.baselineChoices)
      || item.baselineChoices.length > 2) bad();
    seen.add(item.sourceKey);
    const baselineKeys = new Set<string>(), baselineWindows = new Set<string>();
    const baselines = item.baselineChoices.map(baseline => {
      if (!baseline || Object.keys(baseline).sort().join(",") !== "sourceKey,window"
        || typeof baseline.sourceKey !== "string" || !key.test(baseline.sourceKey)
        || baseline.sourceKey === item.sourceKey || detail.sources[baseline.sourceKey]?.complete !== true
        || !["previous", "yearAgo"].includes(baseline.window) || baselineKeys.has(baseline.sourceKey)
        || baselineWindows.has(baseline.window)) bad();
      baselineKeys.add(baseline.sourceKey); baselineWindows.add(baseline.window);
      return { sourceKey: baseline.sourceKey, window: baseline.window };
    });
    return { sourceKey: item.sourceKey, platform: "京东", shop: item.shop,
      startDate: item.startDate, endDate: item.endDate, baselineChoices: baselines };
  });
}

export function promotionSelectionPayload(detail: Detail, selection: PromotionSelection | null, principalKey: string) {
  const choices = validatePromotionChoices(detail);
  if (detail.promotionSupported !== true || !selection || selection.principalKey !== principalKey
    || selection.runId !== detail.id || selection.version !== detail.version) bad();
  const chosen = choices.find(choice => choice.sourceKey === selection.sourceKey);
  if (!chosen || (selection.baselineKey && !chosen.baselineChoices.some(choice => choice.sourceKey === selection.baselineKey))) bad();
  return { sourceKey: chosen.sourceKey, ...(selection.baselineKey ? { baselineKey: selection.baselineKey } : {}) };
}
