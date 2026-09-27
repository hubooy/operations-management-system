// Handles SSE event boundaries independently of TCP chunks and UTF-8 boundaries.
export async function consumeStream(response, onEvent, signal) {
  if (
    !response.ok ||
    !response.headers.get("content-type")?.includes("text/event-stream") ||
    !response.body
  ) {
    throw new Error("未能建立流式连接，请重新发起提问。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let complete = false;
  const consume = () => {
    let match;
    while ((match = /\r?\n\r?\n/.exec(buffer))) {
      const block = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      let event = "message";
      const data = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (!data.length) continue;
      const payload = JSON.parse(data.join("\n"));
      if (event === "failure")
        throw new Error(payload.message || "生成失败，未自动重发。");
      onEvent(event, payload);
      if (event === "done") complete = true;
    }
    if (buffer.length > 100000) throw new Error("流式事件过大，连接已停止。");
  };
  try {
    while (!complete) {
      signal?.throwIfAborted();
      const { value, done } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        consume();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      consume();
    }
    if (!complete) throw new Error("连接已中断，已保留部分回答。未自动重发。");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
