import { env } from "cloudflare:workers";

import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";

type RouteContext = { params: Promise<{ hash: string }> };

export async function GET(request: Request, context: RouteContext) {
  try {
    const principal = await requireAppPrincipal();
    requireUnrestrictedDataScope(principal, "市场商品图片");
    const { hash } = await context.params;
    const contentHash = hash.toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(contentHash)) {
      return new Response("Not found", { status: 404 });
    }
    const metadata = await requestDjangoMarketService<{
      contentSha256: string;
      objectKey: string;
      mimeType: string;
      sizeBytes: number;
    }>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: { operation: "image_metadata", contentHash },
      },
      { signal: request.signal },
    );
    if (!env.SALES_IMPORT_FILES) return new Response("Image unavailable", { status: 503 });
    const object = await env.SALES_IMPORT_FILES.get(metadata.data.objectKey);
    if (!object
      || metadata.data.contentSha256 !== contentHash
      || !["image/jpeg", "image/png", "image/webp"].includes(metadata.data.mimeType)
      || !Number.isSafeInteger(metadata.data.sizeBytes)
      || metadata.data.sizeBytes <= 0
      || object.size !== metadata.data.sizeBytes) {
      return new Response("Image unavailable", { status: 503 });
    }
    return new Response(object.body, {
      headers: {
        "content-type": metadata.data.mimeType,
        "content-length": String(metadata.data.sizeBytes),
        "cache-control": "private, max-age=31536000, immutable",
        "x-content-type-options": "nosniff",
        etag: object.httpEtag,
      },
    });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return new Response("Image unavailable", { status: 500 });
  }
}
