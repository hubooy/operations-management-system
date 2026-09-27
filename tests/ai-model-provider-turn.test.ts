import assert from "node:assert/strict";
import test from "node:test";

import { encryptSecret } from "../lib/ai/crypto";
import {
  runModelProviderTurn,
  type AiTextModelRuntimeConfig,
  type ModelProviderTranscriptFrame,
} from "../lib/ai/model-gateway";
import { ModelProtocolError } from "../lib/ai/tool-loop";

const originalFetch = globalThis.fetch;

function runtimeModel(
  protocol: AiTextModelRuntimeConfig["protocol"],
  apiKeyEncrypted: string,
): AiTextModelRuntimeConfig {
  return {
    id: `${protocol}-model`,
    name: `${protocol} model`,
    protocol,
    modelName: "provider-test-model",
    baseUrl: "https://api.openai.com/v1",
    apiKeyEncrypted,
    timeoutMs: 3_000,
    maxTokens: 512,
    reasoningMode: "auto",
    temperature: 0,
    maxToolRounds: 6,
    maxTotalToolCalls: 12,
  };
}

test("single provider turns replay JSON transcript frames and normalize OpenAI and Anthropic results", async () => {
  const environment = process.env as Record<string, string | undefined>;
  const previousEncryptionKey = environment.AI_SECRET_ENCRYPTION_KEY;
  environment.AI_SECRET_ENCRYPTION_KEY = "provider-turn-test-encryption-key";
  const encryptedApiKey = await encryptSecret("provider-test-api-key");
  const requests: Array<{ url: string; init?: RequestInit; body: Record<string, unknown> }> = [];
  const responses = [
    new Response(JSON.stringify({
      id: "chatcmpl-body-1",
      choices: [{
        message: {
          role: "assistant",
          content: null,
          reasoning_content: "private planning frame",
          tool_calls: [{
            id: "call-current-1",
            type: "function",
            function: { name: "lookup_inventory", arguments: "{\"limit\":2}" },
          }],
        },
        finish_reason: "tool_calls",
      }],
      usage: {
        prompt_tokens: 21,
        completion_tokens: 7,
        total_tokens: 28,
        completion_tokens_details: { reasoning_tokens: 3 },
      },
    }), { status: 200, headers: { "content-type": "application/json", "x-request-id": "req-openai-1" } }),
    new Response(JSON.stringify({
      id: "msg-anthropic-1",
      content: [
        { type: "text", text: "I will query." },
        { type: "tool_use", id: "use-current-1", name: "lookup_sales", input: { limit: 3 } },
      ],
      stop_reason: "tool_use",
      usage: {
        input_tokens: 30,
        output_tokens: 9,
        cache_read_input_tokens: 4,
        cache_creation_input_tokens: 5,
      },
    }), { status: 200, headers: { "content-type": "application/json" } }),
    new Response(JSON.stringify({
      id: "chatcmpl-final",
      choices: [{ message: { role: "assistant", content: "  OpenAI final  " }, finish_reason: "stop" }],
      usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
    }), { status: 200, headers: { "content-type": "application/json" } }),
    new Response(JSON.stringify({
      id: "msg-final",
      content: [{ type: "text", text: "  Anthropic " }, { type: "text", text: "final  " }],
      stop_reason: "end_turn",
      usage: { input_tokens: 5, output_tokens: 3 },
    }), { status: 200, headers: { "content-type": "application/json", "request-id": "req-anthropic-final" } }),
  ];
  globalThis.fetch = (async (url, init) => {
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    requests.push({ url: String(url), init, body });
    const response = responses.shift();
    if (!response) throw new Error("unexpected extra provider request");
    return response;
  }) as typeof fetch;

  try {
    const openAiTranscript: ModelProviderTranscriptFrame[] = [
      { protocol: "openai_compatible", role: "user", content: "check inventory" },
      {
        protocol: "openai_compatible",
        role: "assistant",
        content: null,
        reasoning_content: "prior private frame",
        tool_calls: [{
          id: "call-prior-1",
          type: "function",
          function: { name: "lookup_inventory", arguments: "{}" },
        }],
      },
      { protocol: "openai_compatible", role: "tool", tool_call_id: "call-prior-1", content: "{\"returned\":1}" },
    ];
    const persistedOpenAiTranscript = JSON.parse(JSON.stringify(openAiTranscript));
    let beforeOpenAiRequest = 0;
    const openAiToolTurn = await runModelProviderTurn({
      model: runtimeModel("openai_compatible", encryptedApiKey),
      transcript: openAiTranscript,
      systemPrompt: "system",
      tools: [{ type: "function", function: { name: "lookup_inventory", parameters: { type: "object" } } }],
      beforeProviderRequest: async () => { beforeOpenAiRequest += 1; },
    });
    assert.equal(requests.length, 1);
    assert.equal(beforeOpenAiRequest, 1);
    assert.equal(requests[0].url, "https://api.openai.com/v1/chat/completions");
    assert.equal(new Headers(requests[0].init?.headers).get("authorization"), "Bearer provider-test-api-key");
    assert.deepEqual((requests[0].body.messages as unknown[]).slice(1), persistedOpenAiTranscript.map((frame: Record<string, unknown>) => {
      const wire = { ...frame };
      delete wire.protocol;
      return wire;
    }));
    assert.equal(requests[0].body.tool_choice, "auto");
    assert.deepEqual(openAiTranscript, persistedOpenAiTranscript);
    assert.equal(openAiToolTurn.kind, "tool_calls");
    if (openAiToolTurn.kind === "tool_calls") {
      assert.deepEqual(openAiToolTurn.toolCalls, [{
        providerCallId: "call-current-1",
        name: "lookup_inventory",
        arguments: "{\"limit\":2}",
      }]);
    }
    assert.equal(openAiToolTurn.providerRequestId, "req-openai-1");
    assert.deepEqual(openAiToolTurn.usage, {
      inputTokens: 21,
      outputTokens: 7,
      totalTokens: 28,
      reasoningTokens: 3,
    });
    assert.equal(openAiToolTurn.finishReason, "tool_calls");
    assert.deepEqual(JSON.parse(JSON.stringify(openAiToolTurn.assistantFrame)), {
      protocol: "openai_compatible",
      role: "assistant",
      content: null,
      reasoning_content: "private planning frame",
      tool_calls: [{
        id: "call-current-1",
        type: "function",
        function: { name: "lookup_inventory", arguments: "{\"limit\":2}" },
      }],
    });

    const anthropicTranscript: ModelProviderTranscriptFrame[] = [
      { protocol: "anthropic", role: "user", content: [{ type: "text", text: "check sales" }] },
      {
        protocol: "anthropic",
        role: "assistant",
        content: [{ type: "tool_use", id: "use-prior-1", name: "lookup_sales", input: {} }],
      },
      {
        protocol: "anthropic",
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "use-prior-1", content: "{\"returned\":2}" }],
      },
    ];
    const anthropicToolTurn = await runModelProviderTurn({
      model: runtimeModel("anthropic", encryptedApiKey),
      transcript: anthropicTranscript,
      systemPrompt: "system",
      tools: [{ name: "lookup_sales", input_schema: { type: "object" } }],
    });
    assert.equal(requests.length, 2);
    assert.equal(requests[1].url, "https://api.openai.com/v1/messages");
    assert.equal(new Headers(requests[1].init?.headers).get("x-api-key"), "provider-test-api-key");
    assert.deepEqual(requests[1].body.messages, anthropicTranscript.map((frame) => ({
      role: frame.role,
      content: frame.content,
    })));
    assert.equal(anthropicToolTurn.kind, "tool_calls");
    if (anthropicToolTurn.kind === "tool_calls") {
      assert.deepEqual(anthropicToolTurn.toolCalls, [{
        providerCallId: "use-current-1",
        name: "lookup_sales",
        arguments: { limit: 3 },
      }]);
    }
    assert.equal(anthropicToolTurn.providerRequestId, "msg-anthropic-1");
    assert.deepEqual(anthropicToolTurn.usage, {
      inputTokens: 30,
      outputTokens: 9,
      totalTokens: 39,
      cacheReadInputTokens: 4,
      cacheCreationInputTokens: 5,
    });
    assert.deepEqual(JSON.parse(JSON.stringify(anthropicToolTurn.assistantFrame)), {
      protocol: "anthropic",
      role: "assistant",
      content: [
        { type: "text", text: "I will query." },
        { type: "tool_use", id: "use-current-1", name: "lookup_sales", input: { limit: 3 } },
      ],
    });

    const openAiFinal = await runModelProviderTurn({
      model: runtimeModel("openai_compatible", encryptedApiKey),
      transcript: [{ protocol: "openai_compatible", role: "user", content: "finish" }],
    });
    assert.equal(requests.length, 3);
    assert.equal(openAiFinal.kind, "final");
    if (openAiFinal.kind === "final") assert.equal(openAiFinal.text, "OpenAI final");
    assert.equal(openAiFinal.providerRequestId, "chatcmpl-final");

    const anthropicFinal = await runModelProviderTurn({
      model: runtimeModel("anthropic", encryptedApiKey),
      transcript: [{ protocol: "anthropic", role: "user", content: [{ type: "text", text: "finish" }] }],
    });
    assert.equal(requests.length, 4);
    assert.equal(anthropicFinal.kind, "final");
    if (anthropicFinal.kind === "final") assert.equal(anthropicFinal.text, "Anthropic final");
    assert.equal(anthropicFinal.providerRequestId, "req-anthropic-final");
    assert.equal(responses.length, 0);
  } finally {
    globalThis.fetch = originalFetch;
    if (previousEncryptionKey === undefined) delete environment.AI_SECRET_ENCRYPTION_KEY;
    else environment.AI_SECRET_ENCRYPTION_KEY = previousEncryptionKey;
  }
});

test("single provider turns reject mixed transcripts and cancellation before any HTTP request", async () => {
  let requests = 0;
  globalThis.fetch = (async () => {
    requests += 1;
    throw new Error("must not request");
  }) as typeof fetch;
  try {
    await assert.rejects(
      runModelProviderTurn({
        model: runtimeModel("openai_compatible", ""),
        transcript: [{ protocol: "anthropic", role: "user", content: [{ type: "text", text: "wrong" }] }],
      }),
      (error: unknown) => error instanceof ModelProtocolError && /\u534f\u8bae\u4e0d\u5339\u914d/.test(error.message),
    );
    const controller = new AbortController();
    controller.abort();
    await assert.rejects(
      runModelProviderTurn({
        model: runtimeModel("openai_compatible", ""),
        transcript: [{ protocol: "openai_compatible", role: "user", content: "cancel" }],
        signal: controller.signal,
      }),
      /\u53d6\u6d88/,
    );
    assert.equal(requests, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
