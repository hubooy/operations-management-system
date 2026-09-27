import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { CustomerServiceImportError, parseCustomerServiceImport } from "@/lib/customer-service/import-service";
import { planCustomerServiceImportPayloads, recordRejectedCustomerServiceImport, saveCustomerServiceImport } from "@/lib/customer-service/database";
import {
  CUSTOMER_SERVICE_UPLOAD_CHUNK_BYTES,
  assembleCustomerServiceUpload,
  beginCustomerServiceUpload,
  claimCustomerServiceUpload,
  finishCustomerServiceUpload,
  receiveCustomerServiceUploadChunk,
  releaseCustomerServiceUpload,
} from "@/lib/customer-service/chunked-upload";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

const MAX_FILE_BYTES = 25 * 1024 * 1024;

function reject(status: number, message: string) {
  return Response.json({ ok: false, message }, { status, headers: { "cache-control": "no-store" } });
}

async function digest(bytes: Uint8Array) {
  const copy = new Uint8Array(bytes);
  const hash = await crypto.subtle.digest("SHA-256", copy.buffer as ArrayBuffer);
  return [...new Uint8Array(hash)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "客服数据", "导入");
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body) return reject(400, "Invalid upload request");

    if (body.action === "init") {
      const kind = body.kind === "session" || body.kind === "chat" ? body.kind : null;
      const fileName = typeof body.fileName === "string" ? body.fileName : "";
      const fileSizeBytes = Number(body.fileSizeBytes);
      const chunkCount = Number(body.chunkCount);
      const fingerprint = typeof body.fingerprint === "string" ? body.fingerprint : "";
      if (!kind || (kind === "session" ? !/\.xlsx$/i.test(fileName) : !/\.(log|txt)$/i.test(fileName))
        || fileSizeBytes <= 0 || fileSizeBytes > MAX_FILE_BYTES) {
        return reject(422, "Unsupported customer-service source file");
      }
      const upload = await beginCustomerServiceUpload(principal, {
        kind,
        fileName,
        fileSizeBytes,
        chunkCount,
        fingerprint,
      });
      return Response.json({ ok: true, upload, limits: { chunkSizeBytes: CUSTOMER_SERVICE_UPLOAD_CHUNK_BYTES, maxFileSizeBytes: MAX_FILE_BYTES } }, { headers: { "cache-control": "no-store" } });
    }

    if (body.action === "complete") {
      const sessionUploadId = typeof body.sessionUploadId === "string" ? body.sessionUploadId : "";
      const chatUploadId = typeof body.chatUploadId === "string" ? body.chatUploadId : "";
      const sessionFileName = typeof body.sessionFileName === "string" ? body.sessionFileName : "";
      const chatFileName = typeof body.chatFileName === "string" ? body.chatFileName : "";
      const shopName = typeof body.shopName === "string" ? body.shopName.trim() : "";
      if (!sessionUploadId || !chatUploadId || !shopName || shopName.length > 100 || !/\.xlsx$/i.test(sessionFileName) || !/\.(log|txt)$/i.test(chatFileName)) return reject(400, "Missing shop or paired upload files");
      const pairKey = await digest(new TextEncoder().encode(`${sessionUploadId}:${chatUploadId}`));
      const sessionClaim = await claimCustomerServiceUpload(principal, sessionUploadId);
      if (sessionClaim.upload.kind !== "session") {
        if (sessionClaim.kind === "claimed") await releaseCustomerServiceUpload(principal, sessionUploadId, sessionClaim.ownerToken);
        return reject(409, "Session upload identity does not match the paired import request");
      }
      let chatClaim;
      try { chatClaim = await claimCustomerServiceUpload(principal, chatUploadId); }
      catch (error) {
        if (sessionClaim.kind === "claimed") await releaseCustomerServiceUpload(principal, sessionUploadId, sessionClaim.ownerToken);
        throw error;
      }
      if (chatClaim.upload.kind !== "chat") {
        if (sessionClaim.kind === "claimed") await releaseCustomerServiceUpload(principal, sessionUploadId, sessionClaim.ownerToken);
        if (chatClaim.kind === "claimed") await releaseCustomerServiceUpload(principal, chatUploadId, chatClaim.ownerToken);
        return reject(409, "Chat upload identity does not match the paired import request");
      }
      if (sessionClaim.kind === "completed" || chatClaim.kind === "completed") {
        if (sessionClaim.kind === "claimed") await releaseCustomerServiceUpload(principal, sessionUploadId, sessionClaim.ownerToken);
        if (chatClaim.kind === "claimed") await releaseCustomerServiceUpload(principal, chatUploadId, chatClaim.ownerToken);
        if (sessionClaim.kind !== "completed" || chatClaim.kind !== "completed") {
          return reject(409, "Paired upload sessions are not from the same completed import; upload both files again");
        }
        const sessionResult = sessionClaim.result as { ok?: boolean; status?: string; requestShopName?: string; pairKey?: string };
        const chatResult = chatClaim.result as { ok?: boolean; status?: string; requestShopName?: string; pairKey?: string };
        if (sessionResult.requestShopName !== shopName || sessionResult.pairKey !== pairKey
          || chatResult.pairKey !== pairKey || JSON.stringify(sessionResult) !== JSON.stringify(chatResult)) {
          return reject(409, "Completed paired upload result does not match this shop or file pair");
        }
        return Response.json(sessionResult, { status: sessionResult.ok ? (sessionResult.status === "imported" ? 201 : 200) : 422, headers: { "cache-control": "no-store" } });
      }
      try {
        if (sessionClaim.kind !== "claimed" || chatClaim.kind !== "claimed") return reject(409, "Paired upload sessions are not both ready for processing");
        const [sessionBytes, chatBytes] = await Promise.all([assembleCustomerServiceUpload(principal, sessionClaim), assembleCustomerServiceUpload(principal, chatClaim)]);
        const sessionHash = await digest(sessionBytes); const chatHash = await digest(chatBytes);
        const requestedFileHash = await digest(new TextEncoder().encode(`${shopName}:${sessionHash}:${chatHash}`));
        let parsed: ReturnType<typeof parseCustomerServiceImport>;
        try {
          parsed = parseCustomerServiceImport(sessionBytes, new TextDecoder("utf-8", { fatal: true }).decode(chatBytes));
          if (parsed.conversations.length === 0) throw new CustomerServiceImportError("Customer-service import contains no conversations to save");
        } catch (error) {
          const message = error instanceof CustomerServiceImportError ? error.message : "Customer-service files could not be parsed";
          await recordRejectedCustomerServiceImport(principal, {
            rawFileHash: requestedFileHash,
            scopeHint: { shopName, pairKey },
            errorCode: "CUSTOMER_SERVICE_PARSE_REJECTED",
            issues: [{ code: "CUSTOMER_SERVICE_PARSE_REJECTED", message }],
            fileName: `${sessionFileName} + ${chatFileName}`,
            fileSizeBytes: sessionBytes.byteLength + chatBytes.byteLength,
          });
          if (error instanceof CustomerServiceImportError) throw new PublicApiError(422, "invalid_request", message);
          throw error;
        }
        const resolvedShopName = parsed.conversations.some((item) => item.agent.startsWith("志高厨电")) ? "志高厨电" : shopName;
        const fileHash = await digest(new TextEncoder().encode(`${resolvedShopName}:${await digest(sessionBytes)}:${await digest(chatBytes)}`));
        try {
          planCustomerServiceImportPayloads(resolvedShopName, parsed.conversations);
        } catch (error) {
          if (!(error instanceof PublicApiError) || error.status !== 422) throw error;
          await recordRejectedCustomerServiceImport(principal, {
            rawFileHash: fileHash,
            scopeHint: { shopName: resolvedShopName, pairKey },
            errorCode: "CUSTOMER_SERVICE_PUBLISH_BUDGET_REJECTED",
            issues: [{ code: "CUSTOMER_SERVICE_PUBLISH_BUDGET_REJECTED", message: error.message }],
            fileName: `${sessionFileName} + ${chatFileName}`.slice(0, 500),
            fileSizeBytes: sessionBytes.byteLength + chatBytes.byteLength,
          });
          throw error;
        }
        const saved = await saveCustomerServiceImport({ shopName: resolvedShopName, sessionFileName, chatFileName, fileHash, fileSizeBytes: sessionBytes.byteLength + chatBytes.byteLength, parsed }, principal);
        const result = { ok: true, status: saved.status, requestShopName: shopName, pairKey, batch: saved.batch, summary: parsed.summary, ...saved.warningSummary, message: saved.status === "duplicate" ? "All normalized customer-service data matches the current facts; no rows were rewritten" : `Imported ${parsed.conversations.length} customer-service conversations` };
        await Promise.all([finishCustomerServiceUpload(principal, sessionUploadId, sessionClaim.ownerToken, result), finishCustomerServiceUpload(principal, chatUploadId, chatClaim.ownerToken, result)]);
        return Response.json(result, { status: saved.status === "imported" ? 201 : 200, headers: { "cache-control": "no-store" } });
      } catch (error) {
        await Promise.all([
          sessionClaim.kind === "claimed" ? releaseCustomerServiceUpload(principal, sessionUploadId, sessionClaim.ownerToken) : Promise.resolve(),
          chatClaim.kind === "claimed" ? releaseCustomerServiceUpload(principal, chatUploadId, chatClaim.ownerToken) : Promise.resolve(),
        ]);
        throw error;
      }
    }
    return reject(400, "Unknown upload action");
  } catch (error) {
    const auth = authorizationErrorResponse(error); if (auth) return auth;
    return safeApiErrorResponse(error, "Customer-service upload failed.", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}

export async function PUT(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "客服数据", "导入");
    const uploadId = request.headers.get("x-upload-id") ?? "";
    const chunkIndex = Number(request.headers.get("x-chunk-index"));
    const bytes = new Uint8Array(await request.arrayBuffer());
    if (!uploadId || !Number.isSafeInteger(chunkIndex) || bytes.byteLength === 0 || bytes.byteLength > CUSTOMER_SERVICE_UPLOAD_CHUNK_BYTES) return reject(400, "Invalid upload chunk");
    const upload = await receiveCustomerServiceUploadChunk(principal, { uploadId, chunkIndex, bytes });
    return Response.json({ ok: true, upload }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error); if (auth) return auth;
    return safeApiErrorResponse(error, "Customer-service chunk upload failed.", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
