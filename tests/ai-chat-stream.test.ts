import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import AiMarkdown from "../app/ai-markdown";
import { AiChatStreamError, readAiChatStream } from "../lib/ai/chat-stream";
import { requestDjangoAiStream } from "../lib/django/ai-stream";

const encoder = new TextEncoder();
const frame = (id: number, event: string, data: unknown) => `id: ${id}\r\nevent: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
function response(source: string, width = 1) {
  const bytes = encoder.encode(source); let offset = 0;
  return new Response(new ReadableStream({ pull(c) { if (offset >= bytes.length) { c.close(); return; } c.enqueue(bytes.slice(offset, offset + width)); offset += width; } }), { headers: { "content-type": "text/event-stream", "x-ai-revision": "7" } });
}
test("AI chat SSE decodes split Chinese, multiple frames, heartbeats and terminal receipt", async () => {
  const seen: string[] = [];
  const result = await readAiChatStream<{reply: string}>(response(": heartbeat\n\n" + frame(1,"delta",{content:"中文"}) + frame(2,"done",{reply:"中文"})), event => seen.push(event));
  assert.deepEqual(seen,["delta","done"]); assert.equal(result.reply,"中文");
});
test("AI chat rejects incomplete, reordered, post-terminal, malformed and failed streams without replays", async () => {
  for (const source of [frame(1,"delta",{content:"partial"}), frame(2,"done",{}), frame(1,"done",{})+frame(2,"delta",{content:"late"}), frame(1,"delta",{content:8})]) {
    await assert.rejects(readAiChatStream(response(source, 10000), () => {}), AiChatStreamError);
  }
  await assert.rejects(readAiChatStream(response(frame(1,"failure",{error:"已取消",code:"ai_request_cancelled"})), () => {}), e => e instanceof AiChatStreamError && e.code === "ai_request_cancelled");
});
test("Markdown renders actual content safely and does not execute embedded HTML or load remote images", () => {
  const html=renderToStaticMarkup(createElement(AiMarkdown,{content:'## 正文\n\n**重点**\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n<script>window.BAD=1</script>\n\n[x](javascript:alert%281%29)\n\n![图](https://untrusted.example/a.png)'}));
  assert.match(html,/<h2>/); assert.match(html,/<strong>重点/); assert.match(html,/<table\b/);
  assert.doesNotMatch(html,/<script|<img|href="javascript:/); assert.match(html,/图片：图/);
});
const principal = { email:"analyst@example.invalid", displayName:"测试", role:"analyst" as const, scope:null };
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL:"http://127.0.0.1:18111", TERUISI_DJANGO_AI_WRITER_BASE_URL:"http://127.0.0.1:18112", TERUISI_DJANGO_INTERNAL_SECRET:"stream-isolated-13579-abcdefghijklmnopqrstuvwxyz" };
test("long SSE answers pass the former 48000 character limit and preserve completion metadata", async () => {
  const reply = "长回答".repeat(20000);
  const execution = { outputTokens: 30000, stopReason: "length", outputTruncated: true };
  const result = await readAiChatStream<{reply:string;execution:typeof execution}>(response(frame(1,"delta",{content:reply}) + frame(2,"done",{reply,execution}),65536),()=>{});
  assert.equal(result.reply,reply); assert.deepEqual(result.execution,execution);
});
test("thin SSE relay signs original body and streams only from configured writer", async () => {
  let calls=0;
  const result=await requestDjangoAiStream(principal,{message:"test",clientRequestId:"request"},{environment,fetchImpl:async(url,init)=>{
    calls++; assert.equal(new URL(String(url)).port,"18112"); const headers=new Headers(init?.headers);
    assert.equal(headers.get("accept"),"text/event-stream"); assert.ok(headers.get("x-teruisi-signature")); assert.equal(init?.redirect,"manual");
    return response(frame(1,"done",{reply:"ok"}));
  }});
  assert.equal(result.headers.get("x-ai-revision"),"7"); assert.equal((await readAiChatStream<{reply:string}>(result,()=>{})).reply,"ok"); assert.equal(calls,1);
});
test("relay rejects bad endpoint, missing revision, JSON success, oversized body and preserves safe admission errors", async () => {
  let calls=0; const fetchImpl=async()=>{calls++; return response(frame(1,"done",{}));};
  await assert.rejects(requestDjangoAiStream(principal,{}, {environment:{...environment,TERUISI_DJANGO_AI_WRITER_BASE_URL:"http://192.168.1.1"},fetchImpl})); assert.equal(calls,0);
  await assert.rejects(requestDjangoAiStream(principal,{message:"x".repeat(1048577)}, {environment,fetchImpl})); assert.equal(calls,0);
  for (const bad of [new Response("",{status:302,headers:{location:"https://untrusted.example"}}),new Response("",{headers:{"content-type":"text/event-stream"}}),Response.json({reply:"no stream"})]) await assert.rejects(requestDjangoAiStream(principal,{}, {environment,fetchImpl:async()=>bad}));
  await assert.rejects(requestDjangoAiStream(principal,{}, {environment,fetchImpl:async()=>Response.json({error:"未派发",code:"ai_chat_not_dispatched"},{status:409})}), (error:unknown)=>error instanceof Error && error.message === "未派发");
});
test("browser cancel propagates to upstream once and never starts another request", async () => {
  let cancellations=0; let calls=0; let upstreamSignal: AbortSignal | null | undefined;
  const result=await requestDjangoAiStream(principal,{}, {environment,onCancel:()=>cancellations++,fetchImpl:async(_,init)=>{
    calls++; upstreamSignal=init?.signal;
    return new Response(new ReadableStream({pull(c){c.enqueue(encoder.encode(": waiting\n\n"));}}),{headers:{"content-type":"text/event-stream","x-ai-revision":"1"}});
  }});
  await result.body?.cancel(); assert.equal(cancellations,1); assert.equal(calls,1); assert.equal(upstreamSignal?.aborted,true);
});
