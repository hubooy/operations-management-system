import { PublicApiError } from "@/lib/http/api-error";
import { appendPromotionRelations } from "./promotion-diagnostic-relations";
import { buildPromotionDiagnosticReport, type DiagnosticPeriod, type PromotionDiagnosticReport } from "./promotion-diagnostic-report";
import { promotionSystemSourceReady } from "./promotion-diagnostic-identity";

export type PromotionChatLocator = {
  shopName: string;
  startDate: string;
  endDate: string;
  sourceRevision: string;
};

const SHOP = "志高商用设备旗舰店";
function date(value: unknown): value is string {
  return typeof value === "string" && /^20\d\d-\d\d-\d\d$/.test(value)
    && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

export function parsePromotionChatLocator(value: unknown): PromotionChatLocator {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new PublicApiError(404, "not_found", "这条消息没有推广诊断报告。");
  const input = value as Record<string, unknown>;
  if (Object.keys(input).sort().join(",") !== "endDate,shopName,sourceRevision,startDate"
    || input.shopName !== SHOP || !date(input.startDate) || !date(input.endDate)
    || input.startDate > input.endDate || typeof input.sourceRevision !== "string"
    || !/^[1-9]\d{0,18}:[0-9a-f]{12}$/.test(input.sourceRevision)) {
    throw new PublicApiError(404, "not_found", "这条消息的推广诊断范围无效。");
  }
  const days = (Date.parse(`${input.endDate}T00:00:00Z`) - Date.parse(`${input.startDate}T00:00:00Z`)) / 86_400_000 + 1;
  if (days < 1 || days > 7) throw new PublicApiError(404, "not_found", "这条消息的推广诊断日期无效。");
  return { shopName: SHOP, startDate: input.startDate, endDate: input.endDate, sourceRevision: input.sourceRevision };
}

export function locatorFromChatMessage(payload: unknown, conversationId: string, messageId: string) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new PublicApiError(404, "not_found", "对话消息不存在。");
  const items = (payload as { items?: unknown }).items;
  if (!Array.isArray(items) || items.length !== 1) throw new PublicApiError(404, "not_found", "对话消息不存在。");
  const message = items[0];
  if (!message || typeof message !== "object" || Array.isArray(message)
    || message.id !== messageId || message.conversationId !== conversationId || message.role !== "assistant") {
    throw new PublicApiError(404, "not_found", "对话消息不存在。");
  }
  return parsePromotionChatLocator(message.execution?.promotionReport);
}

export function interpretationFromChatMessage(payload: unknown, conversationId: string, messageId: string) {
  const locator = locatorFromChatMessage(payload, conversationId, messageId);
  const message = (payload as { items: Array<{ content?: unknown; contentTruncated?: unknown }> }).items[0];
  if (typeof message.content !== "string" || !message.content.trim()
    || new TextEncoder().encode(message.content).byteLength > 16 * 1024
    || message.contentTruncated === true || /[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(message.content)) {
    throw new PublicApiError(413, "payload_too_large", "原对话解读过长或不完整，不能作为完整报告附件；请缩小问题后重新生成。");
  }
  return { locator, text: message.content };
}

export function previousPromotionChatPeriod(locator: PromotionChatLocator) {
  const days = (Date.parse(`${locator.endDate}T00:00:00Z`) - Date.parse(`${locator.startDate}T00:00:00Z`)) / 86_400_000 + 1;
  const start = Date.parse(`${locator.startDate}T00:00:00Z`);
  return {
    startDate: new Date(start - days * 86_400_000).toISOString().slice(0, 10),
    endDate: new Date(start - 86_400_000).toISOString().slice(0, 10),
  };
}

export function buildPromotionChatReport(locator: PromotionChatLocator, current: DiagnosticPeriod, baseline: DiagnosticPeriod) {
  const previous = previousPromotionChatPeriod(locator);
  if (current.identity.platform !== "京东" || current.identity.shopName !== locator.shopName
    || current.period.startDate !== locator.startDate || current.period.endDate !== locator.endDate
    || baseline.identity.platform !== "京东" || baseline.identity.shopName !== locator.shopName
    || baseline.period.startDate !== previous.startDate || baseline.period.endDate !== previous.endDate
    || current.sourceRevision !== locator.sourceRevision || baseline.sourceRevision !== locator.sourceRevision) {
    throw new PublicApiError(409, "version_conflict", "推广来源修订或日期已变化，请在 AI 对话中重新生成诊断。");
  }
  if (!promotionSystemSourceReady(current, baseline)) {
    throw new PublicApiError(409, "conflict", "推广来源批次归属或覆盖不完整，暂不能下载完整报告。");
  }
  const report = appendPromotionRelations(buildPromotionDiagnosticReport(current, baseline), current);
  if (!report.complete || !report.comparisonAvailable) {
    throw new PublicApiError(409, "conflict", "推广来源不完整或前期不可比，请重新生成诊断。");
  }
  return report;
}

export function attachPromotionChatInterpretation(report: PromotionDiagnosticReport, content: string, messageId: string) {
  if (!/^[A-Za-z0-9_-]{1,160}$/.test(messageId) || !content.trim()
    || new TextEncoder().encode(content).byteLength > 16 * 1024) {
    throw new PublicApiError(413, "payload_too_large", "AI 对话解读不能完整写入报告。");
  }
  const oldRule = "规则诊断仅列人工复核候选；未调用模型，也未自动修改投放。";
  return { ...report,
    limitations: report.limitations.map((item) => item === oldRule
      ? "数值与行动候选由规则计算；另附本条 AI 对话原文。该原文未逐条核验数字、日期与引用，不能替代来源表，需人工复核；未自动修改投放。" : item),
    tables: [...report.tables, {
      key: "chatInterpretation", title: "AI对话原文（未逐项核验）",
      note: "本表逐字保存本条助手消息正文，但未逐条核验其中的数字、日期与引用。请以其他确定性表为准，并人工复核对话判断。",
      columns: [
        { key: "verification", label: "文字校验状态", kind: "text" as const },
        { key: "sourceRevision", label: "来源修订", kind: "text" as const },
        { key: "messageId", label: "对话消息ID", kind: "text" as const },
        { key: "content", label: "AI 对话解读原文", kind: "text" as const },
      ],
      rows: [["未逐条核验数字/日期/引用；待人工复核", report.sourceRevision, messageId, content]],
    }],
  } satisfies PromotionDiagnosticReport;
}
