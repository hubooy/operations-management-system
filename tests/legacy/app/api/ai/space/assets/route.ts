import { requireAppPrincipal } from "@/lib/auth/authorization";
import { listAiSpaceAssets } from "@/tests/legacy/ai/space";
import { PublicApiError } from "@/lib/http/api-error";
import { getD1Database } from "@/lib/database/d1";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  parseAiPositiveInteger,
} from "@/app/api/ai/route-helpers";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    const params = new URL(request.url).searchParams;
    const favorites = params.getAll("favorites");
    if (favorites.length > 1 || (favorites.length === 1 && favorites[0] !== "1")) {
      throw new PublicApiError(400, "invalid_request", "favorites 仅支持值 1。");
    }
    const page = parseAiPositiveInteger(params, "page", 1, 10_000);
    const pageSize = parseAiPositiveInteger(params, "pageSize", 24, 60);
    return aiJsonResponse(await listAiSpaceAssets({
      page,
      pageSize,
      favoritesOnly: favorites[0] === "1",
    }, principal, getD1Database()));
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 空间图片失败");
  }
}
