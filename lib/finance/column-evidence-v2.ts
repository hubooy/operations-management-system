/** Pure, default-unregistered source-column evidence over parser rawLines.
 *
 * Call immediately before aggregateLines(rawLines). The existing
 * FinanceLineInput values are unchanged; origins and raw header cells are
 * parallel source coordinates supplied by that parser loop. This module does
 * not read workbook bytes, verify a raw-file hash, import, or grant a mapping.
 */
import type { FinanceLineInput, FinanceScopeType } from "./types";

export type FinanceSourceDimension = {
  columnIndex: number;
  scopeKey: string;
  scopeType: FinanceScopeType;
  scopeName: string;
  groupName: string;
};

export type FinanceCellOrigin = { rowIndex: number; columnIndex: number };
export type FinanceHeaderCell = {
  columnIndex: number;
  rawGroupCell: string | null;
  rawShopCell: string | null;
};

export type FinanceColumnEvidenceInput = {
  month: string;
  sheetName: string;
  dimensions: readonly FinanceSourceDimension[];
  rawLines: readonly FinanceLineInput[];
  origins: readonly FinanceCellOrigin[];
  headerCells: readonly FinanceHeaderCell[];
};

const MAX_COLUMNS = 500;
const MAX_CELLS = 100_000;
const MAX_EVIDENCE_BYTES = 32 * 1024 * 1024;
const encoder = new TextEncoder();
const LINE_KEYS = ["amountCents", "groupName", "isTotal", "metricKey", "month", "rateBps",
  "rawValue", "scopeKey", "scopeName", "scopeType", "section", "sortOrder",
  "sourceRowCount", "subjectName", "valueType"].sort();

function requireEvidence(ok: unknown, message = "财报来源列与聚合前事实身份不一致"): asserts ok {
  if (!ok) throw new Error(message);
}

function text(value: unknown, maximum: number, empty = false): string {
  requireEvidence(typeof value === "string" && value.length <= maximum && (empty || value.length > 0));
  return value;
}

function number(value: unknown, maximum: number, minimum = 0): number {
  requireEvidence(Number.isSafeInteger(value) && (value as number) >= minimum && (value as number) <= maximum);
  return value as number;
}

function optionalNumber(value: unknown): number | null {
  return value === null ? null : number(value, Number.MAX_SAFE_INTEGER, -Number.MAX_SAFE_INTEGER);
}

function compact(value: string | null): string {
  return (value ?? "").trim().replace(/[\s　]+/g, "");
}

function scope(value: unknown): FinanceScopeType {
  requireEvidence(value === "business" || value === "group" || value === "shop");
  return value;
}

function lineIdentity(line: FinanceLineInput, month: string, dimension: FinanceSourceDimension,
  origin: FinanceCellOrigin) {
  requireEvidence(line && typeof line === "object" && Object.keys(line).sort().join("\u0000") === LINE_KEYS.join("\u0000"));
  requireEvidence(line.month === month && (line.section === "summary" || line.section === "kingdee")
    && line.scopeKey === dimension.scopeKey && line.scopeType === dimension.scopeType
    && line.scopeName === dimension.scopeName && line.groupName === dimension.groupName
    && line.sourceRowCount === 1 && line.sortOrder === origin.rowIndex + 1);
  text(line.subjectName, 2_000);
  text(line.metricKey, 500, true);
  text(line.rawValue, 4_000, true);
  requireEvidence(["amount", "rate", "number", "text"].includes(line.valueType)
    && typeof line.isTotal === "boolean");
  return { section: line.section, rowIndex: origin.rowIndex,
    columnIndex: origin.columnIndex, subjectName: line.subjectName,
    metricKey: line.metricKey, scopeKey: dimension.scopeKey,
    valueType: line.valueType, amountCents: optionalNumber(line.amountCents),
    rateBps: optionalNumber(line.rateBps), rawValue: line.rawValue,
    isTotal: line.isTotal };
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function extractFinanceColumnEvidenceV2(input: FinanceColumnEvidenceInput) {
  requireEvidence(input && typeof input === "object");
  const month = text(input.month, 7);
  requireEvidence(/^\d{4}-(?:0[1-9]|1[0-2])$/.test(month));
  const sheetName = text(input.sheetName, 200);
  const dimensions = input.dimensions;
  const rawLines = input.rawLines;
  const origins = input.origins;
  const headerCells = input.headerCells;
  requireEvidence(Array.isArray(dimensions) && dimensions.length >= 2 && dimensions.length <= MAX_COLUMNS
    && Array.isArray(rawLines) && rawLines.length <= MAX_CELLS
    && Array.isArray(origins) && origins.length === rawLines.length
    && Array.isArray(headerCells) && headerCells.length >= dimensions.length
    && headerCells.length <= MAX_COLUMNS);

  const byColumn = new Map<number, FinanceSourceDimension>();
  const headers = new Map<number, FinanceHeaderCell>();
  for (const item of headerCells) {
    const index = number(item?.columnIndex, 10_000, 1);
    requireEvidence(!headers.has(index) && (item.rawGroupCell === null
      || typeof item.rawGroupCell === "string") && (item.rawShopCell === null
      || typeof item.rawShopCell === "string"));
    if (item.rawGroupCell !== null) text(item.rawGroupCell, 1_000, true);
    if (item.rawShopCell !== null) text(item.rawShopCell, 1_000, true);
    headers.set(index, item);
  }
  const largestColumn = Math.max(...dimensions.map(item => number(item.columnIndex, 10_000, 1)));
  const effectiveGroups = new Map<number, string>();
  let effectiveGroup = "";
  for (let index = 1; index <= largestColumn; index += 1) {
    const header = headers.get(index);
    requireEvidence(header !== undefined,
      "财报组名继承链缺少原始中间表头列");
    if (index > 1 && compact(header.rawGroupCell)) {
      effectiveGroup = compact(header.rawGroupCell);
    }
    effectiveGroups.set(index, effectiveGroup);
  }
  requireEvidence(headers.size === largestColumn,
    "财报来源表头列不是固定连续范围");
  const columns = [];
  for (const item of dimensions) {
    const index = number(item?.columnIndex, 10_000, 1);
    requireEvidence(!byColumn.has(index));
    const kind = scope(item.scopeType);
    const key = text(item.scopeKey, 2_000);
    const name = text(item.scopeName, 1_000);
    const group = text(item.groupName, 1_000, true);
    const header = headers.get(index);
    requireEvidence(header !== undefined);
    if (kind === "shop") requireEvidence(compact(header.rawShopCell) === name);
    if (kind === "group") requireEvidence(compact(header.rawShopCell) === "组汇总");
    if (kind !== "business") requireEvidence(effectiveGroups.get(index) === group,
      "财报店铺的组名不属于原始表头继承链");
    byColumn.set(index, item);
    columns.push({ columnIndex: index, scopeKey: key, scopeType: kind,
      scopeName: name, groupName: group,
      rawGroupCell: header.rawGroupCell, rawShopCell: header.rawShopCell });
  }
  columns.sort((a, b) => a.columnIndex - b.columnIndex);
  const completeHeaderChain = [...headers.values()].sort((a, b) => a.columnIndex - b.columnIndex)
    .map(item => ({ columnIndex: item.columnIndex, rawGroupCell: item.rawGroupCell,
      rawShopCell: item.rawShopCell }));
  requireEvidence(columns[0].scopeType === "business" && columns[0].columnIndex === 1);

  const seenCells = new Set<string>();
  const cells = [];
  let bytes = 0;
  for (let index = 0; index < rawLines.length; index += 1) {
    const origin = origins[index];
    const rowIndex = number(origin?.rowIndex, 1_000_000, 1);
    const columnIndex = number(origin?.columnIndex, 10_000, 1);
    const dimension = byColumn.get(columnIndex);
    requireEvidence(dimension !== undefined);
    const cell = lineIdentity(rawLines[index], month, dimension, { rowIndex, columnIndex });
    const position = `${cell.section}\u0000${rowIndex}\u0000${columnIndex}`;
    requireEvidence(!seenCells.has(position), "财报来源格重复，不能聚合后掩盖");
    seenCells.add(position);
    bytes += encoder.encode(JSON.stringify(cell)).length + 1;
    requireEvidence(bytes <= MAX_EVIDENCE_BYTES, "财报来源格证据超过固定容量");
    cells.push(cell);
  }
  cells.sort((a, b) => a.rowIndex - b.rowIndex || a.columnIndex - b.columnIndex
    || a.section.localeCompare(b.section));

  const shops = columns.filter(item => item.scopeType === "shop");
  const byLegacyScope = new Map<string, typeof shops>();
  for (const item of shops) {
    const found = byLegacyScope.get(item.scopeKey) ?? [];
    found.push(item);
    byLegacyScope.set(item.scopeKey, found);
  }
  const collisions = [];
  for (const [scopeKey, found] of byLegacyScope) {
    if (found.length < 2) continue;
    const groups = [...new Set(found.map(item => item.groupName))].sort();
    const names = [...new Set(found.map(item => item.scopeName))].sort();
    const memberColumns = found.map(item => item.columnIndex).sort((a, b) => a - b);
    const actualKeys = new Set<string>();
    const collisionsByKey = new Map<string, Set<number>>();
    for (const cell of cells.filter(item => memberColumns.includes(item.columnIndex))) {
      const key = `${cell.section}\u0000${cell.subjectName}`;
      actualKeys.add(key);
      const members = collisionsByKey.get(key) ?? new Set<number>();
      members.add(cell.columnIndex);
      collisionsByKey.set(key, members);
    }
    const actualLegacyMergedSubjectCount = [...actualKeys].filter(key =>
      (collisionsByKey.get(key)?.size ?? 0) > 1).length;
    collisions.push({ legacyScopeKey: scopeKey, columnIndices: memberColumns,
      groupNames: groups, shopNames: names,
      kind: groups.length > 1 ? "cross_group_same_name" :
        names.length > 1 ? "same_scope_key_multiple_names" : "duplicate_shop_columns",
      actualLegacyMergedSubjectCount });
  }
  collisions.sort((a, b) => a.legacyScopeKey.localeCompare(b.legacyScopeKey));
  const body = { schemaVersion: "finance-column-cell-evidence-v2-candidate",
    month, sheetName, headerCells: completeHeaderChain, columns, cells, collisions,
    columnCount: columns.length, cellCount: cells.length,
    crossGroupSameNameRisk: collisions.some(item => item.kind === "cross_group_same_name"),
    sourceWorkbookBytesVerified: false, financeShopMappingVerified: false,
    candidateOnly: true };
  const raw = JSON.stringify(body);
  const encoded = encoder.encode(raw);
  requireEvidence(encoded.length <= MAX_EVIDENCE_BYTES,
    "财报来源列证据超过固定UTF-8容量");
  const buffer = encoded.buffer.slice(encoded.byteOffset,
    encoded.byteOffset + encoded.byteLength) as ArrayBuffer;
  return { ...body, evidenceDigest: hex(await crypto.subtle.digest("SHA-256", buffer)) };
}
