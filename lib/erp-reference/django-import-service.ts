import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoErpReferenceService,
  ERP_REFERENCE_IMPORTS_PATH,
  type DjangoErpReferenceOptions,
} from "@/lib/django/erp-reference-service";
import {
  ERP_REFERENCE_SOURCE_LABELS,
  parseErpReferenceXlsx,
  type ComboItemImportRow,
  type ErpReferenceIssue,
  type ProductMasterRow,
} from "@/lib/imports/erp-reference";
import { isXlsxSignature } from "@/lib/sales/import-service";

export type ErpReferenceDjangoSource = "products" | "combos";

export type ErpReferenceImportExecution = {
  ok: boolean;
  status: "imported" | "duplicate" | "rejected";
  message: string;
  batch?: Record<string, unknown> | null;
  warnings: Array<Record<string, unknown>>;
  errors?: Array<Record<string, unknown>>;
  errorCount?: number;
  verification?: Record<string, unknown>;
};

const CONTRACT_VERSION = "erp-reference-normalized-v1";

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return toHex(await crypto.subtle.digest("SHA-256", input));
}

function safeFileName(name: string) {
  const baseName = name.split(/[\\/]/).pop() ?? "erp-reference.xlsx";
  return baseName.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}

function issue(value: ErpReferenceIssue) {
  return {
    ...(Number.isSafeInteger(value.sourceRowNumber) ? { row: value.sourceRowNumber } : {}),
    ...(value.field ? { field: value.field } : {}),
    code: value.code,
    message: value.message,
  };
}

export async function importErpReferenceToDjango(
  input: {
    principal: AppPrincipal;
    source: ErpReferenceDjangoSource;
    bytes: Uint8Array;
    fileName: string;
    fileSizeBytes: number;
  },
  options: Omit<DjangoErpReferenceOptions, "config"> = {},
): Promise<ErpReferenceImportExecution> {
  const service = createDjangoErpReferenceService();
  const rawFileHash = await sha256(input.bytes);
  const fileName = safeFileName(input.fileName);
  const reject = async (result: ErpReferenceImportExecution) => (await service.requestJson<ErpReferenceImportExecution>(
    input.principal,
    {
      method: "POST",
      path: ERP_REFERENCE_IMPORTS_PATH,
      service: "writer",
      payload: {
        kind: "rejection",
        version: CONTRACT_VERSION,
        source: input.source,
        fileName,
        fileSizeBytes: input.fileSizeBytes,
        rawFileHash,
        message: result.message,
        warnings: result.warnings.slice(0, 200),
        errors: (result.errors ?? []).slice(0, 200),
      },
    },
    options,
  )).data;
  if (!isXlsxSignature(input.bytes)) {
    return reject({
      ok: false, status: "rejected", message: "文件签名不是有效的 .xlsx（ZIP）格式",
      warnings: [], errors: [{ code: "INVALID_XLSX_SIGNATURE", message: "文件签名无效" }], errorCount: 1,
    });
  }
  let parsed: ReturnType<typeof parseErpReferenceXlsx>;
  try {
    parsed = parseErpReferenceXlsx(input.source, input.bytes);
  } catch {
    const message = `${ERP_REFERENCE_SOURCE_LABELS[input.source]}解析失败，请确认文件格式和模板`;
    return reject({
      ok: false, status: "rejected", message, warnings: [],
      errors: [{ code: "XLSX_PARSE_ERROR", message }], errorCount: 1,
    });
  }
  if (parsed.errors.length > 0 || parsed.rows.length === 0) {
    const errors = parsed.errors.length
      ? parsed.errors.slice(0, 200).map(issue)
      : [{ code: "NO_DATA_ROWS", message: "工作表中没有可导入的数据行" }];
    return reject({
      ok: false, status: "rejected", message: "文件校验未通过，未写入任何数据",
      warnings: parsed.warnings.map(issue), errors, errorCount: parsed.errors.length || 1,
    });
  }
  const rows = parsed.rows as ProductMasterRow[] | ComboItemImportRow[];
  const extraWarnings = input.source === "products"
    ? (() => {
        const missing = (rows as ProductMasterRow[]).filter((row) => !row.productName.trim()).length;
        return missing ? [{ code: "MISSING_PRODUCT_NAME", message: `${missing} 行缺少货品名称，已保留货品编号` }] : [];
      })()
    : [];
  const warnings = [...parsed.warnings.map(issue), ...extraWarnings];
  const sourceRowCount = Number(parsed.totals.sourceRowCount ?? rows.length);
  return (await service.requestJson<ErpReferenceImportExecution>(input.principal, {
    method: "POST",
    path: ERP_REFERENCE_IMPORTS_PATH,
    service: "writer",
    payload: {
      version: CONTRACT_VERSION,
      source: input.source,
      fileName,
      fileSizeBytes: input.fileSizeBytes,
      rawFileHash,
      sheetName: parsed.sheetName,
      sourceRowCount,
      rows,
      warnings,
      totals: parsed.totals,
    },
  }, options)).data;
}
