import { recoverySha, type PreflightEvidence } from "./preflight-recovery";

export function n8nEvidenceTime(value: unknown) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$/.test(value)) throw new Error("n8n 时间证据不明确。");
  const utc = value.replace(" ", "T") + "Z", parsed = Date.parse(utc);
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString() !== utc) throw new Error("n8n 时间证据不明确。");
  return utc;
}

/** Decode only diagnostics, never request headers, tokens or complete node outputs. */
export function decodeN8nPreflightEvidence(row: Record<string, unknown>, data: string, activeExecutions: number): PreflightEvidence {
  const values = JSON.parse(data) as unknown[];
  if (!Array.isArray(values) || !values.length || values.length > 10000) throw new Error("n8n 证据格式不支持。");
  const deref = (value: unknown): unknown => typeof value === "string" && /^\d+$/.test(value) ? values[Number(value)] : value;
  const object = (value: unknown): Record<string, unknown> => {
    const result = deref(value);
    if (!result || typeof result !== "object" || Array.isArray(result)) throw new Error("n8n 诊断对象格式不支持。");
    return result as Record<string, unknown>;
  };
  const root = object(values[0]), result = object(root.resultData), error = object(result.error), node = object(error.node), parameters = object(node.parameters);
  return { executionId: String(row.id), workflowId: String(row.workflowId), status: String(row.status),
    startedAt: n8nEvidenceTime(row.startedAt), stoppedAt: n8nEvidenceTime(row.stoppedAt), retrySuccessId: row.retrySuccessId === null ? null : String(row.retrySuccessId),
    lastNode: String(deref(result.lastNodeExecuted)), runNodes: Object.keys(object(result.runData)),
    error: String(deref(error.description)), httpCode: String(deref(error.httpCode)), requestUrl: String(deref(parameters.url)),
    executionDataSha256: recoverySha(data), activeExecutions };
}
