export type FinanceScopeType = "business" | "group" | "shop";
export type FinanceSection = "summary" | "kingdee";
export type FinanceValueType = "amount" | "rate" | "number" | "text";

export type FinanceImportIssue = {
  sheet?: string;
  month?: string;
  row?: number;
  code: string;
  message: string;
};

export type FinanceLineInput = {
  month: string;
  section: FinanceSection;
  metricKey: string;
  subjectName: string;
  scopeKey: string;
  scopeType: FinanceScopeType;
  scopeName: string;
  groupName: string;
  valueType: FinanceValueType;
  amountCents: number | null;
  rateBps: number | null;
  rawValue: string;
  sourceRowCount: number;
  sortOrder: number;
  isTotal: boolean;
};

export type ParsedFinanceMonth = {
  month: string;
  sheetName: string;
  businessName: string;
  shopCount: number;
  subjectCount: number;
  lines: FinanceLineInput[];
};

export type ParsedFinanceWorkbook = {
  months: ParsedFinanceMonth[];
  warnings: FinanceImportIssue[];
  sourceSheetCount: number;
};

/** Opt-in evidence preview only. The Django importer does not accept v2. */
export type FinanceNormalizedV2Candidate = {
  schemaVersion: "finance-normalized-v2-candidate";
  fileName: string;
  fileSizeBytes: number;
  rawFileHash: string;
  rawFileHashVerifiedByBackend: false;
  completeWorkbookBindingVerified: false;
  financeShopMappingVerified: false;
  backendImportSupported: false;
} & ({
  disposition: "candidate_only";
  warnings: FinanceImportIssue[];
  sourceSheetCount: number;
  months: ParsedFinanceMonth[];
  columnEvidence: Awaited<ReturnType<typeof import("./column-evidence-v2").extractFinanceColumnEvidenceV2>>[];
  candidateDigest: string;
} | {
  disposition: "rejected";
  warnings: FinanceImportIssue[];
  errors: FinanceImportIssue[];
  message: string;
});

export type FinanceImportBatch = {
  id: string;
  source: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  status: string;
  rowCount: number;
  insertedCount: number;
  duplicateCount: number;
  warningCount: number;
  parsedMonthCount: number;
  importedMonthCount: number;
  skippedMonthCount: number;
  subjectCount: number;
  months: string[];
  warnings: FinanceImportIssue[];
  createdAt: string;
  completedAt: string | null;
};

export type FinanceTargetPeriodType = "month" | "year" | "project";

export type FinanceTargetInput = {
  id?: string;
  expectedVersion?: number;
  periodType: FinanceTargetPeriodType;
  periodKey: string;
  platform?: string;
  shopName?: string;
  category?: string;
  manager?: string;
  salesTargetCents?: number;
  profitTargetCents?: number;
  grossMarginBps?: number;
  smallMarginBps?: number;
  inventoryCleanupTargetCents?: number;
  promotionFeeRatioBps?: number;
  stagnantInventoryTargetCents?: number;
};

export type FinanceTarget = Required<Omit<FinanceTargetInput, "id" | "expectedVersion">> & {
  id: string;
  version: number;
  createdAt: string;
  updatedAt: string;
};
