import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
  type AppPrincipal,
} from "@/lib/auth/authorization";
import {
  MARKET_COMMANDS_PATH,
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";
import { readBoundedJsonObject } from "@/lib/http/bounded-json";
import {
  listAnnotationModels,
  listPromptTextModels,
  runPromptTextCompletion,
} from "@/lib/market/annotation-model";
import { runClaimedDjangoMarketVisionTask } from "@/lib/market/django-annotation-runner";

type JsonRecord = Record<string, unknown>;
const MARKET_ANNOTATION_BODY_BYTES_MAX = 256 * 1024;
const ADMIN_ACTIONS = new Set([
  "commit",
  "commit_selected",
  "rebuild_stale_selected",
  "activate_prompt",
  "rollback_prompt",
  "delete_prompt",
  "delete_job",
  "create_agent",
  "revoke_agent",
  "mark_gold",
]);

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function integerParam(params: URLSearchParams, key: string, fallback: number) {
  const value = params.get(key);
  if (value === null || value === "") return fallback;
  if (!/^\d+$/.test(value)) {
    throw new PublicApiError(400, "invalid_request", `${key} 必须是整数`);
  }
  return Number(value);
}

function annotationParams(params: URLSearchParams): JsonRecord {
  return {
    jobId: params.get("jobId")?.trim() ?? "",
    aggregateJobs: params.get("aggregateJobs") === "1",
    itemCategory: params.get("itemCategory")?.trim() ?? "",
    itemCategories: params.getAll("itemCategory"),
    q: params.get("q")?.trim() ?? "",
    page: integerParam(params, "page", 1),
    pageSize: integerParam(params, "pageSize", 30),
    itemPage: integerParam(params, "itemPage", 1),
    itemPageSize: integerParam(params, "itemPageSize", 20),
    itemSegments: params.getAll("itemSegment"),
    storageStatuses: params.getAll("storageStatus")
      .filter((value) => value === "pending" || value === "committed"),
    recognitionSources: params.getAll("recognitionSource")
      .filter((value) => value === "ai" || value === "non_ai"),
    includeCatalog: params.get("includeCatalog") === "1",
  };
}

async function marketQuery<T extends JsonRecord>(
  principal: AppPrincipal,
  view: string,
  params: JsonRecord,
  signal?: AbortSignal,
) {
  return requestDjangoMarketService<T>(
    principal,
    {
      path: MARKET_QUERIES_PATH,
      service: "reader",
      payload: { operation: "annotations", view, params },
    },
    { signal },
  );
}

async function marketCommand<T extends JsonRecord>(
  principal: AppPrincipal,
  command: JsonRecord,
  signal?: AbortSignal,
) {
  return requestDjangoMarketService<{ ok: boolean; result: T }>(
    principal,
    {
      path: MARKET_COMMANDS_PATH,
      service: "writer",
      payload: {
        contractVersion: "market-command-v1",
        domain: "annotations",
        command,
      },
    },
    { signal },
  );
}

async function generatedPromptCommand(
  principal: AppPrincipal,
  command: JsonRecord,
  mode: "generate" | "evolve",
  signal?: AbortSignal,
) {
  const category = text(command.category);
  const segments = Array.isArray(command.segments)
    ? command.segments.filter((item): item is string => typeof item === "string")
    : [];
  if (!category || segments.length < 1) {
    throw new PublicApiError(400, "invalid_request", "生成 Prompt 缺少类目或细分品类枚举");
  }
  let parentBody = "";
  const parentId = text(command.parentId);
  if (mode === "evolve" && parentId) {
    const workspace = await marketQuery<JsonRecord>(
      principal,
      "workspace_fast",
      { ...annotationParams(new URLSearchParams()), includeCatalog: false },
      signal,
    );
    const prompts = Array.isArray(workspace.data.prompts) ? workspace.data.prompts : [];
    const parent = prompts.find(
      (item) => item && typeof item === "object" && !Array.isArray(item)
        && text((item as JsonRecord).id) === parentId,
    ) as JsonRecord | undefined;
    parentBody = text(parent?.promptBody);
  }
  const instruction = mode === "evolve"
    ? `你是视觉分类 Prompt 工程师。请仅根据通用品类规则改进下面的 Prompt，保持可审计、可复用，只输出完整新 Prompt 正文，不要代码围栏。不得请求、推断或复述冻结 holdout 的金标、预测或错误信息。\n三级类目：${category}\n固定枚举：${segments.join("、")}\n旧 Prompt：\n${parentBody}`
    : `你是电商视觉分类 Prompt 工程师。为三级类目“${category}”编写完整 Prompt。固定枚举：${segments.join("、")}。要求结合商品名与京东大图判断、提取主图明确展示的人民币价格、输出严格 JSON，只输出 Prompt 正文。`;
  const promptBody = await runPromptTextCompletion(
    text(command.textModelId),
    instruction,
    principal,
  );
  const saved = await marketCommand<JsonRecord>(
    principal,
    {
      action: "record_generated_prompt",
      category,
      segments,
      promptBody,
      parentId,
      source: mode === "evolve" ? "evolved" : "ai_generated",
      changeNote: text(command.changeNote)
        || (mode === "evolve" ? "AI 生成候选版本" : "AI 生成初始版本"),
    },
    signal,
  );
  return mode === "evolve"
    ? { ...saved, data: { ok: true, result: { prompt: saved.data.result, validation: null } } }
    : saved;
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    requireUnrestrictedDataScope(principal, "市场 SKU AI 标注");
    const params = new URL(request.url).searchParams;
    const view = params.get("view") ?? "workspace";
    if (!["workspace", "workspace_fast", "progress", "candidate_counts", "review", "catalog"].includes(view)) {
      throw new PublicApiError(400, "invalid_request", "不支持的市场标注视图");
    }
    const result = await marketQuery<JsonRecord>(
      principal,
      view,
      annotationParams(params),
      request.signal,
    );
    let payload = result.data;
    if (view === "workspace" || view === "workspace_fast") {
      const [models, textModels] = await Promise.all([
        listAnnotationModels(principal),
        listPromptTextModels(principal),
      ]);
      payload = { ...payload, models, textModels, principal };
    }
    return Response.json(payload, {
      headers: {
        "cache-control": "no-store",
        "x-market-data-revision": result.revision,
      },
    });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取 SKU AI 标注工作台失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}

export async function POST(request: Request) {
  try {
    const preliminary = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(preliminary, "市场 SKU AI 标注", "修改");
    const command = await readBoundedJsonObject(request, MARKET_ANNOTATION_BODY_BYTES_MAX);
    const action = text(command.action);
    const principal = ADMIN_ACTIONS.has(action)
      ? await requireAppPrincipal(["admin"])
      : preliminary;
    requireUnrestrictedDataScope(principal, "市场 SKU AI 标注", "修改");
    const result = action === "run_next" || action === "run_batch"
      ? await runClaimedDjangoMarketVisionTask({
          principal,
          jobId: text(command.jobId),
          signal: request.signal,
        })
      : action === "generate_prompt"
        ? await generatedPromptCommand(principal, command, "generate", request.signal)
        : action === "evolve_prompt"
          ? await generatedPromptCommand(principal, command, "evolve", request.signal)
          : await marketCommand<JsonRecord>(principal, command, request.signal);
    return Response.json(result.data, {
      status: result.status,
      headers: {
        "cache-control": "no-store",
        "x-market-data-revision": result.revision,
        ...(result.replayed ? { "x-teruisi-write-replay": "1" } : {}),
      },
    });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "SKU AI 标注操作失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}
