import { probeDjangoBackendReadiness } from "../lib/django/backend-readiness";
import { wakeAiQueue } from "../lib/django/ai-service";
/** Cloudflare Worker entry point for the vinext-starter template. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import { runScheduledDjangoMarketAnnotation } from "../lib/market/django-annotation-scheduled";
import { runDjangoMarketImageCacheBatch } from "../lib/market/django-image-cache-runner";
import { runDjangoMarketNetshopProjectionSync } from "../lib/market/django-netshop-projection-runner";
import { enforceDynamicCachePolicy } from "./cache-policy";

interface Env {
  ASSETS: Fetcher;
  [key: string]: unknown;
  SALES_IMPORT_FILES?: R2Bucket;
  TERUISI_LOCAL_DIRECT_ACCESS?: string;
  TERUISI_RUNTIME_ENV?: string;
  IMAGES: {
    info(stream: ReadableStream): Promise<{ width?: number; height?: number }>;
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number; anim?: boolean }): Promise<{ response(): Response }>;
      };
    };
  };
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

interface ScheduledController {
  scheduledTime: number;
  cron: string;
}

const localScheduledPath = "/_teruisi/local/market-annotation-scheduled";
const localLivenessPath = "/_teruisi/local/health/live";
const localReadinessPath = "/_teruisi/local/health/ready";

type ScheduledMarketTaskResult =
  | { ok: true; result: unknown }
  | { ok: false; error: string };

async function runScheduledMarketTask(
  label: string,
  task: () => Promise<unknown>,
): Promise<ScheduledMarketTaskResult> {
  try {
    return { ok: true, result: await task() };
  } catch (error) {
    const message = error instanceof Error ? error.message : `${label} failed`;
    console.error(`${label}: ${message.slice(0, 300)}`);
    return { ok: false, error: label };
  }
}

async function runScheduledMarketMaintenance(
  input: { marketImageBucket?: R2Bucket } = {},
) {
  // 文件构建独立推进；等待大文件不得阻塞取数、Agent 或市场维护。
  const aiFilesPending = runScheduledMarketTask(
    "AI business file runner failed",
    () => wakeAiQueue("files"),
  );
  // 证据按时间与页数双重限制采集，不依赖浏览器页面存活。
  const aiEvidence = await runScheduledMarketTask(
    "AI evidence collection runner failed",
    () => wakeAiQueue("evidence"),
  );
  // 其他队列先于可能耗时更长的图片任务推进，避免队列饥饿。
  const aiWorkflow = await runScheduledMarketTask(
    "AI workflow scheduled runner failed",
    () => wakeAiQueue("workflow"),
  );
  // 每个 Agent 微步仍只允许一次外部调用；Django 为新版经营报告最多并行三个独立微步，普通任务保持串行。
  const aiAgent = await runScheduledMarketTask(
    "AI Agent scheduled runner failed",
    () => wakeAiQueue("agent"),
  );
  const netshopProjection = await runScheduledMarketTask(
    "market netshop projection scheduled runner failed",
    () => runDjangoMarketNetshopProjectionSync(),
  );
  // 图片缓存每次只处理一个有界批次；任一 runner 失败都不能阻塞其余独立队列。
  const imageCache = await runScheduledMarketTask(
    "market image cache scheduled runner failed",
    () => runDjangoMarketImageCacheBatch({ bucket: input.marketImageBucket }),
  );
  const aiSpace = await runScheduledMarketTask(
    "AI space scheduled runner failed",
    () => wakeAiQueue("space"),
  );
  const annotations = await runScheduledMarketTask(
    "market annotation scheduled runner failed",
    () => runScheduledDjangoMarketAnnotation(),
  );
  return { aiFiles: await aiFilesPending, aiEvidence, aiWorkflow, aiAgent, netshopProjection, imageCache, annotations, aiSpace };
}

function allowsLoopbackDevelopmentRequest(request: Request, env: Env) {
  const hostname = new URL(request.url).hostname.toLowerCase();
  return (hostname === "127.0.0.1" || hostname === "localhost" || hostname === "[::1]" || hostname === "::1")
    && env.TERUISI_RUNTIME_ENV === "development";
}

function localDirectAccessRequested(env: Env) {
  return env.TERUISI_LOCAL_DIRECT_ACCESS?.trim().toLowerCase() === "true"
    && env.TERUISI_RUNTIME_ENV?.trim().toLowerCase() === "development";
}

function allowsLocalScheduledRequest(request: Request, env: Env) {
  return allowsLoopbackDevelopmentRequest(request, env)
    && env.TERUISI_LOCAL_DIRECT_ACCESS === "true"
    && request.headers.get("x-teruisi-local-scheduled") === "1";
}

function allowsLocalHealthRequest(request: Request, env: Env) {
  return allowsLoopbackDevelopmentRequest(request, env)
    && request.headers.get("x-teruisi-local-health") === "1";
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // Local anonymous admin is useful for the controlled desktop launcher, but
    // must never be reachable through a LAN address, hostile Host, or DNS rebinding.
    if (localDirectAccessRequested(env) && !allowsLoopbackDevelopmentRequest(request, env)) {
      return new Response(null, { status: 404, headers: { "cache-control": "no-store" } });
    }

    if (url.pathname === localLivenessPath) {
      if (request.method !== "GET") return new Response(null, { status: 405, headers: { allow: "GET" } });
      if (!allowsLocalHealthRequest(request, env)) return new Response(null, { status: 404 });
      return Response.json(
        { ok: true, status: "live" },
        { headers: { "cache-control": "no-store" } },
      );
    }

    if (url.pathname === localReadinessPath) {
      if (request.method !== "GET") return new Response(null, { status: 405, headers: { allow: "GET" } });
      if (!allowsLocalHealthRequest(request, env)) return new Response(null, { status: 404 });
      const result = await probeDjangoBackendReadiness(env, { signal: request.signal });
      return Response.json(
        result.ok ? { ...result, status: "ready" }
          : { ...result, status: "degraded", code: "django_unavailable" },
        { status: result.ok ? 200 : 503, headers: { "cache-control": "no-store" } },
      );
    }

    if (url.pathname === localScheduledPath) {
      if (request.method !== "POST") return new Response(null, { status: 405, headers: { allow: "POST" } });
      if (!allowsLocalScheduledRequest(request, env)) return new Response(null, { status: 404 });
      try {
        // 本地触发器不会重叠执行，因此限制标注时间片，让图片缓存也能按分钟持续推进。
        const result = await runScheduledMarketMaintenance({
          marketImageBucket: env.SALES_IMPORT_FILES,
        });
        return Response.json({ ok: true, result }, { headers: { "cache-control": "no-store" } });
      } catch (error) {
        const message = error instanceof Error ? error.message : "market maintenance local scheduler failed";
        console.error(message.slice(0, 300));
        return Response.json({ ok: false, error: "本地市场后台调度失败" }, { status: 500, headers: { "cache-control": "no-store" } });
      }
    }

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }

    const response = await handler.fetch(request, env, ctx);
    return enforceDynamicCachePolicy(request, response);
  },
  async scheduled(_controller: ScheduledController, env: Env, _ctx: ExecutionContext) {
    void _ctx;
    await runScheduledMarketMaintenance({ marketImageBucket: env.SALES_IMPORT_FILES });
  },
};

export default worker;
