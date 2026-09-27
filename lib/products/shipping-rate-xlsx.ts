import { Unzip, UnzipInflate } from "fflate";

export const PRODUCT_SHIPPING_RATE_SHEET_NAME = "SKU累计";

const ZIP_INPUT_CHUNK_BYTES = 1024 * 1024;
const MAX_SMALL_ENTRY_BYTES = 256 * 1024;
const MAX_XML_ELEMENT_BUFFER_CHARS = 4 * 1024 * 1024;
const MAX_SOURCE_ROWS = 20_000;
const RATE_TOLERANCE = 1e-9;

type RawCell = {
  type: string;
  rawValue: string | null;
  inlineValue: string;
  formula: string | null;
};

type CandidateRow = {
  productCode: string;
  sourceRowNumber: number;
  shippingRate: number | null;
  complete: boolean;
  formula: string | null;
};

export type ProductShippingRateImportRow = {
  productCode: string;
  shippingRate: number;
  sourceRowNumber: number;
};

export type ProductShippingRateIssue = {
  code: string;
  message: string;
  row?: number;
  field?: string;
};

export type ProductShippingRateParseResult = {
  sheetName: typeof PRODUCT_SHIPPING_RATE_SHEET_NAME;
  rows: ProductShippingRateImportRow[];
  sourceRowCount: number;
  duplicateProductCodeCount: number;
  warnings: ProductShippingRateIssue[];
  totals: {
    negativeRateCount: number;
    aboveOneRateCount: number;
    staticRateCellCount: number;
  };
};

export class ProductShippingRateWorkbookError extends Error {
  readonly issues: ProductShippingRateIssue[];

  constructor(message: string, issues: ProductShippingRateIssue[]) {
    super(message);
    this.name = "ProductShippingRateWorkbookError";
    this.issues = issues;
  }
}

function workbookError(code: string, message: string, row?: number): never {
  throw new ProductShippingRateWorkbookError(message, [{ code, message, ...(row ? { row } : {}) }]);
}

function normalizeZipPath(value: string) {
  return value.replace(/\\/g, "/").replace(/^\/+/, "");
}

function concatenate(chunks: readonly Uint8Array[], totalBytes: number) {
  const result = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

function feedZip(bytes: Uint8Array, unzip: Unzip) {
  for (let offset = 0; offset < bytes.byteLength; offset += ZIP_INPUT_CHUNK_BYTES) {
    const end = Math.min(bytes.byteLength, offset + ZIP_INPUT_CHUNK_BYTES);
    unzip.push(bytes.subarray(offset, end), end === bytes.byteLength);
  }
}

function collectZipEntries(bytes: Uint8Array, names: readonly string[]) {
  const expected = new Set(names.map(normalizeZipPath));
  const chunks = new Map<string, Uint8Array[]>();
  const sizes = new Map<string, number>();
  let failure: Error | null = null;
  const unzip = new Unzip((file) => {
    const name = normalizeZipPath(file.name);
    if (!expected.has(name)) return;
    const entryChunks: Uint8Array[] = [];
    chunks.set(name, entryChunks);
    sizes.set(name, 0);
    file.ondata = (error, data) => {
      if (failure) return;
      if (error) {
        failure = error;
        return;
      }
      const nextSize = (sizes.get(name) ?? 0) + data.byteLength;
      if (nextSize > MAX_SMALL_ENTRY_BYTES) {
        failure = new Error(`XLSX entry is larger than expected: ${name}`);
        return;
      }
      entryChunks.push(data);
      sizes.set(name, nextSize);
    };
    file.start();
  });
  unzip.register(UnzipInflate);
  feedZip(bytes, unzip);
  if (failure) throw failure;
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const result = new Map<string, string>();
  for (const name of expected) {
    const entryChunks = chunks.get(name);
    if (!entryChunks) workbookError("MISSING_XLSX_ENTRY", `工作簿缺少必要文件：${name}`);
    result.set(name, decoder.decode(concatenate(entryChunks, sizes.get(name) ?? 0)));
  }
  return result;
}

function streamZipEntry(
  bytes: Uint8Array,
  requestedName: string,
  onData: (data: Uint8Array, final: boolean) => void,
) {
  const target = normalizeZipPath(requestedName);
  let found = false;
  let completed = false;
  let failure: Error | null = null;
  const unzip = new Unzip((file) => {
    if (normalizeZipPath(file.name) !== target) return;
    if (found) {
      failure = new Error(`XLSX contains duplicate entry: ${target}`);
      return;
    }
    found = true;
    file.ondata = (error, data, final) => {
      if (failure) return;
      if (error) {
        failure = error;
        return;
      }
      try {
        onData(data, final);
        if (final) completed = true;
      } catch (caught) {
        failure = caught instanceof Error ? caught : new Error(String(caught));
      }
    };
    file.start();
  });
  unzip.register(UnzipInflate);
  feedZip(bytes, unzip);
  if (failure) throw failure;
  if (!found || !completed) workbookError("MISSING_XLSX_ENTRY", `工作簿缺少必要文件：${target}`);
}

function decodeXml(value: string) {
  return value
    .replace(/&#x([0-9a-f]+);/gi, (_, hex: string) => String.fromCodePoint(Number.parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, decimal: string) => String.fromCodePoint(Number.parseInt(decimal, 10)))
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

function xmlAttribute(fragment: string, name: string) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|\\s)${escaped}=(?:\"([^\"]*)\"|'([^']*)')`).exec(fragment);
  return match ? decodeXml(match[1] ?? match[2] ?? "") : null;
}

function qualifiedElementName(name: string) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return `(?:[A-Za-z_][\\w.-]*:)?${escaped}`;
}

function xmlChildText(fragment: string, name: string) {
  const qualifiedName = qualifiedElementName(name);
  const match = new RegExp(`<${qualifiedName}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${qualifiedName}>`).exec(fragment);
  return match ? decodeXml(match[1]) : null;
}

function createXmlElementConsumer(tagName: string, onElement: (element: string) => void) {
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const qualifiedName = qualifiedElementName(tagName);
  const expression = new RegExp(`<${qualifiedName}\\b[^>]*(?:\\/>|>[\\s\\S]*?<\\/${qualifiedName}>)`, "g");
  let buffer = "";
  return (data: Uint8Array, final: boolean) => {
    buffer += decoder.decode(data, { stream: !final });
    expression.lastIndex = 0;
    let consumed = 0;
    for (let match = expression.exec(buffer); match; match = expression.exec(buffer)) {
      onElement(match[0]);
      consumed = expression.lastIndex;
    }
    if (consumed > 0) buffer = buffer.slice(consumed);
    if (buffer.length > MAX_XML_ELEMENT_BUFFER_CHARS) {
      throw new Error(`XLSX ${tagName} XML element exceeds the supported size`);
    }
    if (final && new RegExp(`<${qualifiedName}\\b`).test(buffer)) {
      throw new Error(`XLSX ${tagName} XML is truncated`);
    }
  };
}

function parseCell(fragment: string): RawCell {
  const openingEnd = fragment.indexOf(">");
  const opening = openingEnd >= 0 ? fragment.slice(0, openingEnd + 1) : fragment;
  const textName = qualifiedElementName("t");
  const inlineValue = [...fragment.matchAll(new RegExp(`<${textName}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${textName}>`, "g"))]
    .map((match) => decodeXml(match[1]))
    .join("");
  return {
    type: xmlAttribute(opening, "t") ?? "n",
    rawValue: xmlChildText(fragment, "v")?.trim() ?? null,
    inlineValue,
    formula: xmlChildText(fragment, "f"),
  };
}

function resolveCell(cell: RawCell | undefined, sharedStrings: ReadonlyMap<number, string>) {
  if (!cell) return null;
  if (cell.type === "s") {
    if (cell.rawValue === null || !/^\d+$/.test(cell.rawValue)) return null;
    return sharedStrings.get(Number(cell.rawValue)) ?? null;
  }
  if (cell.type === "inlineStr") return cell.inlineValue;
  return cell.rawValue;
}

function parseFiniteNumber(value: string | null) {
  if (value === null || value.trim() === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function ratesEqual(left: number, right: number) {
  const tolerance = Math.max(RATE_TOLERANCE, Math.abs(left) * RATE_TOLERANCE, Math.abs(right) * RATE_TOLERANCE);
  return Math.abs(left - right) <= tolerance;
}

function resolveSheetEntry(workbookXml: string, relationshipsXml: string) {
  const sheetName = qualifiedElementName("sheet");
  const sheetTags = [...workbookXml.matchAll(new RegExp(`<${sheetName}\\b[^>]*\\/?>(?:<\\/${sheetName}>)?`, "g"))]
    .map((match) => match[0]);
  const targetSheets = sheetTags.filter((tag) => xmlAttribute(tag, "name") === PRODUCT_SHIPPING_RATE_SHEET_NAME);
  if (targetSheets.length !== 1) {
    workbookError(
      "INVALID_SHEET_COUNT",
      targetSheets.length === 0
        ? `工作簿缺少“${PRODUCT_SHIPPING_RATE_SHEET_NAME}”工作表`
        : `工作簿包含多个“${PRODUCT_SHIPPING_RATE_SHEET_NAME}”工作表`,
    );
  }
  const relationshipId = xmlAttribute(targetSheets[0], "r:id");
  if (!relationshipId) workbookError("INVALID_WORKBOOK_RELATIONSHIP", "SKU累计工作表缺少关系标识");
  const relationshipName = qualifiedElementName("Relationship");
  const relationshipTags = [...relationshipsXml.matchAll(new RegExp(`<${relationshipName}\\b[^>]*\\/?>(?:<\\/${relationshipName}>)?`, "g"))]
    .map((match) => match[0]);
  const relationship = relationshipTags.find((tag) => xmlAttribute(tag, "Id") === relationshipId);
  const relationshipTarget = relationship ? xmlAttribute(relationship, "Target") : null;
  if (!relationshipTarget) workbookError("INVALID_WORKBOOK_RELATIONSHIP", "无法定位 SKU累计工作表内容");
  const normalized = normalizeZipPath(relationshipTarget);
  return normalized.startsWith("xl/") ? normalized : `xl/${normalized}`;
}

export function parseProductShippingRateXlsx(bytes: Uint8Array): ProductShippingRateParseResult {
  if (bytes.byteLength < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
    workbookError("INVALID_XLSX_SIGNATURE", "文件签名不是有效的 .xlsx（ZIP）格式");
  }
  let workbookParts: Map<string, string>;
  try {
    workbookParts = collectZipEntries(bytes, ["xl/workbook.xml", "xl/_rels/workbook.xml.rels"]);
  } catch (error) {
    if (error instanceof ProductShippingRateWorkbookError) throw error;
    workbookError("INVALID_XLSX_ARCHIVE", "无法读取 Excel 工作簿结构");
  }
  const sheetEntry = resolveSheetEntry(
    workbookParts.get("xl/workbook.xml") ?? "",
    workbookParts.get("xl/_rels/workbook.xml.rels") ?? "",
  );

  const rawRows = new Map<number, Partial<Record<"B" | "M" | "Z" | "AA", RawCell>>>();
  const sharedStringIndexes = new Set<number>();
  let sourceCellCount = 0;
  const consumeCell = createXmlElementConsumer("c", (fragment) => {
    const openingEnd = fragment.indexOf(">");
    const opening = openingEnd >= 0 ? fragment.slice(0, openingEnd + 1) : fragment;
    const reference = xmlAttribute(opening, "r") ?? "";
    const match = /^(B|M|Z|AA)([1-9]\d*)$/.exec(reference);
    if (!match) return;
    const rowNumber = Number(match[2]);
    if (rowNumber > MAX_SOURCE_ROWS) workbookError("TOO_MANY_SOURCE_ROWS", `SKU累计最多支持 ${MAX_SOURCE_ROWS} 行`, rowNumber);
    const column = match[1] as "B" | "M" | "Z" | "AA";
    const cell = parseCell(fragment);
    const row = rawRows.get(rowNumber) ?? {};
    row[column] = cell;
    rawRows.set(rowNumber, row);
    sourceCellCount += 1;
    if (sourceCellCount > MAX_SOURCE_ROWS * 4) workbookError("TOO_MANY_SOURCE_CELLS", "SKU累计有效单元格数量超出限制");
    if (cell.type === "s" && cell.rawValue !== null && /^\d+$/.test(cell.rawValue)) {
      sharedStringIndexes.add(Number(cell.rawValue));
    }
  });
  try {
    streamZipEntry(bytes, sheetEntry, consumeCell);
  } catch (error) {
    if (error instanceof ProductShippingRateWorkbookError) throw error;
    workbookError("INVALID_WORKSHEET_XML", "SKU累计工作表内容损坏或超出支持范围");
  }

  const sharedStrings = new Map<number, string>();
  let sharedStringIndex = -1;
  const consumeSharedString = createXmlElementConsumer("si", (fragment) => {
    sharedStringIndex += 1;
    if (!sharedStringIndexes.has(sharedStringIndex)) return;
    const textName = qualifiedElementName("t");
    const value = [...fragment.matchAll(new RegExp(`<${textName}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${textName}>`, "g"))]
      .map((match) => decodeXml(match[1]))
      .join("");
    sharedStrings.set(sharedStringIndex, value);
  });
  try {
    streamZipEntry(bytes, "xl/sharedStrings.xml", consumeSharedString);
  } catch (error) {
    if (error instanceof ProductShippingRateWorkbookError) throw error;
    workbookError("INVALID_SHARED_STRINGS", "工作簿共享字符串表损坏或超出支持范围");
  }
  if ([...sharedStringIndexes].some((index) => !sharedStrings.has(index))) {
    workbookError("MISSING_SHARED_STRING", "SKU累计引用了不存在的共享字符串");
  }

  const header = rawRows.get(1) ?? {};
  const expectedHeaders = { B: "代码", M: "实际金额", Z: "合计快递费", AA: "快递费占比" } as const;
  for (const [column, expected] of Object.entries(expectedHeaders) as Array<[keyof typeof expectedHeaders, string]>) {
    const actual = resolveCell(header[column], sharedStrings)?.trim() ?? "";
    if (actual !== expected) {
      workbookError("INVALID_SHEET_HEADERS", `SKU累计!${column}1 必须为“${expected}”，当前为“${actual || "空"}”`, 1);
    }
  }

  const candidatesByCode = new Map<string, CandidateRow>();
  const seenCodeCounts = new Map<string, number>();
  const errors: ProductShippingRateIssue[] = [];
  let sourceRowCount = 0;
  for (const rowNumber of [...rawRows.keys()].filter((value) => value > 1).sort((left, right) => left - right)) {
    const rawRow = rawRows.get(rowNumber) ?? {};
    const productCode = (resolveCell(rawRow.B, sharedStrings) ?? "").trim();
    if (!productCode) continue;
    if (productCode === expectedHeaders.B
      && resolveCell(rawRow.M, sharedStrings) === null
      && resolveCell(rawRow.Z, sharedStrings) === null
      && resolveCell(rawRow.AA, sharedStrings) === null) {
      continue;
    }
    sourceRowCount += 1;
    if (productCode.length > 200) {
      errors.push({ code: "PRODUCT_CODE_TOO_LONG", message: "规格代码超过 200 个字符", row: rowNumber });
      continue;
    }
    seenCodeCounts.set(productCode, (seenCodeCounts.get(productCode) ?? 0) + 1);
    const actualAmount = parseFiniteNumber(resolveCell(rawRow.M, sharedStrings));
    const shippingFee = parseFiniteNumber(resolveCell(rawRow.Z, sharedStrings));
    const cachedRate = parseFiniteNumber(resolveCell(rawRow.AA, sharedStrings));
    const complete = actualAmount !== null && shippingFee !== null && cachedRate !== null;
    let shippingRate: number | null = null;
    if (complete) {
      shippingRate = actualAmount === 0 ? 0 : shippingFee / actualAmount;
      if (!Number.isFinite(shippingRate) || !ratesEqual(shippingRate, cachedRate)) {
        errors.push({
          code: "STALE_OR_INVALID_RATE",
          message: `规格 ${productCode} 的快递费占比与“合计快递费 ÷ 实际金额”不一致，请在 Excel 中重新计算后导入`,
          row: rowNumber,
        });
        continue;
      }
      if (Object.is(shippingRate, -0)) shippingRate = 0;
    }
    const candidate: CandidateRow = {
      productCode,
      sourceRowNumber: rowNumber,
      shippingRate,
      complete,
      formula: rawRow.AA?.formula ?? null,
    };
    const previous = candidatesByCode.get(productCode);
    if (!previous || (!previous.complete && complete)) {
      candidatesByCode.set(productCode, candidate);
      continue;
    }
    if (previous.complete && complete && previous.shippingRate !== null && shippingRate !== null && !ratesEqual(previous.shippingRate, shippingRate)) {
      errors.push({
        code: "CONFLICTING_DUPLICATE_PRODUCT_CODE",
        message: `规格代码 ${productCode} 的重复行存在不同快递费率`,
        row: rowNumber,
      });
    }
  }

  for (const candidate of candidatesByCode.values()) {
    if (!candidate.complete || candidate.shippingRate === null) {
      errors.push({
        code: "MISSING_RATE_VALUES",
        message: `规格 ${candidate.productCode} 缺少实际金额、合计快递费或快递费占比`,
        row: candidate.sourceRowNumber,
      });
    }
  }
  if (errors.length > 0) {
    throw new ProductShippingRateWorkbookError("SKU累计工作表校验未通过", errors.slice(0, 200));
  }

  const rows = [...candidatesByCode.values()]
    .map((candidate) => ({
      productCode: candidate.productCode,
      shippingRate: candidate.shippingRate!,
      sourceRowNumber: candidate.sourceRowNumber,
    }))
    .sort((left, right) => left.productCode.localeCompare(right.productCode, "zh-CN"));
  if (rows.length === 0) workbookError("NO_DATA_ROWS", "SKU累计工作表没有可导入的规格快递费率");

  const duplicateProductCodeCount = [...seenCodeCounts.values()].filter((count) => count > 1).length;
  const negativeRateCount = rows.filter((row) => row.shippingRate < 0).length;
  const aboveOneRateCount = rows.filter((row) => row.shippingRate > 1).length;
  const selectedRows = new Set(rows.map((row) => row.sourceRowNumber));
  const staticRateCellCount = [...candidatesByCode.values()]
    .filter((candidate) => selectedRows.has(candidate.sourceRowNumber) && !candidate.formula)
    .length;
  const warnings: ProductShippingRateIssue[] = [
    ...(duplicateProductCodeCount > 0 ? [{ code: "DUPLICATE_PRODUCT_CODES_COLLAPSED", message: `已合并 ${duplicateProductCodeCount} 组费率一致或仅一行完整的重复规格代码` }] : []),
    ...(negativeRateCount > 0 ? [{ code: "NEGATIVE_SHIPPING_RATES", message: `${negativeRateCount} 个规格的实际金额为负或运费冲回，快递费率为负值，已保留源口径` }] : []),
    ...(aboveOneRateCount > 0 ? [{ code: "SHIPPING_RATES_ABOVE_ONE", message: `${aboveOneRateCount} 个规格的快递费率超过 100%，已保留源口径` }] : []),
    ...(staticRateCellCount > 0 ? [{ code: "STATIC_RATE_CELLS", message: `${staticRateCellCount} 个规格的快递费占比为静态值；已通过实际金额与合计快递费重新核验` }] : []),
  ];

  return {
    sheetName: PRODUCT_SHIPPING_RATE_SHEET_NAME,
    rows,
    sourceRowCount,
    duplicateProductCodeCount,
    warnings,
    totals: { negativeRateCount, aboveOneRateCount, staticRateCellCount },
  };
}
