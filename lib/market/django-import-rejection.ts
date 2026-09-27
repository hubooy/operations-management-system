import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  MARKET_COMMANDS_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes).buffer);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

export async function recordDjangoMarketImportRejection(input: {
  principal: AppPrincipal;
  sourceType: string;
  fileName: string;
  fileSizeBytes: number;
  rawFileHash: string;
  error: unknown;
  signal?: AbortSignal;
}) {
  const message = (input.error instanceof Error ? input.error.message : "市场文件预校验失败")
    .slice(0, 500);
  return requestDjangoMarketService(
    input.principal,
    {
      path: MARKET_COMMANDS_PATH,
      service: "writer",
      payload: {
        contractVersion: "market-command-v1",
        domain: "master",
        command: {
          action: "record_import_rejection",
          sourceType: input.sourceType.slice(0, 64) || "unknown",
          fileName: input.fileName.slice(0, 1_000) || "unknown",
          fileSizeBytes: input.fileSizeBytes,
          rawFileHash: input.rawFileHash,
          errorCode: "MARKET_EDGE_PREVALIDATION_FAILED",
          errorMessage: message,
        },
      },
    },
    { signal: input.signal },
  );
}
