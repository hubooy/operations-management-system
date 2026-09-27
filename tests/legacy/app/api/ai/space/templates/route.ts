import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import {
  deleteAiSpaceTemplate,
  listAiSpaceTemplates,
  upsertAiSpaceTemplate,
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
  requireUnrestrictedDataScope(principal, "AI 空间模板", operation);
  return principal;
}

export async function GET() {
  try {
    await requireManager("读取");
    return aiJsonResponse({ items: await listAiSpaceTemplates({}, getD1Database()) });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 空间模板失败");
  }
}

export async function POST(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireManager("修改");
    const body = await readAiJsonObject(request);
    const item = await upsertAiSpaceTemplate({
      id: body.id,
      scene: body.scene,
      name: body.name,
      promptTemplate: body.promptTemplate,
      size: body.size,
      modelProfileId: body.modelProfileId,
      isEnabled: body.isEnabled,
      isDefault: body.isDefault,
      expectedVersion: body.expectedVersion,
    }, principal, getD1Database());
    return aiJsonResponse({ item });
  } catch (error) {
    return aiRouteErrorResponse(error, "保存 AI 空间模板失败");
  }
}

export async function DELETE(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireManager("删除");
    const values = new URL(request.url).searchParams.getAll("id");
    const versionValues = new URL(request.url).searchParams.getAll("expectedVersion");
    const id = requireAiId(values.length === 1 ? values[0] : undefined, "id");
    await deleteAiSpaceTemplate(
      id,
      versionValues.length === 1 ? Number(versionValues[0]) : undefined,
      principal,
      getD1Database(),
    );
    return aiJsonResponse({ ok: true });
  } catch (error) {
    return aiRouteErrorResponse(error, "删除 AI 空间模板失败");
  }
}
