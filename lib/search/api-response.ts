import { GlobalSearchRequestError } from "./global-search";

const noStoreHeaders = { "cache-control": "no-store" } as const;

export function globalSearchErrorResponse(error: unknown): Response {
  if (error instanceof GlobalSearchRequestError) {
    return Response.json(
      { error: error.message, code: "invalid_request" },
      { status: error.status, headers: noStoreHeaders },
    );
  }
  return Response.json(
    { error: "搜索系统数据失败", code: "internal_error" },
    { status: 500, headers: noStoreHeaders },
  );
}
