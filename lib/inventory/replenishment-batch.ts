export type DraftPlanBatchItem = {
  id: string;
  plannedQuantity: number;
};

export type DraftPlanBatchResult = {
  id: string;
  ok: boolean;
  confirmed: boolean;
  error?: string;
};

export function retainFailedPlanSelections(
  preservedIds: readonly string[],
  results: readonly Pick<DraftPlanBatchResult, "id" | "ok">[],
): string[] {
  return [...new Set([
    ...preservedIds,
    ...results.filter((result) => !result.ok).map((result) => result.id),
  ])];
}

export async function confirmAndSyncDraftPlans<T extends DraftPlanBatchItem>(
  plans: readonly T[],
  confirmPlan: (plan: T) => Promise<void>,
  syncPlan: (plan: T) => Promise<void>,
  shouldStop: (message: string) => boolean,
): Promise<DraftPlanBatchResult[]> {
  const results: DraftPlanBatchResult[] = [];
  for (let index = 0; index < plans.length; index += 1) {
    const plan = plans[index]!;
    let confirmed = false;
    try {
      await confirmPlan(plan);
      confirmed = true;
      await syncPlan(plan);
      results.push({ id: plan.id, ok: true, confirmed: true });
    } catch (error) {
      const message = error instanceof Error ? error.message : "处理失败";
      results.push({ id: plan.id, ok: false, confirmed, error: message });
      if (shouldStop(message)) {
        for (const remaining of plans.slice(index + 1)) {
          results.push({
            id: remaining.id,
            ok: false,
            confirmed: false,
            error: `批量处理已停止：${message}`,
          });
        }
        break;
      }
    }
  }
  return results;
}
