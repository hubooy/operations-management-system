import { decryptSecret } from "@/lib/ai/crypto";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  loadAiEndpointSecurityContext,
  resolveAiModelEndpointUrl,
} from "@/lib/ai/endpoint-security";
import { throwIfAiRequestCancelled } from "@/lib/ai/cancellation";
import {
  ModelProtocolError,
  runAnthropicToolLoop,
  runOpenAiCompatibleToolLoop,
  type AnthropicContentBlock,
  type AnthropicMessagesResponse,
  type ConversationTextMessage,
  type OpenAiChatCompletionResponse,
  type OpenAiToolCall,
  type ProviderToolDefinition,
  type ProviderToolCallMetadata,
  type ToolExecutionResult,
} from "@/lib/ai/tool-loop";

export type AiTextModelRuntimeConfig = {
  id: string;
  name: string;
  protocol: "openai_compatible" | "anthropic";
  modelName: string;
  baseUrl: string;
  apiKeyEncrypted: string;
  timeoutMs: number;
  maxTokens: number;
  reasoningMode: "auto" | "disabled";
  temperature: number;
  maxToolRounds: number;
  maxTotalToolCalls: number;
};

/**
 * Provider-native frames are deliberately plain JSON so a durable executor can
 * checkpoint them and replay the next turn without reconstructing provider
 * call identifiers. The `protocol` discriminator is never sent on the wire.
 */
export type OpenAiCompatibleTranscriptFrame = {
  protocol: "openai_compatible";
  role: "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: OpenAiToolCall[];
  tool_call_id?: string;
  reasoning_content?: string | null;
};

export type AnthropicTranscriptContentBlock = {
  type: string;
  [key: string]: unknown;
};

export type AnthropicTranscriptFrame = {
  protocol: "anthropic";
  role: "user" | "assistant";
  content: AnthropicTranscriptContentBlock[];
};

export type ModelProviderTranscriptFrame =
  | OpenAiCompatibleTranscriptFrame
  | AnthropicTranscriptFrame;

export type ModelProviderTurnUsage = {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  reasoningTokens?: number;
  cacheReadInputTokens?: number;
  cacheCreationInputTokens?: number;
};

export type ModelProviderTurnToolCall = {
  providerCallId: string;
  name: string;
  arguments: unknown;
};

type ModelProviderTurnBase = {
  protocol: AiTextModelRuntimeConfig["protocol"];
  assistantFrame: ModelProviderTranscriptFrame;
  providerRequestId: string | null;
  usage: ModelProviderTurnUsage | null;
  finishReason: string | null;
};

export type ModelProviderTurnResult =
  | (ModelProviderTurnBase & { kind: "final"; text: string })
  | (ModelProviderTurnBase & { kind: "tool_calls"; toolCalls: ModelProviderTurnToolCall[] });

type OpenAiResponseWithMetadata = OpenAiChatCompletionResponse & { id?: string };
type AnthropicResponseWithMetadata = AnthropicMessagesResponse & {
  id?: string;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_input_tokens?: number;
    cache_creation_input_tokens?: number;
  };
};

type ProviderHttpResult<T> = {
  data: T;
  providerRequestId: string | null;
};

/**
 * Executes exactly one provider HTTP turn. It never executes tools and never
 * loops; callers own tool execution, checkpointing, cancellation and replay.
 */
export async function runModelProviderTurn(input: {
  model: AiTextModelRuntimeConfig;
  transcript: readonly ModelProviderTranscriptFrame[];
  systemPrompt?: string;
  tools?: readonly ProviderToolDefinition[];
  signal?: AbortSignal;
  beforeProviderRequest?: () => Promise<void>;
}): Promise<ModelProviderTurnResult> {
  throwIfAiRequestCancelled(input.signal);
  assertTranscriptMatchesProtocol(input.model.protocol, input.transcript);
  const endpointUrl = await resolveRuntimeModelEndpoint(input.model);
  const apiKey = await requireModelApiKey(input.model);
  throwIfAiRequestCancelled(input.signal);
  await input.beforeProviderRequest?.();
  throwIfAiRequestCancelled(input.signal);

  if (input.model.protocol === "anthropic") {
    const response = await requestAnthropicEnvelope(input.model, endpointUrl, apiKey, {
      ...(input.systemPrompt ? { system: input.systemPrompt } : {}),
      messages: input.transcript.map(toAnthropicWireFrame),
      ...(input.tools?.length ? { tools: [...input.tools] } : {}),
    }, input.signal);
    throwIfAiRequestCancelled(input.signal);
    return normalizeAnthropicTurn(response);
  }

  const response = await requestOpenAiEnvelope(input.model, endpointUrl, apiKey, {
    messages: [
      ...(input.systemPrompt ? [{ role: "system", content: input.systemPrompt }] : []),
      ...input.transcript.map(toOpenAiWireFrame),
    ],
    ...(input.tools?.length ? { tools: [...input.tools], tool_choice: "auto" } : {}),
  }, input.signal);
  throwIfAiRequestCancelled(input.signal);
  return normalizeOpenAiTurn(response);
}

export async function completeText(input: {
  model: AiTextModelRuntimeConfig;
  messages: ConversationTextMessage[];
  systemPrompt?: string;
  signal?: AbortSignal;
}): Promise<string> {
  throwIfAiRequestCancelled(input.signal);
  const endpointUrl = await resolveRuntimeModelEndpoint(input.model);
  const apiKey = await requireModelApiKey(input.model);
  return input.model.protocol === "anthropic"
    ? completeAnthropicText({ ...input, apiKey, endpointUrl })
    : completeOpenAiText({ ...input, apiKey, endpointUrl });
}

export async function completeTextWithTools(input: {
  model: AiTextModelRuntimeConfig;
  messages: ConversationTextMessage[];
  systemPrompt: string;
  tools: ProviderToolDefinition[];
  executeTool: (name: string, rawArguments: unknown, metadata: ProviderToolCallMetadata) => Promise<ToolExecutionResult>;
  signal?: AbortSignal;
  beforeProviderRequest?: () => Promise<void>;
}): Promise<string> {
  throwIfAiRequestCancelled(input.signal);
  const endpointUrl = await resolveRuntimeModelEndpoint(input.model);
  const apiKey = await requireModelApiKey(input.model);
  const limits = resolveModelToolLoopLimits(input.model);
  if (input.model.protocol === "anthropic") {
    return runAnthropicToolLoop({
      messages: input.messages,
      tools: input.tools,
      executeTool: input.executeTool,
      systemPrompt: input.systemPrompt,
      signal: input.signal,
      limits,
      request: async (body) => {
        throwIfAiRequestCancelled(input.signal);
        await input.beforeProviderRequest?.();
        throwIfAiRequestCancelled(input.signal);
        return requestAnthropic(input.model, endpointUrl, apiKey, body, input.signal);
      },
    });
  }
  return runOpenAiCompatibleToolLoop({
    messages: input.messages,
    tools: input.tools,
    executeTool: input.executeTool,
    systemPrompt: input.systemPrompt,
    signal: input.signal,
    limits,
    request: async (body) => {
      throwIfAiRequestCancelled(input.signal);
      await input.beforeProviderRequest?.();
      throwIfAiRequestCancelled(input.signal);
      return requestOpenAi(input.model, endpointUrl, apiKey, body, input.signal);
    },
  });
}

export function resolveModelToolLoopLimits(
  model: Pick<AiTextModelRuntimeConfig, "maxToolRounds" | "maxTotalToolCalls">,
) {
  return {
    maxRounds: model.maxToolRounds,
    // A provider may efficiently request several independent reads in one
    // response. Do not impose the former fixed limit of four; the configured
    // total-call budget, runtime duration, per-tool caps and cancellation still
    // provide bounded execution and loop protection.
    maxCallsPerRound: model.maxTotalToolCalls,
    maxTotalCalls: model.maxTotalToolCalls,
  };
}

async function completeOpenAiText(input: {
  model: AiTextModelRuntimeConfig;
  messages: ConversationTextMessage[];
  systemPrompt?: string;
  signal?: AbortSignal;
  apiKey: string;
  endpointUrl: string;
}): Promise<string> {
  const data = await requestOpenAi(input.model, input.endpointUrl, input.apiKey, {
    messages: [
      ...(input.systemPrompt ? [{ role: "system", content: input.systemPrompt }] : []),
      ...input.messages,
    ],
  }, input.signal);
  return data.choices?.[0]?.message?.content?.trim() ?? "";
}

async function completeAnthropicText(input: {
  model: AiTextModelRuntimeConfig;
  messages: ConversationTextMessage[];
  systemPrompt?: string;
  signal?: AbortSignal;
  apiKey: string;
  endpointUrl: string;
}): Promise<string> {
  const data = await requestAnthropic(input.model, input.endpointUrl, input.apiKey, {
    ...(input.systemPrompt ? { system: input.systemPrompt } : {}),
    messages: input.messages.map((message) => ({
      role: message.role,
      content: [{ type: "text", text: message.content }],
    })),
  }, input.signal);
  return (data.content ?? [])
    .map((block) => block.type === "text" ? block.text : "")
    .join("")
    .trim();
}

async function requestOpenAi(
  model: AiTextModelRuntimeConfig,
  endpointUrl: string,
  apiKey: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<OpenAiChatCompletionResponse> {
  return (await requestOpenAiEnvelope(model, endpointUrl, apiKey, body, signal)).data;
}

async function requestOpenAiEnvelope(
  model: AiTextModelRuntimeConfig,
  endpointUrl: string,
  apiKey: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<ProviderHttpResult<OpenAiResponseWithMetadata>> {
  const { response, data: rawData } = await fetchBoundedJson({
    url: endpointUrl,
    init: {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}` },
      body: JSON.stringify(buildOpenAiChatRequestBody(model, body)),
    },
    timeoutMs: model.timeoutMs,
    signal,
  });
  const data = rawData as (OpenAiResponseWithMetadata & { error?: { message?: string } }) | null;
  if (!response.ok) throw new Error(providerFailure("模型调用失败", response.status, data?.error?.message));
  if (!data) throw new ModelProtocolError("OpenAI-compatible 响应不是有效 JSON");
  return { data, providerRequestId: extractProviderRequestId(response, data.id) };
}

export function buildOpenAiChatRequestBody(
  model: Pick<AiTextModelRuntimeConfig, "modelName" | "maxTokens" | "reasoningMode" | "temperature">,
  body: Record<string, unknown>,
): Record<string, unknown> {
  return {
    model: model.modelName,
    ...body,
    ...(model.reasoningMode === "disabled" ? { thinking: { type: "disabled" } } : {}),
    max_tokens: model.maxTokens,
    temperature: model.temperature,
  };
}

async function requestAnthropic(
  model: AiTextModelRuntimeConfig,
  endpointUrl: string,
  apiKey: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<AnthropicMessagesResponse> {
  return (await requestAnthropicEnvelope(model, endpointUrl, apiKey, body, signal)).data;
}

async function requestAnthropicEnvelope(
  model: AiTextModelRuntimeConfig,
  endpointUrl: string,
  apiKey: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<ProviderHttpResult<AnthropicResponseWithMetadata>> {
  const { response, data: rawData } = await fetchBoundedJson({
    url: endpointUrl,
    init: {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: model.modelName,
        max_tokens: model.maxTokens,
        temperature: model.temperature,
        ...body,
      }),
    },
    timeoutMs: model.timeoutMs,
    signal,
  });
  const data = rawData as (AnthropicResponseWithMetadata & { error?: { message?: string } }) | null;
  if (!response.ok) throw new Error(providerFailure("模型调用失败", response.status, data?.error?.message));
  if (!data) throw new ModelProtocolError("Anthropic 响应不是有效 JSON");
  return { data, providerRequestId: extractProviderRequestId(response, data.id) };
}

function assertTranscriptMatchesProtocol(
  protocol: AiTextModelRuntimeConfig["protocol"],
  transcript: readonly ModelProviderTranscriptFrame[],
): void {
  for (const frame of transcript) {
    if (frame.protocol !== protocol) {
      throw new ModelProtocolError("会话帧与当前模型协议不匹配");
    }
    if (frame.protocol === "openai_compatible") validateOpenAiTranscriptFrame(frame);
    else validateAnthropicTranscriptFrame(frame);
  }
}

function validateOpenAiTranscriptFrame(frame: OpenAiCompatibleTranscriptFrame): void {
  if (frame.role === "user") {
    if (typeof frame.content !== "string"
      || frame.tool_call_id !== undefined
      || frame.tool_calls !== undefined
      || frame.reasoning_content !== undefined) {
      throw new ModelProtocolError("OpenAI-compatible user 会话帧格式无效");
    }
    return;
  }
  if (frame.role === "tool") {
    if (typeof frame.content !== "string"
      || !isNonEmptyString(frame.tool_call_id)
      || frame.tool_calls !== undefined
      || frame.reasoning_content !== undefined) {
      throw new ModelProtocolError("OpenAI-compatible tool 会话帧格式无效");
    }
    return;
  }
  if (frame.content !== null && typeof frame.content !== "string") {
    throw new ModelProtocolError("OpenAI-compatible assistant 会话帧 content 无效");
  }
  if (frame.tool_call_id !== undefined) {
    throw new ModelProtocolError("OpenAI-compatible assistant 会话帧不应包含 tool_call_id");
  }
  if (frame.reasoning_content !== undefined
    && frame.reasoning_content !== null
    && typeof frame.reasoning_content !== "string") {
    throw new ModelProtocolError("OpenAI-compatible assistant 会话帧 reasoning_content 无效");
  }
  for (const call of frame.tool_calls ?? []) validateOpenAiToolCall(call);
}

function validateAnthropicTranscriptFrame(frame: AnthropicTranscriptFrame): void {
  if (!Array.isArray(frame.content)) throw new ModelProtocolError("Anthropic 会话帧 content 必须是块数组");
  for (const block of frame.content) {
    if (!block || typeof block !== "object" || !isNonEmptyString(block.type)) {
      throw new ModelProtocolError("Anthropic 会话帧包含无效内容块");
    }
    if (block.type === "text" && typeof block.text !== "string") {
      throw new ModelProtocolError("Anthropic text 内容块缺少 text");
    }
    if (block.type === "tool_use"
      && (!isNonEmptyString(block.id) || !isNonEmptyString(block.name) || !("input" in block))) {
      throw new ModelProtocolError("Anthropic tool_use 内容块格式无效");
    }
    if (block.type === "tool_result" && (!isNonEmptyString(block.tool_use_id) || typeof block.content !== "string")) {
      throw new ModelProtocolError("Anthropic tool_result 内容块格式无效");
    }
  }
}

function toOpenAiWireFrame(frame: ModelProviderTranscriptFrame): Record<string, unknown> {
  if (frame.protocol !== "openai_compatible") throw new ModelProtocolError("会话帧与 OpenAI-compatible 协议不匹配");
  const wireFrame: Record<string, unknown> = { ...frame };
  delete wireFrame.protocol;
  return wireFrame;
}

function toAnthropicWireFrame(frame: ModelProviderTranscriptFrame): Record<string, unknown> {
  if (frame.protocol !== "anthropic") throw new ModelProtocolError("会话帧与 Anthropic 协议不匹配");
  return { role: frame.role, content: frame.content };
}

function normalizeOpenAiTurn(
  response: ProviderHttpResult<OpenAiResponseWithMetadata>,
): ModelProviderTurnResult {
  const choice = response.data.choices?.[0];
  const message = choice?.message;
  if (!message) throw new ModelProtocolError("OpenAI-compatible 响应缺少 choices[0].message");
  if (message.content !== undefined && message.content !== null && typeof message.content !== "string") {
    throw new ModelProtocolError("OpenAI-compatible 响应 content 格式无效");
  }
  if (message.tool_calls !== undefined && !Array.isArray(message.tool_calls)) {
    throw new ModelProtocolError("OpenAI-compatible 响应 tool_calls 必须是数组");
  }
  const toolCalls = message.tool_calls ?? [];
  for (const call of toolCalls) validateOpenAiToolCall(call);
  const assistantFrame: OpenAiCompatibleTranscriptFrame = {
    protocol: "openai_compatible",
    role: "assistant",
    content: message.content ?? null,
    ...(message.reasoning_content !== undefined ? { reasoning_content: message.reasoning_content } : {}),
    ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
  };
  const base = {
    protocol: "openai_compatible" as const,
    assistantFrame,
    providerRequestId: response.providerRequestId,
    usage: normalizeOpenAiUsage(response.data.usage),
    finishReason: choice?.finish_reason ?? null,
  };
  if (toolCalls.length) {
    return {
      ...base,
      kind: "tool_calls",
      toolCalls: toolCalls.map((call) => ({
        providerCallId: call.id,
        name: call.function.name,
        arguments: call.function.arguments,
      })),
    };
  }
  const text = message.content?.trim() ?? "";
  if (!text) throw new ModelProtocolError("模型未返回最终正文");
  return { ...base, kind: "final", text };
}

function normalizeAnthropicTurn(
  response: ProviderHttpResult<AnthropicResponseWithMetadata>,
): ModelProviderTurnResult {
  if (response.data.content !== undefined && !Array.isArray(response.data.content)) {
    throw new ModelProtocolError("Anthropic 响应 content 必须是数组");
  }
  const blocks = response.data.content ?? [];
  const assistantFrame: AnthropicTranscriptFrame = {
    protocol: "anthropic",
    role: "assistant",
    content: blocks as unknown as AnthropicTranscriptContentBlock[],
  };
  const toolCalls: ModelProviderTurnToolCall[] = [];
  for (const block of blocks) {
    if (!block || typeof block !== "object" || !isNonEmptyString(block.type)) {
      throw new ModelProtocolError("Anthropic 响应包含无效内容块");
    }
    if (block.type !== "tool_use") continue;
    if (!isNonEmptyString(block.id) || !isNonEmptyString(block.name) || !("input" in block)) {
      throw new ModelProtocolError("Anthropic 工具调用缺少 id、name 或 input");
    }
    toolCalls.push({ providerCallId: block.id, name: block.name, arguments: block.input });
  }
  const base = {
    protocol: "anthropic" as const,
    assistantFrame,
    providerRequestId: response.providerRequestId,
    usage: normalizeAnthropicUsage(response.data.usage),
    finishReason: response.data.stop_reason ?? null,
  };
  if (toolCalls.length) return { ...base, kind: "tool_calls", toolCalls };
  const text = blocks
    .filter((block): block is Extract<AnthropicContentBlock, { type: "text" }> => block.type === "text")
    .map((block) => block.text)
    .join("")
    .trim();
  if (!text) throw new ModelProtocolError("Anthropic 模型未返回最终正文");
  return { ...base, kind: "final", text };
}

function validateOpenAiToolCall(call: OpenAiToolCall): void {
  if (!isNonEmptyString(call?.id)
    || !isNonEmptyString(call?.function?.name)
    || typeof call?.function?.arguments !== "string") {
    throw new ModelProtocolError("OpenAI-compatible 工具调用缺少 id、name 或 arguments");
  }
}

function normalizeOpenAiUsage(
  usage: OpenAiChatCompletionResponse["usage"],
): ModelProviderTurnUsage | null {
  if (!usage) return null;
  return compactUsage({
    inputTokens: tokenCount(usage.prompt_tokens),
    outputTokens: tokenCount(usage.completion_tokens),
    totalTokens: tokenCount(usage.total_tokens),
    reasoningTokens: tokenCount(usage.completion_tokens_details?.reasoning_tokens),
  });
}

function normalizeAnthropicUsage(
  usage: AnthropicResponseWithMetadata["usage"],
): ModelProviderTurnUsage | null {
  if (!usage) return null;
  const inputTokens = tokenCount(usage.input_tokens);
  const outputTokens = tokenCount(usage.output_tokens);
  return compactUsage({
    inputTokens,
    outputTokens,
    ...(inputTokens !== undefined && outputTokens !== undefined ? { totalTokens: inputTokens + outputTokens } : {}),
    cacheReadInputTokens: tokenCount(usage.cache_read_input_tokens),
    cacheCreationInputTokens: tokenCount(usage.cache_creation_input_tokens),
  });
}

function compactUsage(usage: ModelProviderTurnUsage): ModelProviderTurnUsage | null {
  const entries = Object.entries(usage).filter((entry): entry is [string, number] => entry[1] !== undefined);
  return entries.length ? Object.fromEntries(entries) as ModelProviderTurnUsage : null;
}

function tokenCount(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : undefined;
}

function extractProviderRequestId(response: Response, bodyId?: string): string | null {
  for (const value of [
    response.headers.get("x-request-id"),
    response.headers.get("request-id"),
    response.headers.get("openai-request-id"),
    response.headers.get("anthropic-request-id"),
    bodyId,
  ]) {
    const normalized = value?.trim().replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 160);
    if (normalized) return normalized;
  }
  return null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

async function resolveRuntimeModelEndpoint(model: AiTextModelRuntimeConfig): Promise<string> {
  const context = await loadAiEndpointSecurityContext();
  return resolveAiModelEndpointUrl(model.baseUrl, model.protocol, context);
}

async function requireModelApiKey(model: AiTextModelRuntimeConfig): Promise<string> {
  const apiKey = await decryptSecret(model.apiKeyEncrypted);
  if (!apiKey) throw new Error("模型 API Key 未配置");
  return apiKey;
}

function providerFailure(prefix: string, status: number, message?: string): string {
  const safeMessage = message
    ?.replace(/\s+/g, " ")
    .replace(/\b(sk-|key-)[A-Za-z0-9_-]{8,}\b/gi, "$1…")
    .replace(/(authorization\s*[:=]?\s*bearer\s+)\S+/gi, "$1…")
    .replace(/(api[_ -]?key\s*[:=]\s*)\S+/gi, "$1…")
    .slice(0, 160);
  return `${prefix}: ${status}${safeMessage ? ` · ${safeMessage}` : ""}`;
}
