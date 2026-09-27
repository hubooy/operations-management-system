import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import {
  deleteAiSpaceModelProfile,
  listAiSpaceModelProfiles,
  upsertAiSpaceModelProfile,
} from "@/tests/legacy/ai/space";
import { getD1Database } from "@/lib/database/d1";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  readAiJsonObject,
  requireAiId,
  requireAiSameOriginWrite,
} from "@/app/api/ai/route-helpers";

async function requireManager(operation: string) {
  const principal = await requireAppPrincipal(["admin"]);
  requireUnrestrictedDataScope(principal, "AI 空间图片生成模型", operation);
  return principal;
}

export async function GET() {
  try {
    await requireManager("读取");
    return aiJsonResponse({ items: await listAiSpaceModelProfiles({}, getD1Database()) });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取图片生成模型失败");
  }
}

export async function POST(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireManager("修改");
    const body = await readAiJsonObject(request);
    const item = await upsertAiSpaceModelProfile({
      id: body.id,
      name: body.name,
      modelName: body.modelName,
      baseUrl: body.baseUrl,
      apiKey: body.apiKey,
      status: body.status,
      timeoutMs: body.timeoutMs,
      expectedVersion: body.expectedVersion,
    }, principal, getD1Database());
    return aiJsonResponse({ item });
  } catch (error) {
    return aiRouteErrorResponse(error, "保存图片生成模型失败");
  }
}

export async function DELETE(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireManager("删除");
    const values = new URL(request.url).searchParams.getAll("id");
    const versionValues = new URL(request.url).searchParams.getAll("expectedVersion");
    const id = requireAiId(values.length === 1 ? values[0] : undefined, "id");
    await deleteAiSpaceModelProfile(
      id,
      versionValues.length === 1 ? Number(versionValues[0]) : undefined,
      principal,
      getD1Database(),
    );
    return aiJsonResponse({ ok: true });
  } catch (error) {
    return aiRouteErrorResponse(error, "删除图片生成模型失败");
  }
}
