import type { MarketEntryForImport } from "@/lib/market/import-core";
import { parseMarketRows } from "@/lib/market/parser";

export const MARKET_IMPORT_CONTRACT_VERSION = "market-import-v1" as const;

type ImportRange = {
  category: string;
  scope: string;
  rankingDimension: "SKU" | "SPU";
  priceBandFilter: string;
  periodStart: string;
  periodEnd: string;
};

function canonicalValue(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("市场导入包含非有限数字");
    return Object.is(value, -0) ? 0 : value;
  }
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(record).sort().map((key) => [key, canonicalValue(record[key])]),
    );
  }
  throw new Error(`市场导入包含不支持的字段类型：${typeof value}`);
}

export function canonicalMarketJson(value: unknown): string {
  return JSON.stringify(canonicalValue(value));
}

async function sha256Bytes(bytes: Uint8Array): Promise<string> {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  const digest = await crypto.subtle.digest("SHA-256", input);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Text(value: string): Promise<string> {
  return sha256Bytes(new TextEncoder().encode(value));
}

function rangeFor(row: MarketEntryForImport): ImportRange {
  return {
    category: row.category,
    scope: row.scope,
    rankingDimension: row.rankingDimension,
    priceBandFilter: row.priceBandFilter,
    periodStart: row.periodStart,
    periodEnd: row.periodEnd,
  };
}

function businessRow(row: MarketEntryForImport): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(row)
      .filter(([key]) => !["naturalKey", "sourceRowNumber", "raw"].includes(key))
      .sort(([left], [right]) => left.localeCompare(right)),
  );
}

function lexical(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

export async function prepareDjangoMarketImport(input: {
  bytes: Uint8Array;
  fileName: string;
  fileSizeBytes: number;
  sourceType: "market_ranking" | "sku_catalog";
  defaultStartDate: string;
  defaultEndDate: string;
  defaultCategory?: string;
  defaultScope?: string;
  defaultPriceBandFilter?: string;
}) {
  const parsed = parseMarketRows(input);
  if (parsed.rows.some(
    (row) => row.periodStart < input.defaultStartDate || row.periodEnd > input.defaultEndDate,
  )) {
    throw new Error("市场文件包含表单权威周期之外的日期，请修正导入周期后重试");
  }
  const rangesByIdentity = new Map<string, ImportRange>();
  for (const row of parsed.rows) {
    const range = rangeFor(row);
    rangesByIdentity.set(canonicalMarketJson(range), range);
  }
  const ranges = [...rangesByIdentity.entries()]
    .sort(([left], [right]) => lexical(left, right))
    .map(([, value]) => value);
  const scope = { sourceType: input.sourceType, ranges };
  const rows = [...parsed.rows].sort((left, right) => lexical(left.naturalKey, right.naturalKey));
  const contentHash = await sha256Text(canonicalMarketJson({
    contractVersion: MARKET_IMPORT_CONTRACT_VERSION,
    scope,
    rows: rows.map(businessRow),
  }));
  return {
    contractVersion: MARKET_IMPORT_CONTRACT_VERSION,
    sourceType: input.sourceType,
    fileName: input.fileName,
    fileSizeBytes: input.fileSizeBytes,
    rawFileHash: await sha256Bytes(input.bytes),
    contentHash,
    sheetName: parsed.sheetName,
    rows: parsed.rows,
    warnings: parsed.warnings,
    scope,
  };
}
