import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
  type AppPrincipal,
} from "@/lib/auth/authorization";
import {
  MARKET_COMMANDS_PATH,
  MARKET_IMPORTS_PATH,
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { prepareDjangoMarketImport } from "@/lib/market/django-import-contract";
import {
  recordDjangoMarketImportRejection,
  sha256Hex,
} from "@/lib/market/django-import-rejection";

type JsonRecord = Record<string, unknown>;
type DownloadTask = {
  id: string;
  category: string;
  scope: string;
  rankingDimension: "SKU" | "SPU";
  month: string;
  status: string;
};

function monthEnd(month: string) {
  const [year, value] = month.split("-").map(Number);
  const last = new Date(Date.UTC(year!, value!, 0));
  return last.toISOString().slice(0, 10);
}

async function command(
  principal: AppPrincipal,
  commandPayload: JsonRecord,
  signal?: AbortSignal,
) {
  return requestDjangoMarketService<{ ok: boolean; result: JsonRecord }>(
    principal,
    {
      path: MARKET_COMMANDS_PATH,
      service: "writer",
      payload: {
        contractVersion: "market-command-v1",
        domain: "master",
        command: commandPayload,
      },
    },
    { signal },
  );
}

export async function POST(request: Request) {
  let principal: AppPrincipal | null = null;
  let taskId = "";
  try {
    principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "市场榜单下载执行", "导入");
    const form = await request.formData();
    taskId = String(form.get("taskId") ?? "").trim();
    const jdTaskId = String(form.get("jdTaskId") ?? "").trim().slice(0, 160);
    const file = form.get("file");
    if (!taskId || !(file instanceof File)) {
      throw new Error("taskId 和下载文件必填");
    }
    if (!/\.(xls|xlsx|csv)$/i.test(file.name) || !file.size || file.size > 25 * 1024 * 1024) {
      throw new Error("文件必须是 1 byte 到 25 MB 的 XLS、XLSX 或 CSV");
    }
    const taskResult = await requestDjangoMarketService<DownloadTask>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: { operation: "master", view: "download_task", params: { taskId } },
      },
      { signal: request.signal },
    );
    const task = taskResult.data;
    if (!/^(?:planned|failed|waiting_login)$/.test(task.status)) {
      throw new Error("下载任务已被执行器领取或已经完成");
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    const rawFileHash = await sha256Hex(bytes);
    const startDate = `${task.month}-01`;
    const endDate = monthEnd(task.month);
    let prepared: Awaited<ReturnType<typeof prepareDjangoMarketImport>>;
    try {
      prepared = await prepareDjangoMarketImport({
        bytes,
        fileName: file.name.slice(0, 240),
        fileSizeBytes: file.size,
        sourceType: "market_ranking",
        defaultStartDate: startDate,
        defaultEndDate: endDate,
        defaultCategory: task.category,
        defaultScope: task.scope,
        defaultPriceBandFilter: "全部",
      });
      if (prepared.rows.some((row) => row.category !== task.category
        || row.scope !== task.scope
        || row.rankingDimension !== task.rankingDimension
        || !row.periodStart.startsWith(task.month)
        || !row.periodEnd.startsWith(task.month))) {
        throw new Error("榜单文件与下载任务的类目、范围、维度或月份不一致");
      }
    } catch (error) {
      await recordDjangoMarketImportRejection({
        principal,
        sourceType: "market_ranking",
        fileName: file.name,
        fileSizeBytes: file.size,
        rawFileHash,
        error,
        signal: request.signal,
      }).catch(() => undefined);
      throw error;
    }
    const imported = await requestDjangoMarketService<{
      status: string;
      batch: { id: string; rowCount: number };
      imageCacheJob?: JsonRecord;
    }>(
      principal,
      { path: MARKET_IMPORTS_PATH, service: "writer", payload: prepared },
      { signal: request.signal },
    );
    if (!imported.data.batch?.id || !["imported", "duplicate"].includes(imported.data.status)) {
      throw new Error("市场榜单导入没有返回已完成批次");
    }
    const completed = await command(
      principal,
      {
        action: "complete_download_task",
        taskId,
        jdTaskId,
        fileName: file.name.slice(0, 240),
        rawFileHash: prepared.rawFileHash,
        contentHash: prepared.contentHash,
        rowCount: prepared.rows.length,
        batchId: imported.data.batch.id,
      },
      request.signal,
    );
    return Response.json(
      {
        ok: true,
        result: {
          task: completed.data.result,
          import: imported.data,
          imageCacheJob: imported.data.imageCacheJob ?? null,
        },
      },
      {
        headers: {
          "cache-control": "no-store",
          "x-market-data-revision": completed.revision,
        },
      },
    );
  } catch (error) {
    if (principal && taskId) {
      await command(
        principal,
        {
          action: "record_download_attempt",
          taskId,
          status: "failed",
          errorCode: "market_download_import_failed",
          errorMessage: "榜单文件校验或导入失败",
        },
        request.signal,
      ).catch(() => undefined);
    }
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "榜单文件校验导入失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}
