import { scenarios, chooseScenario } from "./src/fixtures.mjs";

// Preview fixtures only. No provider calls, credentials, database, or business writes.
export function demoMiddleware(req, res, next) {
  if (req.url !== "/demo-api/chat") return next();
  if (req.method !== "POST") {
    res.writeHead(405, { Allow: "POST" });
    res.end();
    return;
  }
  if (
    req.headers.origin &&
    req.headers.origin !== `http://${req.headers.host}`
  ) {
    res.writeHead(403);
    res.end();
    return;
  }
  let raw = "";
  let tooLarge = false;
  req.on("data", (chunk) => {
    raw += chunk.toString();
    if (Buffer.byteLength(raw) > 16000) {
      tooLarge = true;
      raw = "";
      res.writeHead(413);
      res.end();
      req.destroy();
    }
  });
  req.on("end", () => {
    if (tooLarge) return;
    let body;
    try {
      body = JSON.parse(raw);
    } catch {
      res.writeHead(400);
      res.end();
      return;
    }
    if (
      !body ||
      typeof body.prompt !== "string" ||
      !body.prompt.trim() ||
      body.prompt.length > 4000
    ) {
      res.writeHead(400);
      res.end();
      return;
    }
    const scenario = chooseScenario(body.prompt);
    const fixture = scenarios[scenario];
    res.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store, no-transform",
      "X-Accel-Buffering": "no",
      "X-Demo-Data": "synthetic",
      "X-Content-Type-Options": "nosniff",
    });
    res.flushHeaders();
    let id = 0;
    let cursor = 0;
    let tick = 0;
    const emit = (event, data) =>
      res.write(
        `id: ${++id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
      );
    emit("status", {
      stage: "正在整理演示数据",
      sources: fixture.sources,
      scenario,
    });
    const timer = setInterval(() => {
      tick++;
      if (tick === 5)
        emit("status", {
          stage: "已整理来源，正在生成回答",
          sources: fixture.sources,
          scenario,
        });
      if (tick < 9) return;
      if (body.mode === "disconnect" && cursor > 100) {
        res.destroy();
        return;
      }
      if (body.mode === "error" && cursor > 100) {
        emit("failure", {
          message: "演示生成失败，已保留收到的内容。未自动重发。",
        });
        res.end();
        return;
      }
      const content = fixture.body.slice(cursor, cursor + 9);
      cursor += content.length;
      emit("delta", { content });
      if (cursor >= fixture.body.length) {
        emit("done", {
          scenario,
          metrics: fixture.metrics,
          stage: "回答已完成",
        });
        res.end();
      }
    }, 38);
    res.on("close", () => clearInterval(timer));
  });
}
