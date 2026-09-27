import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  MARKET_IMPORTS_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";
import { prepareDjangoMarketImport } from "@/lib/market/django-import-contract";
import {
  recordDjangoMarketImportRejection,
  sha256Hex,
} from "@/lib/market/django-import-rejection";
import { isStrictMarketDate } from "@/lib/market/import-identity";
import { MarketImportRowLimitError } from "@/lib/market/parser";

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const noStoreHeaders = { "cache-control": "no-store" } as const;

function formText(form: FormData, key: string, fallback = "") {
  const value = form.get(key);
  return typeof value === "string" ? value.trim() : fallback;
}

function today() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "市场数据导入", "导入");
    if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("multipart/form-data")) {
      return Response.json(
        { error: "请使用上传表单提交市场数据文件" },
        { status: 415, headers: noStoreHeaders },
      );
    }
    const form = await request.formData().catch(() => null);
    if (!form) {
      return Response.json({ error: "无法读取上传表单" }, { status: 400, headers: noStoreHeaders });
    }
    const file = form.get("file");
    if (!(file instanceof File)) {
      return Response.json(
        { error: "请选择 XLS、XLSX 或 CSV 文件" },
        { status: 400, headers: noStoreHeaders },
      );
    }
    if (!/\.(xls|xlsx|csv)$/i.test(file.name)) {
      return Response.json(
        { error: "仅支持 XLS、XLSX 或 CSV 文件" },
        { status: 400, headers: noStoreHeaders },
      );
    }
    if (!file.size || file.size > MAX_FILE_BYTES) {
      return Response.json(
        { error: "文件须大于 0 且不超过 25MB" },
        { status: 413, headers: noStoreHeaders },
      );
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    const rawFileHash = await sha256Hex(bytes);
    const rawSourceType = formText(form, "sourceType", "market_ranking");
    let payload: Awaited<ReturnType<typeof prepareDjangoMarketImport>>;
    try {
      if (rawSourceType !== "market_ranking" && rawSourceType !== "sku_catalog") {
        throw new PublicApiError(400, "invalid_request", "不支持的数据类型");
      }
      const currentDate = today();
      const periodStart = formText(form, "periodStart", currentDate);
      const periodEnd = formText(form, "periodEnd", currentDate);
      if (!isStrictMarketDate(periodStart) || !isStrictMarketDate(periodEnd) || periodStart > periodEnd) {
        throw new PublicApiError(400, "invalid_request", "导入周期无效");
      }
      payload = await prepareDjangoMarketImport({
        bytes,
        fileName: file.name,
        fileSizeBytes: file.size,
        sourceType: rawSourceType,
        defaultStartDate: periodStart,
        defaultEndDate: periodEnd,
        defaultCategory: formText(form, "category"),
        defaultScope: formText(form, "scope", "全部"),
        defaultPriceBandFilter: formText(form, "priceBandFilter", "全部"),
      });
    } catch (error) {
      await recordDjangoMarketImportRejection({
        principal,
        sourceType: rawSourceType,
        fileName: file.name,
        fileSizeBytes: file.size,
        rawFileHash,
        error,
        signal: request.signal,
      }).catch(() => undefined);
      throw error;
    }
    const result = await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      { path: MARKET_IMPORTS_PATH, service: "writer", payload },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      status: result.status,
      headers: {
        ...noStoreHeaders,
        "x-market-data-revision": result.revision,
        ...(result.replayed ? { "x-teruisi-write-replay": "1" } : {}),
      },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    if (error instanceof MarketImportRowLimitError) {
      return Response.json({ error: error.message }, { status: 413, headers: noStoreHeaders });
    }
    return safeApiErrorResponse(error, "市场数据导入失败", { headers: noStoreHeaders });
  }
}
