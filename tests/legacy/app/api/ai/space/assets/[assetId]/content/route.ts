import { requireAppPrincipal } from "@/lib/auth/authorization";
import { getAiSpaceAssetDownload } from "@/tests/legacy/ai/space";
import { getD1Database } from "@/lib/database/d1";
import { aiRouteErrorResponse, requireAiId } from "@/app/api/ai/route-helpers";

export async function GET(_request: Request, context: { params: Promise<{ assetId: string }> }) {
  try {
    const principal = await requireAppPrincipal();
    const params = await context.params;
    const id = requireAiId(params.assetId, "assetId");
    const file = await getAiSpaceAssetDownload(id, principal, getD1Database());
    return new Response(file.body, {
      headers: {
        "cache-control": "private, no-store",
        "content-type": file.mimeType,
        "content-length": String(file.byteSize),
        "content-disposition": `inline; filename="${file.fileName}"`,
        "x-content-type-options": "nosniff",
        "content-security-policy": "default-src 'none'; sandbox",
        "x-ai-space-sha256": file.contentSha256,
        "x-ai-generated": "true",
        "x-ai-review-required": "true",
      },
    });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 空间图片失败");
  }
}
