import { aiConsumer } from "@/lib/django/ai-service";
import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import {
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { readBoundedJsonObject } from "@/lib/http/bounded-json";

type MarketAiRequest = {
  question?: string;
  query?: string;
  categories?: string[];
  scopes?: string[];
  brands?: string[];
  rankingDimensions?: string[];
  operationModes?: string[];
  subcategories?: string[];
  priceBands?: string[];
  startDate?: string;
  endDate?: string;
};

const MARKET_AI_SYSTEM_PROMPT = [
  "你是运营管理系统的市场分析助手，只能依据本次请求已注入的市场数据回答。",
  "本入口不提供工具调用，不要声称调用工具、查询其他系统数据或补全未提供的指标。",
  "金额单位为人民币分，转化率单位为基点（100 基点=1%）；回答时必须说明数据日期范围和统计口径。",
].join("\n");
const MARKET_AI_BODY_BYTES_MAX = 64 * 1024;

function safeList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).map((item) => item.trim()).filter(Boolean).slice(0, 20) : [];
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    if (principal.scope !== null) {
      return Response.json(
        { error: "市场数据没有可安全映射的账号数据范围，已阻止发送到云模型" },
        { status: 403, headers: { "cache-control": "no-store" } },
      );
    }
    const body = await readBoundedJsonObject(request, MARKET_AI_BODY_BYTES_MAX) as MarketAiRequest;
    const market = await requestDjangoMarketService<{
      summary: Record<string, number | null> & { productCount: number };
      items: Array<Record<string, unknown>>;
      dataRange: { startDate: string | null; endDate: string | null };
    }>(principal, {
      path: MARKET_QUERIES_PATH,
      service: "reader",
      payload: {
        operation: "overview",
        view: "full",
        page: 1,
        pageSize: 50,
        filters: {
          query: body.query?.trim().slice(0, 120) || "",
          categories: safeList(body.categories),
          scopes: safeList(body.scopes),
          brands: safeList(body.brands),
          rankingDimensions: safeList(body.rankingDimensions),
          operationModes: safeList(body.operationModes),
          subcategories: safeList(body.subcategories),
          priceBands: safeList(body.priceBands),
          startDate: /^\d{4}-\d{2}-\d{2}$/.test(body.startDate ?? "") ? body.startDate : null,
          endDate: /^\d{4}-\d{2}-\d{2}$/.test(body.endDate ?? "") ? body.endDate : null,
        },
      },
    }, { signal: request.signal });
    const overview = market.data;
    if (!overview.summary.productCount) return Response.json({ error: "当前筛选范围没有市场数据，请先导入榜单或 SKU 数据" }, { status: 400 });
    const topItems = overview.items.slice(0, 12).map((item) => ({
      rank: item.rank,
      sku: item.skuCode,
      name: item.productName,
      brand: item.brand,
      category: item.category,
      marketGmvCents: item.gmvCents,
      marketPositionPriceCents: item.marketPriceCents,
      averageTransactionPriceCents: item.averageTransactionPriceCents,
      operationMode: item.operationMode,
      rankingDimension: item.rankingDimension,
      visitors: item.visitors,
      conversionBps: item.conversionBps,
      ownProduct: item.isOwn,
      ownSalesCents: item.ownSalesCents,
    }));
    const question = body.question?.trim().slice(0, 1200) || "请总结市场机会、竞品风险与下一步运营动作";
    const prompt = [
      "你是电商运营系统的市场分析助手。仅依据下面的真实导入数据回答，不要编造缺失指标。",
      "金额单位为人民币分，转化率单位为基点（100 基点=1%）。市场规模、品牌份额和自营占比均是当前 TOP 榜单覆盖口径，不代表完整行业市场。商品价格必须区分市场定位价（主图）与成交均价。",
      `用户问题：${question}`,
      `筛选数据范围：${overview.dataRange.startDate ?? "未知"} 至 ${overview.dataRange.endDate ?? "未知"}`,
      `汇总：${JSON.stringify(overview.summary)}`,
      `头部商品：${JSON.stringify(topItems)}`,
    ].join("\n");
    const generation = await aiConsumer<{ reply: string; conversationId: string }>(principal, {
      operation: "analysis-reply", title: `市场分析：${question.slice(0, 36)}`, prompt,
      systemPrompt: MARKET_AI_SYSTEM_PROMPT, surface: "market_ai",
    }, { signal: request.signal });
    return Response.json({ ok: true, answer: generation.reply, conversationId: generation.conversationId, dataRange: overview.dataRange });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "AI 市场分析失败", { headers: { "cache-control": "no-store" } });
  }
}
