// Development-only entry; production worker never imports this module.
import handler from "vinext/server/app-router-entry";
import { env } from "cloudflare:workers";
import { createPreviewFetch } from "./fetch-policy.mjs";
// HMR re-evaluates this module; retain the original function, never stack wrappers.
const previewGlobals = globalThis as typeof globalThis & {
    __teruisiPreviewFetch?: typeof fetch;
};
const nativeFetch = previewGlobals.__teruisiPreviewFetch ?? globalThis.fetch;
previewGlobals.__teruisiPreviewFetch = nativeFetch;
globalThis.fetch = createPreviewFetch(nativeFetch, () => [env.PREVIEW_READER_URL, env.PREVIEW_WRITER_URL]);
const previewWorker = {
    async fetch(...args: Parameters<typeof handler.fetch>) {
        const [request, bindings, context] = args;
        const url = new URL(request.url);
        if (!["127.0.0.1", "localhost"].includes(url.hostname)) {
            return new Response("Preview is loopback only", { status: 403 });
        }
        if (!["GET", "HEAD"].includes(request.method) || url.pathname.startsWith("/_teruisi/")) {
            return Response.json({ error: "演示预览只开放查询；提交、导入和自动化未启用。" }, { status: 403 });
        }
        const response = await handler.fetch(request, bindings, context);
        const headers = new Headers(response.headers);
        const origin = url.origin;
        headers.set("Content-Security-Policy", `connect-src 'self' ${origin.replace("http:", "ws:")}; frame-src 'none'; form-action 'none'; img-src 'self' data: blob:; object-src 'none'`);
        headers.set("X-Teruisi-Preview", "synthetic-v1");
        return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
    },
};
export default previewWorker;
