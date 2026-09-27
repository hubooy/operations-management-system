export type SalesImportIssue = {
  row?: number;
  column?: string;
  field?: string;
  code?: string;
  message: string;
};

export type SalesLineInput = {
  sourceRowNumber: number;
  sourceLineKey: string;
  sourceRowHash: string;
  orderNo: string;
  onlineOrderNo: string;
  channel: string;
  platform: string;
  shopName: string;
  logisticsCompany: string;
  warehouse: string;
  productCode: string;
  onlineSpecCode: string;
  productName: string;
  specification: string;
  barcode: string;
  supplier: string;
  category: string;
  quantity: number;
  listUnitPriceCents: number;
  costAmountCents: number;
  allocatedUnitPriceCents: number;
  allocatedAmountCents: number;
  feeAllocationCents: number;
  grossProfitCents: number;
  grossMarginBps: number;
  untaxedGrossProfitCents: number;
  untaxedGrossMarginBps: number;
  orderTime: string;
  salesTime: string;
  shipTime: string;
  lineShipTime: string;
  businessType: "sale" | "return" | "zero";
};

export type SalesImportBatch = {
  id: string;
  source: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  rawFileHash?: string;
  sheetName: string;
  status: string;
  rowCount: number;
  insertedCount: number;
  duplicateCount: number;
  warningCount: number;
  warnings: SalesImportIssue[];
  totals: unknown;
  createdAt: string;
  completedAt: string | null;
};

export function sanitizeSalesIssues(issues: readonly unknown[]): SalesImportIssue[] {
  return issues.slice(0, 200).map((issue) => {
    if (typeof issue === "string") return { message: issue.slice(0, 500) };
    if (!issue || typeof issue !== "object" || Array.isArray(issue)) {
      return { message: String(issue).slice(0, 500) };
    }
    const value = issue as Record<string, unknown>;
    const row = Number(value.row ?? value.rowNumber ?? value.sourceRowNumber);
    const result: SalesImportIssue = {
      message: String(value.message ?? value.reason ?? value.code ?? "数据校验失败").slice(0, 500),
    };
    if (Number.isSafeInteger(row) && row > 0) result.row = row;
    if (typeof value.column === "string") result.column = value.column.slice(0, 100);
    if (typeof value.field === "string") result.field = value.field.slice(0, 100);
    if (typeof value.code === "string") result.code = value.code.slice(0, 100);
    return result;
  });
}
