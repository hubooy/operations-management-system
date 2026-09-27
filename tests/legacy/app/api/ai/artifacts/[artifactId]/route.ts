import { requireAppPrincipal, type AppPrincipal } from "@/lib/auth/authorization";
import { getAiArtifactDownload, isAiArtifactId, recordAiArtifactDelivery } from "@/tests/legacy/ai/artifacts";
import { getD1Database } from "@/lib/database/d1";
import { aiRouteErrorResponse } from "@/app/api/ai/route-helpers";

export async function GET(
  _request: Request,
  context: { params: Promise<{ artifactId: string }> },
) {
  let principal: AppPrincipal | undefined;
  let artifactId = "[invalid_artifact_id]";
  const requestId = `artifact-download-${crypto.randomUUID()}`;
  try {
    principal = await requireAppPrincipal();
    const params = await context.params;
    artifactId = isAiArtifactId(params.artifactId)
      ? params.artifactId
      : "[invalid_artifact_id]";
    if (artifactId === "[invalid_artifact_id]") {
      return Response.json({ error: "产物不存在" }, { status: 404, headers: { "cache-control": "private, no-store" } });
    }
    const result = await getAiArtifactDownload(artifactId, principal, getD1Database());
    if (!result) {
      await recordAiArtifactDelivery({
        artifactId,
        requestId,
        principal,
        status: "failed",
        errorCode: "artifact_not_found_or_denied",
        database: getD1Database(),
      });
      return Response.json({ error: "产物不存在或无权访问" }, { status: 404, headers: { "cache-control": "private, no-store" } });
    }
    await recordAiArtifactDelivery({
      artifactId,
      requestId,
      principal,
      status: "succeeded",
      byteSize: result.bytes.byteLength,
      contentDigest: result.contentDigest,
      database: getD1Database(),
    });
    const responseBody = new Uint8Array(result.bytes.byteLength);
    responseBody.set(result.bytes);
    return new Response(responseBody.buffer, {
      headers: {
        "cache-control": "private, no-store",
        "content-type": result.artifact.mimeType,
        "content-disposition": `attachment; filename="${result.artifact.fileName}"`,
        "x-content-type-options": "nosniff",
      },
    });
  } catch (error) {
    if (principal && artifactId !== "[invalid_artifact_id]") {
      await recordAiArtifactDelivery({
        artifactId,
        requestId,
        principal,
        status: "failed",
        errorCode: "artifact_delivery_failed",
        database: getD1Database(),
      }).catch(() => undefined);
    }
    return aiRouteErrorResponse(error, "产物下载失败", { "cache-control": "private, no-store" });
  }
}
