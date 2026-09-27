import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_IMPORTS_PATH,
  type DjangoInventoryServiceOptions,
} from "@/lib/django/inventory-service";
import {
  parseErpReferenceXlsx,
  type ErpReferenceIssue,
  type InventoryAgeImportRow,
} from "@/lib/imports/erp-reference";
import type { InventoryImportExecution } from "@/lib/inventory/django-import-service";
import { isXlsxSignature } from "@/lib/sales/import-service";

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return toHex(await crypto.subtle.digest("SHA-256", input));
}

function safeFileName(name: string) {
  const baseName = name.split(/[\\/]/).pop() ?? "inventory-age.xlsx";
  return baseName.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}

function isIsoDate(value: string | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function issue(value: ErpReferenceIssue) {
  return {
    ...(Number.isSafeInteger(value.sourceRowNumber) ? { row: value.sourceRowNumber } : {}),
    ...(value.field ? { field: value.field } : {}),
    code: value.code,
    message: value.message,
  };
}

export async function importInventoryAgeToDjango(
  input: {
    principal: AppPrincipal;
    bytes: Uint8Array;
    fileName: string;
    fileSizeBytes: number;
    snapshotDate?: string;
  },
  options: Omit<DjangoInventoryServiceOptions, "config"> = {},
): Promise<InventoryImportExecution> {
  const service = createDjangoInventoryService();
  const rawFileHash = await sha256(input.bytes);
  const fileName = safeFileName(input.fileName);
  const reject = async (result: InventoryImportExecution) => (await service.requestJson<InventoryImportExecution>(
    input.principal,
    {
      method: "POST",
      path: INVENTORY_IMPORTS_PATH,
      service: "writer",
      payload: {
        action: "reject",
        dataset: "age",
        file: { name: fileName, sizeBytes: input.fileSizeBytes, rawFileHash },
        snapshotDate: isIsoDate(input.snapshotDate) ? input.snapshotDate : null,
        message: result.message,
        errors: (result.errors ?? []).slice(0, 200),
        warnings: result.warnings.slice(0, 200),
      },
    },
    options,
  )).data;
  if (!isIsoDate(input.snapshotDate)) {
    return reject({ ok: false, status: "rejected", message: "库龄报表必须提供有效快照日期", warnings: [], errors: [{ code: "INVALID_SNAPSHOT_DATE", message: "快照日期必须为 YYYY-MM-DD" }], errorCount: 1 });
  }
  if (!isXlsxSignature(input.bytes)) {
    return reject({ ok: false, status: "rejected", message: "文件签名不是有效的 .xlsx（ZIP）格式", warnings: [], errors: [{ code: "INVALID_XLSX_SIGNATURE", message: "文件签名无效" }], errorCount: 1 });
  }
  let parsed: ReturnType<typeof parseErpReferenceXlsx>;
  try {
    parsed = parseErpReferenceXlsx("inventory_age", input.bytes);
  } catch {
    return reject({ ok: false, status: "rejected", message: "吉客云 ERP · 库龄分析解析失败，请确认文件格式和模板", warnings: [], errors: [{ code: "XLSX_PARSE_ERROR", message: "库龄 Excel 文件解析失败，请确认文件格式和模板" }], errorCount: 1 });
  }
  if (parsed.errors.length > 0 || parsed.rows.length === 0) {
    const errors = parsed.errors.length ? parsed.errors.slice(0, 200).map(issue) : [{ code: "NO_DATA_ROWS", message: "工作表中没有可导入的数据行" }];
    return reject({ ok: false, status: "rejected", message: "文件校验未通过，未写入任何数据", warnings: parsed.warnings.map(issue), errors, errorCount: parsed.errors.length || 1 });
  }
  const allRows = parsed.rows as InventoryAgeImportRow[];
  const excluded = allRows.filter((row) => row.warehouse.trim() === "刷刷仓").length;
  const rows = allRows.filter((row) => row.warehouse.trim() !== "刷刷仓");
  if (rows.length === 0) {
    return reject({ ok: false, status: "rejected", message: "应用业务排除规则后没有可导入的数据", warnings: parsed.warnings.map(issue), errors: [{ code: "NO_DATA_ROWS_AFTER_FILTER", message: "没有符合当前经营口径的库龄资料" }], errorCount: 1 });
  }
  const keys = rows.map((row) => `${row.warehouse}\u001f${row.productCode}`);
  const duplicates = keys.filter((key, index) => keys.indexOf(key) !== index);
  if (duplicates.length) {
    return reject({ ok: false, status: "rejected", message: "库龄报表包含重复的仓库与货品组合", warnings: parsed.warnings.map(issue), errors: [{ code: "DUPLICATE_INVENTORY_IDENTITY", message: `检测到 ${new Set(duplicates).size} 个重复业务键` }], errorCount: new Set(duplicates).size });
  }
  const warnings = [
    ...parsed.warnings.map(issue),
    ...(excluded ? [{ code: "EXCLUDED_BRUSH_WAREHOUSE", message: `已从库龄分析中排除刷刷仓 ${excluded} 行` }] : []),
  ];
  const sourceRowCount = Number(parsed.totals.sourceRowCount ?? parsed.totals.rowCount ?? allRows.length);
  const result = await service.requestJson<InventoryImportExecution>(input.principal, {
    method: "POST",
    path: INVENTORY_IMPORTS_PATH,
    service: "writer",
    payload: {
      dataset: "age",
      file: { name: fileName, sizeBytes: input.fileSizeBytes, rawFileHash, sheetName: parsed.sheetName },
      snapshotDate: input.snapshotDate,
      sourceRowCount,
      excludedCount: excluded,
      rows,
      warnings,
      totals: { ...parsed.totals, rowCount: rows.length, excludedBrushWarehouseRows: excluded },
    },
  }, options);
  return result.data;
}
