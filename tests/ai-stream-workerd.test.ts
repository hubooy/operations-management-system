import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { Miniflare } from "miniflare";

test("workerd relays signed SSE and rejects redirects without following them", async () => {
  let redirected = false;
  let requests = 0;
  const server = createServer(async (request, response) => {
    requests++;
    assert.match(String(request.headers["x-teruisi-signature"]), /^v1=/);
    let body = "";
    for await (const chunk of request) body += chunk;
    if (JSON.parse(body).redirect) {
      response.writeHead(302, { location: "/forbidden" });
      response.end();
    } else if (request.url === "/forbidden") {
      redirected = true;
      response.end();
    } else {
      response.writeHead(200, { "content-type": "text/event-stream", "x-ai-revision": "7" });
      response.end('id: 1\nevent: done\ndata: {"reply":"中文"}\n\n');
    }
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  let runtime: Miniflare | undefined;
  try {
    const bundle = await build({
      stdin: { contents: `
        import { requestDjangoAiStream } from "./lib/django/ai-stream";
        export default { async fetch(request) {
          try {
            return await requestDjangoAiStream(
              { email: "test@example.invalid", displayName: "test", role: "analyst", scope: null },
              { redirect: new URL(request.url).pathname === "/redirect" },
              { environment: {
                TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:1",
                TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:${port}",
                TERUISI_DJANGO_INTERNAL_SECRET: "isolated-workerd-sse-test-secret-abcdefghijklmnopqrstuvwxyz"
              } }
            );
          } catch (error) { return Response.json({ code: error.code }, { status: error.status ?? 500 }); }
        } };`, resolveDir: process.cwd(), loader: "ts" },
      bundle: true, write: false, format: "esm", platform: "browser", external: ["cloudflare:workers"],
    });
    runtime = new Miniflare({ modules: true, script: bundle.outputFiles[0].text, compatibilityDate: "2026-04-01" });
    const stream = await runtime.dispatchFetch("http://test.invalid/");
    assert.equal(stream.status, 200);
    assert.match(stream.headers.get("content-type") ?? "", /text\/event-stream/);
    assert.match(await stream.text(), /event: done\ndata: {"reply":"中文"}/);
    const rejected = await runtime.dispatchFetch("http://test.invalid/redirect");
    assert.equal(rejected.status, 503);
    assert.equal((await rejected.json() as { code: string }).code, "service_unavailable");
    assert.equal(requests, 2);
    assert.equal(redirected, false);
  } finally {
    await runtime?.dispose();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});
