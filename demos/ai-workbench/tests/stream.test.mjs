import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { consumeStream } from "../src/stream.mjs";
import { demoMiddleware } from "../server.mjs";
import { visibleMarkdown } from "../src/stream-markdown.mjs";

const encoder = new TextEncoder();
test("incomplete table syntax is buffered and complete rows remain visible", () => {
  assert.equal(
    visibleMarkdown("正文\n\n| 商品 | 数量 |\n| --", true),
    "正文\n",
  );
  assert.equal(
    visibleMarkdown("| 商品 | 数量 |\n| --- | --- |\n| 未完成", true),
    "| 商品 | 数量 |\n| --- | --- |",
  );
});
test("unfinished emphasis is formatted; completed source is unchanged", () => {
  assert.equal(visibleMarkdown("一个 **重要结论", true), "一个 **重要结论**");
  assert.equal(visibleMarkdown("##", true), "");
  assert.equal(visibleMarkdown("**部分", false), "**部分");
});
function stream(text, width = 1) {
  const bytes = encoder.encode(text);
  let index = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (index >= bytes.length) {
          controller.close();
          return;
        }
        controller.enqueue(bytes.slice(index, index + width));
        index += width;
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

test("SSE preserves Chinese UTF-8 split into individual bytes and CRLF boundaries", async () => {
  const events = [];
  await consumeStream(
    stream(
      'event: delta\r\ndata: {"content":"中文回答"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n',
    ),
    (event, data) => events.push([event, data]),
  );
  assert.deepEqual(events, [
    ["delta", { content: "中文回答" }],
    ["done", {}],
  ]);
});
test("multiple events in one chunk, comments and multiline data", async () => {
  const events = [];
  await consumeStream(
    stream(
      ': keepalive\n\nevent: delta\ndata: {"content":\ndata: "hello"}\n\nevent: done\ndata: {}\n\n',
      500,
    ),
    (event, data) => events.push([event, data]),
  );
  assert.equal(events[0][1].content, "hello");
  assert.equal(events.length, 2);
});
test("EOF without done preserves received content but rejects completion", async () => {
  let text = "";
  await assert.rejects(
    consumeStream(
      stream('event: delta\ndata: {"content":"部分"}\n\n'),
      (_, data) => {
        text += data.content;
      },
    ),
    /连接已中断/,
  );
  assert.equal(text, "部分");
});
test("explicit upstream failure, invalid JSON and invalid response fail visibly", async () => {
  await assert.rejects(
    consumeStream(
      stream('event: failure\ndata: {"message":"演示失败"}\n\n'),
      () => {},
    ),
    /演示失败/,
  );
  await assert.rejects(
    consumeStream(stream("event: delta\ndata: invalid\n\n"), () => {}),
    SyntaxError,
  );
  await assert.rejects(
    consumeStream(new Response("{}"), () => {}),
    /未能建立/,
  );
});
test("aborted request emits no events", async () => {
  const abort = new AbortController();
  abort.abort();
  let calls = 0;
  await assert.rejects(
    consumeStream(
      stream("event: done\ndata: {}\n\n"),
      () => calls++,
      abort.signal,
    ),
    { name: "AbortError" },
  );
  assert.equal(calls, 0);
});
test("Markdown renders tables, lists, strong text and code without executable HTML or links", () => {
  const source =
    "## 标题\n\n**重点**\n\n| 商品 | 数量 |\n| --- | ---: |\n| 示例 | 2 |\n\n- 清单\n\n```js\nconst x = 1;\n```\n\n<script>alert(1)</script>\n\n[unsafe](javascript:alert%281%29)";
  const html = renderToStaticMarkup(
    React.createElement(
      Markdown,
      { remarkPlugins: [remarkGfm], skipHtml: true },
      source,
    ),
  );
  for (const tag of ["<h2>", "<strong>", "<table>", "<ul>", "<pre>"])
    assert.ok(html.includes(tag), tag);
  assert.ok(!html.includes("<script"));
  assert.ok(!html.includes('href="javascript:'));
});
test("real HTTP endpoint streams before completion and isolates failures", async (t) => {
  const server = createServer((req, res) =>
    demoMiddleware(req, res, () => {
      res.writeHead(404);
      res.end();
    }),
  );
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  const url = `http://127.0.0.1:${server.address().port}/demo-api/chat`;
  const post = (body) =>
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  await t.test(
    "normal stream has status and many deltas, then a single done",
    async () => {
      const events = [];
      const started = Date.now();
      let firstAt = 0;
      const response = await post({ prompt: "展示格式" });
      assert.equal(response.headers.get("x-demo-data"), "synthetic");
      await consumeStream(response, (event, data) => {
        if (!firstAt) firstAt = Date.now();
        events.push([event, data]);
      });
      assert.ok(firstAt - started < 2000);
      assert.ok(Date.now() - firstAt > 500);
      assert.equal(events[0][0], "status");
      assert.ok(events.filter((e) => e[0] === "delta").length > 10);
      assert.equal(events.filter((e) => e[0] === "done").length, 1);
      assert.equal(events.at(-1)[0], "done");
    },
  );
  await t.test(
    "failure does not report done or lose delivered deltas",
    async () => {
      let deltas = 0;
      let done = false;
      await assert.rejects(
        consumeStream(
          await post({ prompt: "补货", mode: "error" }),
          (event) => {
            if (event === "delta") deltas++;
            if (event === "done") done = true;
          },
        ),
        /演示生成失败/,
      );
      assert.ok(deltas > 0);
      assert.equal(done, false);
    },
  );
  await t.test("disconnect rejects completion", async () => {
    let done = false;
    await assert.rejects(
      consumeStream(
        await post({ prompt: "库存", mode: "disconnect" }),
        (event) => {
          if (event === "done") done = true;
        },
      ),
    );
    assert.equal(done, false);
  });
  await t.test(
    "invalid input, methods, cross-origin and other routes are rejected",
    async () => {
      assert.equal((await post({ prompt: "" })).status, 400);
      assert.equal((await post({ prompt: "x".repeat(4001) })).status, 400);
      assert.equal((await fetch(url)).status, 405);
      assert.equal(
        (
          await fetch(url, {
            method: "POST",
            headers: { Origin: "https://untrusted.example" },
          })
        ).status,
        403,
      );
      assert.equal((await fetch(url + "/other")).status, 404);
    },
  );
});
