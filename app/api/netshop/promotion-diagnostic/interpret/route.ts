import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import jdStoreRegistry from "@/config/jd-store-accounts.json";
import { aiConsumer } from "@/lib/django/ai-service";
import { createDjangoNetshopService, NETSHOP_PROMOTION_DIAGNOSTIC_PATH } from "@/lib/django/netshop-service";
import { readBoundedJsonObject } from "@/lib/http/bounded-json";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { netshopOutletsForPrincipal, netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import { resolveNetshopQueryPeriod } from "@/lib/netshop/query-contract";
import { buildPromotionDiagnosticReport, type DiagnosticPeriod, type ReportTarget } from "@/lib/jd/promotion-diagnostic-report";
import { appendPromotionRelations } from "@/lib/jd/promotion-diagnostic-relations";
import { promotionSystemSourceReady } from "@/lib/jd/promotion-diagnostic-identity";
import { preparePromotionInterpretation, validatePromotionInterpretationReply } from "@/lib/jd/promotion-diagnostic-interpret";

const SHOP_NAME = "志高商用设备旗舰店";
const MAX_BODY_BYTES = 4 * 1024;

function previousPeriod(startDate: string, days: number) {
  const start = Date.parse(`${startDate}T00:00:00Z`);
  return {
    startDate: new Date(start - days * 86_400_000).toISOString().slice(0, 10),
    endDate: new Date(start - 86_400_000).toISOString().slice(0, 10),
  };
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    const registered = jdStoreRegistry.stores.find((store) => store.storeKey === "jd-yiyong-director");
    if (!registered || registered.platform !== "京东" || registered.shopName !== SHOP_NAME || registered.shopId !== "701455") {
      return Response.json({ error: "运营系统受控店铺注册信息与诊断范围不一致" }, { status: 503 });
    }
    const input = await readBoundedJsonObject(request, MAX_BODY_BYTES);
    const allowed = new Set(["startDate", "endDate", "shopName", "sourceRevision", "target"]);
    if (Object.keys(input).some((key) => !allowed.has(key))
      || input.shopName !== SHOP_NAME || typeof input.sourceRevision !== "string"
      || typeof input.startDate !== "string" || typeof input.endDate !== "string") {
      return Response.json({ error: "推广解读店铺、日期或请求字段无效" }, { status: 400 });
    }
    const period = resolveNetshopQueryPeriod(input.startDate, input.endDate, 7);
    if (!period) return Response.json({ error: "推广解读必须选择1—7个完整自然日" }, { status: 400 });
    const target = input.target as ReportTarget;
    if (!target || typeof target !== "object" || typeof target.tableKey !== "string"
      || typeof target.groupKey !== "string" || Object.keys(target).sort().join(",") !== "groupKey,tableKey") {
      return Response.json({ error: "推广解读对象来源键无效" }, { status: 400 });
    }
    const outlets = [{ platform: "京东", shopName: SHOP_NAME }];
    netshopPlatformsForPrincipal(principal, ["京东"]);
    netshopOutletsForPrincipal(principal, outlets, ["京东"]);
    const service = createDjangoNetshopService();
    async function read(startDate: string, endDate: string) {
      const query = new URLSearchParams({ platform: "京东", outlet: `京东\u001f${SHOP_NAME}`, startDate, endDate });
      const response = await service.request<DiagnosticPeriod>(principal,
        { method: "GET", path: NETSHOP_PROMOTION_DIAGNOSTIC_PATH, query, service: "reader" }, { signal: request.signal });
      return response.data;
    }
    const current = await read(period.startDate, period.endDate);
    if (current.sourceRevision !== input.sourceRevision) {
      return Response.json({ error: "推广来源修订已变化，请重新生成规则报告" }, { status: 409, headers: { "cache-control": "no-store" } });
    }
    const previous = previousPeriod(period.startDate, period.days);
    const baseline = await read(previous.startDate, previous.endDate);
    if (!promotionSystemSourceReady(current, baseline)) {
      return Response.json({ error: "推广来源的系统导入批次归属尚未完整对账，已阻止发送给模型" },
        { status: 409, headers: { "cache-control": "no-store" } });
    }
    const report = appendPromotionRelations(buildPromotionDiagnosticReport(current, baseline), current);
    if (!report.comparisonAvailable) {
      return Response.json({ error: "前等长周期的覆盖或来源修订不可比，请重新生成完整报告" },
        { status: 409, headers: { "cache-control": "no-store" } });
    }
    const prepared = preparePromotionInterpretation(report, target);
    const available = await aiConsumer<{ items: Array<{ id: string; modelName: string; maxTokens: number; isDefaultTextModel: boolean }> }>(
      principal, { operation: "model-list", modelType: "text" }, { signal: request.signal });
    const model = available.items.find((item) => item.isDefaultTextModel);
    if (!model || model.modelName !== "deepseek-v4-flash" || model.maxTokens !== 65_536) {
      return Response.json({ error: "默认文本模型或 65,536 Token 配置已变化，请先复核 AI 管理中的模型" },
        { status: 409, headers: { "cache-control": "no-store" } });
    }
    const generation = await aiConsumer<{ reply: string }>(principal, {
      operation: "analysis-reply", prompt: prepared.prompt, systemPrompt: prepared.systemPrompt,
      surface: "jd_promotion_diagnostic", modelId: model.id,
    }, { signal: request.signal });
    const interpretation = validatePromotionInterpretationReply(generation.reply, prepared);
    return Response.json({ status: "complete", modelDispatched: true, interpretation,
      sourceRevision: prepared.sourceRevision, period: prepared.period, target: prepared.target,
      evidence: prepared.evidence }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "准备推广 AI 解读证据失败", { headers: { "cache-control": "no-store" } });
  }
}
