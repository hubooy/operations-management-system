import {
  type GlobalSearchGroupKey,
} from "./global-search";
import { handleSearchAllSystemDataTool } from "./global-search-tool";
import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";

export type SearchSystemDataForAiArguments = {
  q: string;
  domain?: GlobalSearchGroupKey;
  page?: number;
  limitPerDomain?: number;
  totalLimit?: number;
};

export type SearchSystemDataForAiContext = {
  execution: AiToolExecutionContext;
};

/** Stable handler for the central AI tool registry. */
export async function searchSystemDataForAi(
  args: SearchSystemDataForAiArguments,
  context: SearchSystemDataForAiContext,
): Promise<Record<string, unknown>> {
  const result = await handleSearchAllSystemDataTool({
    query: args.q,
    domain: args.domain,
    page: args.page,
    perGroupLimit: args.limitPerDomain,
    totalLimit: args.totalLimit,
  }, context.execution.principal, { signal: context.execution.signal });
  return {
    dataCutoff: result.dataCutoffDate,
    filtersApplied: result.filtersApplied,
    groups: result.groups,
    returned: result.returned,
    truncated: result.truncated,
    deadlineExceeded: result.deadlineExceeded,
    timedOutDomains: result.timedOutDomains,
    monetaryUnit: result.monetaryUnit,
    currency: result.currency,
    unavailableDomains: result.unavailableDomains,
    cutoffMeaning: result.cutoffMeaning,
  };
}
