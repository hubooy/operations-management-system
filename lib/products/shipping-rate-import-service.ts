import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoProductsService,
  PRODUCTS_IMPORTS_PATH,
  type DjangoProductsServiceOptions,
} from "@/lib/django/products-service";
import {
  PRODUCT_SHIPPING_RATE_SHEET_NAME,
  ProductShippingRateWorkbookError,
  parseProductShippingRateXlsx,
  type ProductShippingRateIssue,
} from "@/lib/products/shipping-rate-xlsx";

export const PRODUCT_SHIPPING_RATE_IMPORT_VERSION = "product-shipping-rates-normalized-v1";
const MAX_FILE_BYTES = 20 * 1024 * 1024;
const MAX_RATE_PPT = 10 ** 15;

export type ProductShippingRateImportBatch = {
  id: string;
  source: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  rawFileHash: string;
  contentHash: string;
  sheetName: string;
  actor: string;
  status: string;
  sourceRowCount: number;
  rowCount: number;
  insertedCount: number;
  updatedCount: number;
  duplicateCount: number;
  warningCount: number;
  warnings: ProductShippingRateIssue[];
  totals: Record<string, unknown>;
  createdAt: string;
  completedAt: string | null;
};

export type ProductShippingRateImportExecution = {
  ok: boolean;
  status: "imported" | "duplicate" | "rejected";
  message: string;
  batch?: ProductShippingRateImportBatch | null;
  warnings: ProductShippingRateIssue[];
  errors?: ProductShippingRateIssue[];
  errorCount?: number;
  verification?: {
    verified: boolean;
    parsedRowCount: number;
    readbackRowCount: number;
  };
};

export type ProductShippingRateImportWriter = {
  requestJson<T>(
    principal: AppPrincipal,
    input: {
      method: "POST";
      path: typeof PRODUCTS_IMPORTS_PATH;
      service: "writer";
      payload: Record<string, unknown>;
    },
    options?: Omit<DjangoProductsServiceOptions, "config">,
  ): Promise<{ data: T }>;
};

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256(bytes: Uint8Array) {
  const exact = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return toHex(await crypto.subtle.digest("SHA-256", exact));
}

function safeFileName(name: string) {
  const baseName = name.split(/[\\/]/).pop() ?? "sku-shipping-rates.xlsx";
  return baseName.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}

function issue(code: string, message: string): ProductShippingRateIssue {
  return { code, message };
}

function boundedIssues(issues: ProductShippingRateIssue[]) {
  return issues.slice(0, 200).map((value) => ({
    ...(Number.isSafeInteger(value.row) && Number(value.row) > 0 ? { row: Number(value.row) } : {}),
    ...(typeof value.field === "string" && value.field ? { field: value.field.slice(0, 100) } : {}),
    ...(typeof value.code === "string" && value.code ? { code: value.code.slice(0, 100) } : {}),
    message: String(value.message).slice(0, 500),
  }));
}

export async function importProductShippingRateBytes(input: {
  bytes: Uint8Array;
  fileName: string;
  fileSizeBytes: number;
  principal: AppPrincipal;
  signal?: AbortSignal;
  writer?: ProductShippingRateImportWriter;
}): Promise<ProductShippingRateImportExecution> {
  const writer = input.writer ?? createDjangoProductsService();
  const rawFileHash = await sha256(input.bytes);
  const fileName = safeFileName(input.fileName);
  const post = async (payload: Record<string, unknown>) => (
    await writer.requestJson<ProductShippingRateImportExecution>(input.principal, {
      method: "POST",
      path: PRODUCTS_IMPORTS_PATH,
      service: "writer",
      payload,
    }, { signal: input.signal })
  ).data;
  const reject = (errors: ProductShippingRateIssue[], warnings: ProductShippingRateIssue[] = []) => post({
    version: PRODUCT_SHIPPING_RATE_IMPORT_VERSION,
    kind: "rejection",
    fileName,
    fileSizeBytes: Number.isSafeInteger(input.fileSizeBytes) && input.fileSizeBytes >= 0
      ? input.fileSizeBytes
      : 0,
    rawFileHash,
    errors: boundedIssues(errors),
    warnings: boundedIssues(warnings),
  });

  if (!fileName.toLowerCase().endsWith(".xlsx")) {
    return reject([issue("INVALID_FILE_EXTENSION", "文件扩展名必须为 .xlsx")]);
  }
  if (input.bytes.byteLength === 0 || input.fileSizeBytes !== input.bytes.byteLength) {
    return reject([issue("FILE_SIZE_MISMATCH", "请重新选择并完整上传文件")]);
  }
  if (input.bytes.byteLength > MAX_FILE_BYTES) {
    return reject([issue("FILE_TOO_LARGE", "SKU 快递费率文件最大支持 20MB")]);
  }

  let parsed;
  try {
    parsed = parseProductShippingRateXlsx(input.bytes);
  } catch (error) {
    const issues = error instanceof ProductShippingRateWorkbookError
      ? error.issues
      : [issue("XLSX_PARSE_ERROR", "SKU 快递费率工作簿解析失败，请确认文件未损坏")];
    return reject(issues);
  }

  const rows: Array<{ productCode: string; shippingRatePpt: number; sourceRowNumber: number }> = [];
  for (const row of parsed.rows) {
    const shippingRatePpt = Math.round(row.shippingRate * 10 ** 12);
    if (!Number.isSafeInteger(shippingRatePpt) || Math.abs(shippingRatePpt) > MAX_RATE_PPT) {
      return reject([
        {
          row: row.sourceRowNumber,
          field: "快递费占比",
          code: "SHIPPING_RATE_OUT_OF_RANGE",
          message: "重算后的快递费率超出安全数值范围",
        },
      ], parsed.warnings);
    }
    rows.push({
      productCode: row.productCode,
      shippingRatePpt,
      sourceRowNumber: row.sourceRowNumber,
    });
  }

  return post({
    version: PRODUCT_SHIPPING_RATE_IMPORT_VERSION,
    kind: "import",
    fileName,
    fileSizeBytes: input.fileSizeBytes,
    rawFileHash,
    sheetName: PRODUCT_SHIPPING_RATE_SHEET_NAME,
    sourceRowCount: parsed.sourceRowCount,
    duplicateCount: parsed.duplicateProductCodeCount,
    rows,
    warnings: boundedIssues(parsed.warnings),
    totals: parsed.totals,
  });
}
