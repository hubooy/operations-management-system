import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  DjangoMarketServiceResponseError,
  MARKET_COMMANDS_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { readBoundedJsonObject } from "@/lib/http/bounded-json";
import {
  AnnotationAgentError,
  annotationAgentErrorResponse,
} from "@/lib/market/annotation-agent-errors";
import { runClaimedDjangoMarketVisionTask } from "@/lib/market/django-annotation-runner";

type JsonRecord = Record<string, unknown>;
const MARKET_AGENT_BODY_BYTES_MAX = 256 * 1024;
const INTERNAL_AGENT_PRINCIPAL: AppPrincipal = {
  email: "market-agent-gateway@teruisi.internal",
  displayName: "市场本地标注网关",
  role: "operator",
  scope: null,
};

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function agentToken(request: Request): string {
  const authorization = request.headers.get("authorization") ?? "";
  const match = authorization.match(/^Bearer\s+(teruisi_ma_[A-Za-z0-9]+)$/i);
  if (!match?.[1] || match[1].length > 160) throw new AnnotationAgentError("authentication");
  return match[1];
}

async function agentCommand(command: JsonRecord, signal?: AbortSignal) {
  return requestDjangoMarketService<{ ok: boolean; result: JsonRecord }>(
    INTERNAL_AGENT_PRINCIPAL,
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

export async function POST(request: Request) {
  try {
    const token = agentToken(request);
    const parsed: unknown = await readBoundedJsonObject(request, MARKET_AGENT_BODY_BYTES_MAX)
      .catch(() => null);
    if (!isRecord(parsed)) throw new AnnotationAgentError("bad_request");
    const action = typeof parsed.action === "string" ? parsed.action : "claim";
    if (action === "heartbeat") {
      const result = await agentCommand(
        { action: "agent_heartbeat", agentToken: token },
        request.signal,
      );
      return Response.json(
        { ok: true, ...result.data.result },
        { headers: { "cache-control": "no-store" } },
      );
    }
    if (action === "claim") {
      const result = await agentCommand(
        { action: "agent_claim", agentToken: token },
        request.signal,
      );
      return Response.json(
        { ok: true, ...result.data.result },
        { headers: { "cache-control": "no-store" } },
      );
    }
    if (action === "pump_cloud") {
      if (parsed.jobId !== undefined && typeof parsed.jobId !== "string") {
        throw new AnnotationAgentError("bad_request");
      }
      await agentCommand(
        { action: "agent_heartbeat", agentToken: token },
        request.signal,
      );
      const result = await runClaimedDjangoMarketVisionTask({
        principal: INTERNAL_AGENT_PRINCIPAL,
        jobId: typeof parsed.jobId === "string" ? parsed.jobId.trim() : "",
        signal: request.signal,
      });
      return Response.json(
        { ok: true, ...(isRecord(result.data.result) ? result.data.result : {}) },
        { headers: { "cache-control": "no-store" } },
      );
    }
    if (action === "complete") {
      if (typeof parsed.itemId !== "string" || !parsed.itemId.trim()
        || typeof parsed.leaseToken !== "string" || !parsed.leaseToken.trim()) {
        throw new AnnotationAgentError("bad_request");
      }
      const rawResult = isRecord(parsed.result) ? parsed.result : parsed.result;
      const resultValue = isRecord(rawResult)
        ? {
            ...rawResult,
            resolvedImageUrl: typeof parsed.resolvedImageUrl === "string" ? parsed.resolvedImageUrl : "",
            imageSource: typeof parsed.imageSource === "string" ? parsed.imageSource : "none",
          }
        : rawResult;
      const result = await agentCommand(
        {
          action: "agent_complete",
          agentToken: token,
          itemId: parsed.itemId.trim(),
          leaseToken: parsed.leaseToken.trim(),
          result: resultValue,
          error: typeof parsed.error === "string" ? parsed.error.slice(0, 800) : "",
          resolvedImageUrl: typeof parsed.resolvedImageUrl === "string" ? parsed.resolvedImageUrl : "",
          imageSource: typeof parsed.imageSource === "string" ? parsed.imageSource : "none",
        },
        request.signal,
      );
      return Response.json(
        { ok: true, result: result.data.result },
        { headers: { "cache-control": "no-store" } },
      );
    }
    throw new AnnotationAgentError("bad_request");
  } catch (error) {
    if (error instanceof DjangoMarketServiceResponseError) {
      if (error.status === 403) error = new AnnotationAgentError("authentication");
      else if (error.status === 409) error = new AnnotationAgentError("lease_conflict");
      else if (error.status === 400) error = new AnnotationAgentError("bad_request");
    }
    const response = annotationAgentErrorResponse(error);
    return Response.json(
      { error: response.error },
      { status: response.status, headers: { "cache-control": "no-store" } },
    );
  }
}
