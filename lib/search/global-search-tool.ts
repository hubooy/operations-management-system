import type { AppPrincipal } from "@/lib/auth/authorization";
import type { GlobalSearchExecutionOptions } from "./global-search";
import {
  type GlobalSearchGroupKey,
  normalizeGlobalSearchRequest,
  searchAllBusinessData,
} from "./global-search";

export type SearchAllSystemDataToolArguments = {
  query: string;
  domain?: GlobalSearchGroupKey;
  page?: number;
  perGroupLimit?: number;
  totalLimit?: number;
};

function asToolArguments(raw: unknown): SearchAllSystemDataToolArguments {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("工具参数必须是 JSON 对象。");
  const input = raw as Record<string, unknown>;
  const allowed = new Set(["query", "domain", "page", "perGroupLimit", "totalLimit"]);
  const unexpected = Object.keys(input).filter((key) => !allowed.has(key));
  if (unexpected.length > 0) throw new Error(`不支持的参数：${unexpected.join(", ")}`);
  if (typeof input.query !== "string") throw new Error("query 必须是字符串。");
  for (const key of ["page", "perGroupLimit", "totalLimit"] as const) {
    if (input[key] !== undefined && !Number.isInteger(input[key])) throw new Error(`${key} 必须是整数。`);
  }
  if (input.domain !== undefined && typeof input.domain !== "string") throw new Error("domain 必须是字符串。");
  return input as SearchAllSystemDataToolArguments;
}

/**
 * Central AI tool registries reuse the authorized Django readers here without
 * coupling the search implementation to a particular model/provider loop.
 */
export async function handleSearchAllSystemDataTool(
  rawArguments: unknown,
  principal: AppPrincipal,
  dependencies: GlobalSearchExecutionOptions = {},
) {
  const args = asToolArguments(rawArguments);
  const params = new URLSearchParams({ q: args.query });
  if (args.domain) params.set("group", args.domain);
  if (args.page !== undefined) params.set("page", String(args.page));
  if (args.perGroupLimit !== undefined) params.set("limit", String(args.perGroupLimit));
  if (args.totalLimit !== undefined) params.set("totalLimit", String(args.totalLimit));
  const request = normalizeGlobalSearchRequest(params);
  const response = await searchAllBusinessData(request, principal, dependencies);
  return {
    ...response,
    cutoffMeaning: "dataCutoffDate 是本页匹配结果中最新的业务日期或更新时间；各域仍以其导入批次为最终时效依据。",
    currency: "CNY",
    monetaryUnit: "cents",
  };
}
