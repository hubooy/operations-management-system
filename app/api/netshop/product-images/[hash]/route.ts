import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import {
  createDjangoNetshopService,
} from "@/lib/django/netshop-service";
import {
  readNetshopProductImageObject,
  type StoredNetshopProductImage,
} from "@/lib/netshop/product-image-assets";

type RouteContext = { params: Promise<{ hash: string }> };

export async function GET(request: Request, context: RouteContext) {
  try {
    const principal = await requireAppPrincipal();
    netshopPlatformsForPrincipal(principal, ["天猫"]);
    const { hash } = await context.params;
    if (!/^[a-f0-9]{64}$/.test(hash)) return new Response("Not found", { status: 404 });
    const result = await createDjangoNetshopService().request<{ item?: StoredNetshopProductImage | null }>(
      principal,
      {
        method: "GET",
        path: `/api/netshop/product-images/${hash}/metadata`,
        service: "reader",
        acceptedErrorStatuses: [404],
      },
      { signal: request.signal },
    );
    const metadata = result.status === 404 ? null : result.data.item;
    if (!metadata) return new Response("Not found", { status: 404 });
    const stored = await readNetshopProductImageObject(metadata);
    if (!stored) return new Response("Not found", { status: 404 });
    return new Response(stored.object.body, { headers: {
      "content-type": stored.mimeType,
      "content-length": String(stored.sizeBytes),
      "cache-control": "private, max-age=31536000, immutable",
      "x-content-type-options": "nosniff",
      etag: stored.object.httpEtag,
    } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return new Response("Image unavailable", { status: 500 });
  }
}
