import { MAX_AI_REPLY_CHARACTERS, MAX_AI_STREAM_BYTES } from "./model-generation";
export class AiChatStreamError extends Error {
  constructor(message: string, public readonly code = "ai_chat_result_unknown") { super(message); }
}

export async function readAiChatStream<T>(response: Response, onEvent: (event: string, data: Record<string, unknown>) => void, signal?: AbortSignal): Promise<T> {
  if (!response.ok || !response.headers.get("content-type")?.includes("text/event-stream") || !response.body) {
    throw new AiChatStreamError("未能建立流式连接，请核对原请求结果。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = ""; let bytes = 0; let serial = 0; let terminal = false; let result: T | undefined;
  function parse() {
    let boundary: RegExpExecArray | null;
    while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
      const block = buffer.slice(0, boundary.index); buffer = buffer.slice(boundary.index + boundary[0].length);
      const lines = block.split(/\r?\n/);
      const content = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
      if (!content) continue;
      const id = Number(lines.find(line => line.startsWith("id:"))?.slice(3).trim());
      const event = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
      if (terminal || !Number.isSafeInteger(id) || id !== serial + 1 || !event || !["status", "reset", "delta", "tool", "done", "failure"].includes(event)) throw new AiChatStreamError("流式事件顺序无效，请核对原请求结果。");
      serial = id;
      const data: unknown = JSON.parse(content);
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new AiChatStreamError("流式事件格式无效。");
      const value = data as Record<string, unknown>;
      if (event === "failure") throw new AiChatStreamError(typeof value.error === "string" ? value.error : "生成失败，请核对服务端记录。", typeof value.code === "string" ? value.code : undefined);
      if (event === "delta" && (typeof value.content !== "string" || value.content.length > MAX_AI_REPLY_CHARACTERS)) throw new AiChatStreamError("流式正文超出范围。");
      if (event === "done") { terminal = true; result = value as T; }
      onEvent(event, value);
    }
    if (buffer.length > MAX_AI_STREAM_BYTES) throw new AiChatStreamError("流式事件超出传输范围。");
  }
  try {
    while (true) {
      signal?.throwIfAborted();
      const next = await reader.read();
      if (next.done) { buffer += decoder.decode(); parse(); break; }
      bytes += next.value.byteLength;
      if (bytes > MAX_AI_STREAM_BYTES) throw new AiChatStreamError("流式回复超出传输上限。");
      buffer += decoder.decode(next.value, { stream: true }); parse();
    }
    if (!terminal) throw new AiChatStreamError("连接已中断，已收到的内容可能不完整；请核对服务端记录。");
    return result as T;
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
