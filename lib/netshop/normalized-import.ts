import { unzipSync } from "fflate";
import * as XLSX from "xlsx";
import { parseXlsxFirstSheet, type XlsxCellValue } from "@/lib/imports/xlsx";
import type { NetshopImportBatch, NetshopImportIssue, NetshopRowInput, NetshopSource } from "@/lib/netshop/database";
import { netshopMasterRowKey } from "@/lib/netshop/batch-identity";
import { dailyDateCoverage, dailyRowKey, detectJdDailyDataset } from "@/lib/netshop/daily-contract";
import { extractTmallProductAssetImages, persistTmallProductAssetImages, productAssetMetadata, type TmallProductAssetImage } from "@/lib/netshop/product-image-assets";
import { resolveEnabledTmallShop } from "@/lib/netshop/tmall-store-catalog";


export const DEFAULT_PLATFORM = "京东";

export const DEFAULT_SHOP_NAME = "志高商用设备旗舰店";

export const TMALL_PLATFORM = "天猫";

export const TMALL_YIJIU_SHOP = "天猫-志高亿玖专卖店";

export const MAX_TABULAR_ROWS = 50_000;

const MAX_TABULAR_COLUMNS = 120;

const MAX_UNCOMPRESSED_ENTRY_BYTES = 25 * 1024 * 1024;


const sourceSet = new Set<NetshopSource>([
  "jd_shop_overview",
  "jd_sku_daily",
  "jd_promotion",
  "jd_b2b",
  "jd_product_master",
  "jd_cs",
  "jd_yimei_sku",
  "tmall_product_master",
  "tmall_product_assets",
  "tmall_product_daily",
  "tmall_promotion",
  "inv_selfop",
]);


export type NetshopImportExecution = {
  ok: boolean;
  status: "imported" | "duplicate" | "rejected";
  message: string;
  batch?: NetshopImportBatch | null;
  warnings: NetshopImportIssue[];
  errors?: NetshopImportIssue[];
  errorCount?: number;
  verification?: {
    verified: boolean;
    parsedRowCount: number;
    readbackRowCount: number;
    dateMin: string | null;
    dateMax: string | null;
    dataset: string;
    platform: string;
    shopName: string;
    unmatchedProductCount: number;
    imageCount?: number;
    verifiedImageCount?: number;
  };
};


type TabularRow = {
  rowNumber: number;
  values: Array<string | number | boolean | null>;
};


export type ParsedTable = {
  sheetName: string;
  archiveEntryName?: string;
  rows: TabularRow[];
};


export type ParsedHeader = {
  index: number;
  headers: string[];
  businessDateFallback?: string;
};


export function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}


export function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return crypto.subtle.digest("SHA-256", input);
}


export function safeFileName(name: string) {
  const baseName = name.split(/[\\/]/).pop() ?? "netshop-import";
  return baseName.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}


function isXlsx(bytes: Uint8Array) {
  return bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04;
}


function normalizeSource(value: FormDataEntryValue | null): NetshopSource | null {
  if (typeof value !== "string") return null;
  const source = value.trim() as NetshopSource;
  return sourceSet.has(source) ? source : null;
}


export function normalizeText(value: unknown) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}


function normalizeHeader(value: unknown) {
  return normalizeText(value).replace(/\uFEFF/g, "");
}


function normalizeKey(value: string) {
  return value.toLowerCase().replace(/[\s_\-（）()【】[\]：:./\\]/g, "");
}


function parseDelimitedText(text: string, delimiter: "," | "\t"): TabularRow[] {
  const rows: TabularRow[] = [];
  let current = "";
  let record: Array<string | null> = [];
  let rowNumber = 1;
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"') {
      if (inQuotes && next === '"') {
        current += '"';
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (!inQuotes && char === delimiter) {
      record.push(current);
      current = "";
      continue;
    }
    if (!inQuotes && (char === "\n" || char === "\r")) {
      if (char === "\r" && next === "\n") index += 1;
      record.push(current);
      if (record.some((cell) => normalizeText(cell))) rows.push({ rowNumber, values: record });
      rowNumber += 1;
      record = [];
      current = "";
      continue;
    }
    current += char;
  }

  if (current || record.length) {
    record.push(current);
    if (record.some((cell) => normalizeText(cell))) rows.push({ rowNumber, values: record });
  }
  return rows;
}


export function parseNetshopCsv(text: string): TabularRow[] {
  return parseDelimitedText(text, ",");
}


function decodeText(bytes: Uint8Array) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    try {
      return new TextDecoder("gb18030", { fatal: true }).decode(bytes);
    } catch (error) {
      throw new Error(`文本既不是有效 UTF-8，也无法按 GB18030 解码：${error instanceof Error ? error.message : "编码错误"}`);
    }
  }
}


function stripHtml(value: string) {
  return value
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/t[dh]>/gi, "\t")
    .replace(/<\/tr>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"');
}


function parseXlsx(bytes: Uint8Array, maxCompressedBytes = 25 * 1024 * 1024): ParsedTable {
  const sheet = parseXlsxFirstSheet(bytes, {
    maxCompressedBytes,
    maxUncompressedBytes: 100 * 1024 * 1024,
    maxWorksheetBytes: 75 * 1024 * 1024,
    maxRows: MAX_TABULAR_ROWS + 10,
  });
  if (sheet.maxColumns > MAX_TABULAR_COLUMNS) throw new Error(`表格超过 ${MAX_TABULAR_COLUMNS} 列上限`);
  return {
    sheetName: sheet.sheetName,
    rows: sheet.rows.map((row) => ({
      rowNumber: row.rowNumber,
      values: row.cells.map(cellValue),
    })),
  };
}


function parseLegacyXls(bytes: Uint8Array): ParsedTable {
  let workbook: XLSX.WorkBook;
  try {
    workbook = XLSX.read(bytes, {
      type: "array",
      cellDates: false,
      cellFormula: false,
      cellHTML: false,
      cellText: false,
      dense: true,
      sheetRows: MAX_TABULAR_ROWS + 10,
    });
  } catch (error) {
    throw new Error(`二进制 XLS 解析失败：${error instanceof Error ? error.message : "未知错误"}`);
  }
  if (workbook.SheetNames.length !== 1) throw new Error("生意参谋 XLS 必须且只能包含一个工作表");
  const sheetName = workbook.SheetNames[0];
  const matrix = XLSX.utils.sheet_to_json<Array<string | number | boolean | null>>(workbook.Sheets[sheetName], {
    header: 1,
    raw: true,
    defval: null,
    blankrows: false,
  });
  if (matrix.length > MAX_TABULAR_ROWS + 5) throw new Error(`表格超过 ${MAX_TABULAR_ROWS} 行上限`);
  const rows = matrix.map((values, index) => {
    if (values.length > MAX_TABULAR_COLUMNS) throw new Error(`第 ${index + 1} 行超过 ${MAX_TABULAR_COLUMNS} 列上限`);
    return { rowNumber: index + 1, values };
  });
  return { sheetName, rows };
}


function cellValue(cell: XlsxCellValue) {
  if (typeof cell !== "number") return cell;
  if (!Number.isFinite(cell)) return null;
  return cell;
}


function parseZip(bytes: Uint8Array, fileName: string, source: NetshopSource): ParsedTable {
  const archiveEntries: string[] = [];
  const zip = unzipSync(bytes, { filter(file) {
    if (file.name.endsWith("/")) return false;
    archiveEntries.push(file.name);
    if (file.originalSize > MAX_UNCOMPRESSED_ENTRY_BYTES) throw new Error("压缩包内文件解压后超过 25MB 上限");
    return /\.(xlsx|csv)$/i.test(file.name);
  } });
  const entries = Object.entries(zip).filter(([name]) => !name.endsWith("/") && /\.(xlsx|csv)$/i.test(name));
  if (source === "tmall_promotion" && (archiveEntries.length !== 1 || entries.length !== 1 || !/\.csv$/i.test(entries[0]?.[0] ?? ""))) {
    throw new Error("天猫推广 ZIP 必须且只能包含一个 CSV 文件");
  }
  if (entries.length !== 1) {
    throw new Error(`${fileName} 压缩包内没有可识别的 .xlsx 或 .csv 文件`);
  }
  const [innerName, innerBytes] = entries[0];
  const parsed = parseFile(innerBytes, innerName, source);
  return { ...parsed, archiveEntryName: innerName };
}


export function parseFile(bytes: Uint8Array, fileName: string, source: NetshopSource): ParsedTable {
  if (/\.zip$/i.test(fileName)) return parseZip(bytes, fileName, source);
  if (isXlsx(bytes) || /\.xlsx$/i.test(fileName)) {
    return parseXlsx(bytes, source === "tmall_product_assets" ? 64 * 1024 * 1024 : undefined);
  }
  if (/\.xls$/i.test(fileName) && source === "tmall_product_daily") return parseLegacyXls(bytes);
  const text = decodeText(bytes);
  if (/\.csv$/i.test(fileName)) return { sheetName: "csv", rows: parseNetshopCsv(text) };
  if (/\.xls$/i.test(fileName)) {
    const normalized = /<table|<tr|<td|<th/i.test(text) ? stripHtml(text) : text;
    const rows = parseDelimitedText(normalized, normalized.includes("\t") ? "\t" : ",");
    if (rows.length > 0) return { sheetName: "xls-text", rows };
  }
  throw new Error(`暂不支持该文件格式：${fileName}`);
}


function looksLikeHeader(values: readonly unknown[]) {
  const nonEmpty = values.map(normalizeHeader).filter(Boolean);
  if (nonEmpty.length < 2) return false;
  const joined = nonEmpty.join("|");
  return /日期|时间|SKU|SPU|商品|订单|会话|访客|库存|仓|金额|展现|点击|费用|销售|浏览|成交|编码|名称/i.test(joined);
}


function findHeader(table: ParsedTable): ParsedHeader {
  const index = table.rows.findIndex((row) => looksLikeHeader(row.values));
  if (index < 0) throw new Error("没有识别到表头行");
  const headers = table.rows[index].values.map((value, cellIndex) => normalizeHeader(value) || `列${cellIndex + 1}`);
  return { index, headers };
}


export function objectFromRow(headers: string[], values: readonly unknown[]) {
  const row: Record<string, string | number | boolean | null> = {};
  for (let index = 0; index < headers.length; index += 1) {
    const header = headers[index];
    const value = values[index] ?? null;
    if (value === null || value === "") row[header] = null;
    else row[header] = typeof value === "string" ? normalizeText(value) : value as number | boolean;
  }
  return row;
}


function normalizedTmallValue(value: unknown): string | number | boolean | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "boolean") return value;
  const text = normalizeText(value);
  return !text || text === "-" || text.toLowerCase() === "nan" ? null : text;
}


function tmallObjectFromRow(headers: readonly string[], values: readonly unknown[]) {
  return Object.fromEntries(headers.map((header, index) => [header, normalizedTmallValue(values[index])])) as Record<string, string | number | boolean | null>;
}


function canonicalTmallPromotionHeader(value: unknown) {
  const header = normalizeHeader(value);
  const key = normalizeKey(header);
  if (["日期", "数据日期", "统计日期"].includes(key)) return "日期";
  if (["主体id", "商品id", "宝贝id"].includes(key)) return "主体ID";
  if (["主体名称", "商品名称", "商品标题", "宝贝名称", "宝贝标题"].includes(key)) return "主体名称";
  if (["主体类型", "商品类型"].includes(key)) return "主体类型";
  return header;
}


function canonicalTmallPromotionRaw(raw: Record<string, string | number | boolean | null>) {
  const output = { ...raw };
  output["日期"] = normalizedTmallValue(output["日期"] ?? output["数据日期"] ?? output["统计日期"]);
  output["主体ID"] = normalizedTmallValue(output["主体ID"] ?? output["商品ID"] ?? output["宝贝ID"]);
  output["主体名称"] = normalizedTmallValue(output["主体名称"] ?? output["商品名称"] ?? output["商品标题"] ?? output["宝贝名称"] ?? output["宝贝标题"]);
  output["主体类型"] = normalizedTmallValue(output["主体类型"] ?? (output["主体ID"] ? "商品" : null));
  return output;
}


const tmallMasterCanonicalRawHeaders = [
  "商品ID", "类目ID", "类目名称", "商品标题", "一口价", "导购标题", "商品商家编码", "商品发货时间",
  "最长发货时间", "销售属性", "属性对", "SKU发货时间", "SKUID", "SKU价格", "SKU库存", "SKU商家编码",
  "生产日期", "保质期",
] as const;


function tmallMasterCanonicalHeaders(values: readonly unknown[]) {
  const headers = values.map(normalizeHeader);
  const normalized = headers.map(normalizeKey);
  const skuIndex = normalized.findIndex((header) => header === "skuid");
  const salesAttributeIndex = normalized.findIndex((header) => header === "销售属性");
  const attributePairIndex = normalized.findIndex((header) => header === "属性对");
  if (normalized[0] !== "商品id" || !normalized.includes("商品标题") || !normalized.includes("一口价")) {
    throw new Error("天猫货品文件缺少商品Id、商品标题或一口价表头");
  }

  return headers.map((header, index) => {
    const key = normalized[index];
    if (key === "商品id") return "商品ID";
    if (key === "类目id") return "类目ID";
    if (key === "类目名称") return "类目名称";
    if (key === "商品标题") return "商品标题";
    if (key === "一口价") return "一口价";
    if (key === "导购标题") return "导购标题";
    if (key === "商家编码") return skuIndex < 0 || index < skuIndex ? "商品商家编码" : "SKU商家编码";
    if (key === "商品商家编码") return "商品商家编码";
    if (key === "sku商家编码") return "SKU商家编码";
    if (key === "发货时间") {
      if (skuIndex < 0) return "商品发货时间";
      if (salesAttributeIndex >= 0 && index < salesAttributeIndex) return "商品发货时间";
      if (attributePairIndex >= 0 && index > attributePairIndex && index < skuIndex) return "SKU发货时间";
      if (salesAttributeIndex >= 0 && index > salesAttributeIndex && index < skuIndex) return "SKU发货时间";
      if (attributePairIndex >= 0 && index < attributePairIndex) return "商品发货时间";
      throw new Error(`天猫货品文件第 ${index + 1} 列“发货时间”无法判定为商品或 SKU 字段`);
    }
    if (key === "商品发货时间") return "商品发货时间";
    if (key === "sku发货时间") return "SKU发货时间";
    if (key === "最长发货时间") return "最长发货时间";
    if (key === "销售属性") return "销售属性";
    if (key === "属性对") return "属性对";
    if (key === "skuid") return "SKUID";
    if (key === "价格元" || key === "sku价格") return "SKU价格";
    if (key === "数量" || key === "sku库存") return "SKU库存";
    if (key.startsWith("生产日期")) return "生产日期";
    if (key.startsWith("保质期")) return "保质期";
    return header || `列${index + 1}`;
  });
}


const tmallDirectPromotionIdentityHeaders = [
  "场景ID", "计划ID", "主体ID", "主体类型", "主体名称", "展现量", "点击量", "花费",
] as const;


function tmallPromotionArchiveDateFallback(
  table: ParsedTable,
  expectedStartDate?: string,
  expectedEndDate?: string,
) {
  const startDate = isoDateFromValue(expectedStartDate);
  const endDate = isoDateFromValue(expectedEndDate);
  if (!startDate || startDate !== endDate || !table.archiveEntryName) return "";
  const dateTokens = table.archiveEntryName.match(/20\d{6}/g) ?? [];
  if (dateTokens.length !== 1) return "";
  return isoDateFromValue(dateTokens[0]) === startDate ? startDate : "";
}


export function findTmallHeader(
  source: NetshopSource,
  table: ParsedTable,
  expectedStartDate?: string,
  expectedEndDate?: string,
): ParsedHeader {
  if (source === "tmall_product_master") {
    if (table.sheetName !== "发布模板") throw new Error("天猫货品文件首个工作表必须为“发布模板”");
    const index = table.rows.findIndex((row) => normalizeKey(normalizeHeader(row.values[0])) === "商品id"
      && row.values.some((value) => normalizeKey(normalizeHeader(value)) === "商品标题")
      && row.values.some((value) => normalizeKey(normalizeHeader(value)) === "一口价"));
    if (index < 0) throw new Error("天猫货品文件缺少商品Id、商品标题或一口价表头");
    return { index, headers: tmallMasterCanonicalHeaders(table.rows[index].values) };
  }
  if (source === "tmall_product_assets") {
    if (table.sheetName !== "商品图") throw new Error("天猫 SPU 商品图文件首个工作表必须为“商品图”");
    const expected = ["主图", "ID", "标题", "商品价格", "付款人数", "店铺", "链接"];
    const index = table.rows.findIndex((row) => {
      const headers = row.values.map(normalizeHeader);
      return expected.every((header, headerIndex) => headers[headerIndex] === header);
    });
    if (index < 0) throw new Error(`天猫 SPU 商品图文件缺少标准表头：${expected.join("、")}`);
    return { index, headers: expected };
  }
  if (source === "tmall_product_daily") {
    const index = table.rows.findIndex((row) => normalizeHeader(row.values[0]) === "统计日期" && normalizeHeader(row.values[1]) === "商品ID" && row.values.map(normalizeHeader).includes("支付金额"));
    if (index < 0) throw new Error("生意参谋商品文件缺少统计日期、商品ID或支付金额表头");
    return { index, headers: table.rows[index].values.map((value, cellIndex) => normalizeHeader(value) || `列${cellIndex + 1}`) };
  }
  if (source === "tmall_promotion") {
    const fallbackDate = tmallPromotionArchiveDateFallback(table, expectedStartDate, expectedEndDate);
    const index = table.rows.findIndex((row) => {
      const headers = row.values.map(canonicalTmallPromotionHeader);
      const hasRequiredFacts = headers.includes("主体ID") && headers.includes("花费");
      const isDirectSingleDayReport = Boolean(fallbackDate)
        && tmallDirectPromotionIdentityHeaders.every((header) => headers.includes(header));
      return hasRequiredFacts && (headers.includes("日期") || isDirectSingleDayReport);
    });
    if (index < 0) throw new Error("天猫商品推广报表缺少日期、商品ID（或主体ID）或花费表头");
    const headers = table.rows[index].values.map((value, cellIndex) => canonicalTmallPromotionHeader(value) || `列${cellIndex + 1}`);
    return {
      index,
      headers,
      ...(headers.includes("日期") ? {} : { businessDateFallback: fallbackDate }),
    };
  }
  return findHeader(table);
}


export function tmallRowObject(
  source: NetshopSource,
  headers: readonly string[],
  values: readonly unknown[],
  businessDateFallback = "",
) {
  const raw = tmallObjectFromRow(headers, values);
  if (source === "tmall_product_master") {
    for (const header of tmallMasterCanonicalRawHeaders) {
      if (!(header in raw)) raw[header] = null;
    }
    raw["商品名称"] = raw["商品标题"];
    raw["商品编码"] = raw["商品商家编码"];
    raw["商家SKU"] = raw["SKU商家编码"];
    raw["商品总库存"] = raw["SKU库存"];
    raw["商品可用库存"] = raw["SKU库存"];
  }
  if (source === "tmall_product_assets") {
    return {
      "商品ID": raw["ID"],
      "商品名称": raw["标题"],
      "店铺": raw["店铺"],
      "商品链接": raw["链接"],
    };
  }
  if (source === "tmall_promotion") {
    const output = canonicalTmallPromotionRaw(raw);
    if (!normalizeText(output["日期"]) && businessDateFallback) output["日期"] = businessDateFallback;
    return output;
  }
  return raw;
}


function moneyCents(value: unknown) {
  const numeric = numberFromUnknown(value);
  return numeric === null ? null : Math.round(numeric * 100);
}


function rateFromUnknown(value: unknown) {
  if (value === null || value === undefined) return null;
  const text = normalizeText(value);
  const numeric = numberFromUnknown(value);
  if (numeric === null) return null;
  return text.includes("%") ? numeric / 100 : numeric;
}


export function tmallMetrics(source: NetshopSource, raw: Record<string, string | number | boolean | null>): Record<string, number | string | null> {
  if (source === "tmall_product_assets") return {};
  if (source === "tmall_product_master") {
    return {
      itemPriceCents: moneyCents(raw["一口价"]),
      skuPriceCents: moneyCents(raw["SKU价格"]),
      inventoryQuantity: numberFromUnknown(raw["SKU库存"]),
    };
  }
  if (source === "tmall_product_daily") {
    return {
      pageViews: numberFromUnknown(raw["商品浏览量"]),
      visitors: numberFromUnknown(raw["商品访客数"]),
      averageStaySeconds: numberFromUnknown(raw["平均停留时长"]),
      bounceRate: rateFromUnknown(raw["商品详情页跳出率"]),
      favorites: numberFromUnknown(raw["商品收藏人数"]),
      addCartQuantity: numberFromUnknown(raw["商品加购件数"]),
      addCartCustomers: numberFromUnknown(raw["商品加购人数"]),
      orderCustomers: numberFromUnknown(raw["下单买家数"]),
      orderQuantity: numberFromUnknown(raw["下单件数"]),
      orderAmountCents: moneyCents(raw["下单金额"]),
      orderConversionRate: rateFromUnknown(raw["下单转化率"]),
      transactionCustomers: numberFromUnknown(raw["支付买家数"]),
      transactionQuantity: numberFromUnknown(raw["支付件数"]),
      transactionAmountCents: moneyCents(raw["支付金额"]),
      conversionRate: rateFromUnknown(raw["商品支付转化率"]),
      newTransactionCustomers: numberFromUnknown(raw["支付新买家数"]),
      repeatTransactionCustomers: numberFromUnknown(raw["支付老买家数"]),
      repeatTransactionAmountCents: moneyCents(raw["老买家支付金额"]),
      juhuasuanTransactionAmountCents: moneyCents(raw["聚划算支付金额"]),
      uvValueCents: moneyCents(raw["访客平均价值"]),
      refundAmountCents: moneyCents(raw["成功退款金额"]),
      yearTransactionAmountCents: moneyCents(raw["年累计支付金额"]),
      monthTransactionAmountCents: moneyCents(raw["月累计支付金额"]),
      monthTransactionQuantity: numberFromUnknown(raw["月累计支付件数"]),
      searchConversionRate: rateFromUnknown(raw["搜索引导支付转化率"]),
      searchVisitors: numberFromUnknown(raw["搜索引导访客数"]),
      searchTransactionCustomers: numberFromUnknown(raw["搜索引导支付买家数"]),
    };
  }
  return {
    spendCents: moneyCents(raw["花费"]),
    netTransactionAmountCents: moneyCents(raw["净成交金额"]),
    grossTransactionAmountCents: moneyCents(raw["总成交金额"]),
    reportedNetRoas: numberFromUnknown(raw["净实际投产比"]),
    reportedGrossRoas: numberFromUnknown(raw["实际投产比"]),
    impressions: numberFromUnknown(raw["展现量"]),
    clicks: numberFromUnknown(raw["点击量"]),
    clickThroughRate: rateFromUnknown(raw["点击率"]),
    averageClickCostCents: moneyCents(raw["平均点击花费"]),
    cpmCents: moneyCents(raw["千次展现花费"]),
    netOrders: numberFromUnknown(raw["净成交笔数"]),
    grossOrders: numberFromUnknown(raw["总成交笔数"]),
    clickConversionRate: rateFromUnknown(raw["点击转化率"]),
    directNetTransactionAmountCents: moneyCents(raw["直接净成交金额"]),
    directNetOrders: numberFromUnknown(raw["直接净成交笔数"]),
    cartQuantity: numberFromUnknown(raw["总购物车数"]),
    directCartQuantity: numberFromUnknown(raw["直接购物车数"]),
    favorites: numberFromUnknown(raw["收藏宝贝数"]),
    directFavorites: numberFromUnknown(raw["直接收藏宝贝数"]),
    favoriteAndCartQuantity: numberFromUnknown(raw["宝贝收藏加购数"]),
  };
}


function numberFromUnknown(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const text = normalizeText(value).replace(/[,￥¥%]/g, "");
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}


function excelDateToIso(serial: number, date1904 = false) {
  if (!Number.isFinite(serial) || serial < 1 || serial > 100000) return null;
  const epoch = Date.UTC(date1904 ? 1904 : 1899, date1904 ? 0 : 11, date1904 ? 1 : 30);
  const date = new Date(epoch + Math.trunc(serial) * 86400000);
  return date.toISOString().slice(0, 10);
}


export function isoDateFromValue(value: unknown) {
  if (typeof value === "number") return excelDateToIso(value) ?? "";
  const text = normalizeText(value);
  if (!text) return "";
  const compact = /(?:^|\D)(20\d{2})(\d{2})(\d{2})(?:\D|$)/.exec(text);
  const separated = /(?:^|\D)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:\D|$)/.exec(text);
  const match = compact ?? separated;
  if (!match) return "";
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return "";
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}


export function fileDate(fileName: string) {
  return isoDateFromValue(fileName);
}


export function findValue(row: Record<string, unknown>, patterns: RegExp[]) {
  for (const [key, value] of Object.entries(row)) {
    const normalized = normalizeKey(key);
    if (patterns.some((pattern) => pattern.test(key) || pattern.test(normalized))) return value;
  }
  return null;
}


export function firstDate(row: Record<string, unknown>) {
  const direct = findValue(row, [/日期/, /时间/, /date/i, /day/i]);
  const date = isoDateFromValue(direct);
  if (date) return date;
  for (const value of Object.values(row)) {
    const fallback = isoDateFromValue(value);
    if (fallback) return fallback;
  }
  return "";
}


export function isDailyAggregateRow(source: NetshopSource, row: Record<string, unknown>) {
  if (source !== "jd_sku_daily") return false;
  return normalizeText(findValue(row, [/^sku$/i, /^spu$/i])) === "合计";
}


type ParsedNetshopRawRow = {
  rowNumber: number;
  raw: Record<string, string | number | boolean | null>;
};


const tmallPromotionAdditiveHeaders = new Set([
  "花费",
  "净成交金额",
  "总成交金额",
  "直接净成交金额",
  "直接成交金额",
  "展现量",
  "点击量",
  "净成交笔数",
  "总成交笔数",
  "直接净成交笔数",
  "直接成交笔数",
  "总购物车数",
  "直接购物车数",
  "收藏宝贝数",
  "直接收藏宝贝数",
  "宝贝收藏加购数",
  "宝贝直接收藏加购数",
]);


function sumTmallPromotionHeader(rows: readonly ParsedNetshopRawRow[], header: string) {
  const values = rows.map((row) => numberFromUnknown(row.raw[header])).filter((value): value is number => value !== null);
  return values.length > 0 ? values.reduce((sum, value) => sum + value, 0) : null;
}


function safeRatio(numerator: number | null, denominator: number | null, multiplier = 1) {
  return numerator !== null && denominator !== null && denominator !== 0 ? numerator / denominator * multiplier : null;
}


/**
 * 阿里妈妈“商品报表”在选择“商品、计划”维度后，同一商品同一天会出现多条计划行。
 * 运营系统的 promotion_daily 自然键仍是“店铺 + 日期 + 商品”，因此必须先把计划行
 * 汇总为唯一商品日事实，不能依赖数据库冲突覆盖，也不能丢掉某个计划的指标。
 */
export function prepareTmallPromotionRows(rows: readonly ParsedNetshopRawRow[]) {
  const groups = new Map<string, ParsedNetshopRawRow[]>();
  for (const row of rows) {
    const date = normalizeText(row.raw["日期"]);
    const subjectId = normalizeText(row.raw["主体ID"]);
    const key = JSON.stringify([date, subjectId]);
    const group = groups.get(key) ?? [];
    group.push(row);
    groups.set(key, group);
  }

  return [...groups.values()].map((group) => {
    const first = group[0]!;
    if (group.length === 1) return first;
    const subjectNames = [...new Set(group.map((row) => normalizeText(row.raw["主体名称"])).filter(Boolean))].sort();
    const subjectTypes = [...new Set(group.map((row) => normalizeText(row.raw["主体类型"])).filter(Boolean))];
    if (!/^\d+$/.test(normalizeText(first.raw["主体ID"]))) throw new Error("天猫商品报表缺少有效商品 ID，拒绝汇总");
    if (subjectTypes.some((value) => value !== "商品")) throw new Error("天猫商品报表计划维度包含非商品主体，拒绝汇总");

    const raw = { ...first.raw };
    // Plan-level titles can differ for one product ID. Keep every title as
    // provenance and select a stable display title independent of row order.
    if (subjectNames.length > 1) {
      raw["主体名称"] = subjectNames[0]!;
      raw["主体名称列表"] = JSON.stringify(subjectNames);
    }
    for (const header of tmallPromotionAdditiveHeaders) raw[header] = sumTmallPromotionHeader(group, header);
    const spend = numberFromUnknown(raw["花费"]);
    const impressions = numberFromUnknown(raw["展现量"]);
    const clicks = numberFromUnknown(raw["点击量"]);
    const netAmount = numberFromUnknown(raw["净成交金额"]);
    const grossAmount = numberFromUnknown(raw["总成交金额"]);
    const grossOrders = numberFromUnknown(raw["总成交笔数"]);
    const directNetAmount = numberFromUnknown(raw["直接净成交金额"]);
    raw["点击率"] = safeRatio(clicks, impressions);
    raw["平均点击花费"] = safeRatio(spend, clicks);
    raw["千次展现花费"] = safeRatio(spend, impressions, 1000);
    raw["净实际投产比"] = safeRatio(netAmount, spend);
    raw["实际投产比"] = safeRatio(grossAmount, spend);
    raw["点击转化率"] = safeRatio(grossOrders, clicks);
    raw["直接净实际投产比"] = safeRatio(directNetAmount, spend);

    const plans = [...new Set(group.map((row) => {
      const id = normalizeText(row.raw["计划ID"]);
      const name = normalizeText(row.raw["计划名称"] ?? row.raw["计划"]);
      return [id, name].filter(Boolean).join(":");
    }).filter(Boolean))].sort();
    raw["报表维度"] = "商品,计划";
    raw["计划明细行数"] = group.length;
    raw["计划数量"] = plans.length || group.length;
    raw["计划列表"] = plans.join("|") || null;
    raw["计划ID"] = null;
    raw["计划名称"] = null;
    raw["计划"] = null;
    return { rowNumber: Math.min(...group.map((row) => row.rowNumber)), raw };
  });
}


export function prepareTmallProductAssetRows(
  rows: readonly ParsedNetshopRawRow[],
  images: ReadonlyMap<number, TmallProductAssetImage> | undefined,
  canonicalShopName: string,
) {
  const maxIssues = 200;
  const errors: NetshopImportIssue[] = [];
  let issuesTruncated = false;
  const addIssue = (issue: NetshopImportIssue) => {
    if (errors.length < maxIssues - 1) errors.push(issue);
    else issuesTruncated = true;
  };
  const sourceRows = new Set(rows.map((row) => row.rowNumber));
  const seenSpuIds = new Set<string>();
  const workbookShopName = canonicalShopName.replace(/^天猫-/, "");

  for (const row of rows) {
    const spuId = normalizeText(row.raw["商品ID"]);
    const productName = normalizeText(row.raw["商品名称"]);
    const sourceShopName = normalizeText(row.raw["店铺"]);
    const rawProductUrl = normalizeText(row.raw["商品链接"]);
    const image = images?.get(row.rowNumber);

    if (!/^\d+$/.test(spuId)) {
      addIssue({ row: row.rowNumber, field: "ID", code: "INVALID_SPU_ID", message: "SPUID 必须为纯数字" });
    } else if (seenSpuIds.has(spuId)) {
      addIssue({ row: row.rowNumber, field: "ID", code: "DUPLICATE_SPU_ID", message: `文件内存在重复 SPUID：${spuId}` });
    } else {
      seenSpuIds.add(spuId);
    }
    if (!productName) addIssue({ row: row.rowNumber, field: "标题", code: "MISSING_PRODUCT_NAME", message: "商品标题不能为空" });
    if (sourceShopName !== canonicalShopName && sourceShopName !== workbookShopName) {
      addIssue({ row: row.rowNumber, field: "店铺", code: "TMALL_ASSET_SHOP_MISMATCH", message: `文件店铺必须为 ${workbookShopName}` });
    }

    let normalizedProductUrl = "";
    try {
      const parsedUrl = new URL(rawProductUrl);
      const linkedIds = parsedUrl.searchParams.getAll("id");
      if (parsedUrl.protocol !== "https:"
        || !["item.taobao.com", "detail.tmall.com"].includes(parsedUrl.hostname.toLowerCase())
        || parsedUrl.username || parsedUrl.password || parsedUrl.port
        || linkedIds.length !== 1 || linkedIds[0] !== spuId) {
        throw new Error("invalid product URL");
      }
      normalizedProductUrl = parsedUrl.toString();
    } catch {
      addIssue({ row: row.rowNumber, field: "链接", code: "INVALID_PRODUCT_URL", message: "商品链接必须为与 SPUID 一致的淘宝或天猫 HTTPS 商品链接" });
    }

    if (!image) {
      addIssue({ row: row.rowNumber, field: "主图", code: "MISSING_PRODUCT_IMAGE", message: "该 SPUID 缺少锚定在主图列的内嵌图片" });
    }

    row.raw["商品ID"] = spuId;
    row.raw["商品名称"] = productName;
    row.raw["店铺"] = canonicalShopName;
    row.raw["商品链接"] = normalizedProductUrl || rawProductUrl;
    if (image) Object.assign(row.raw, productAssetMetadata(image));
  }

  for (const rowNumber of images?.keys() ?? []) {
    if (!sourceRows.has(rowNumber)) {
      addIssue({ row: rowNumber, field: "主图", code: "ORPHAN_PRODUCT_IMAGE", message: "内嵌图片没有对应的商品数据行" });
    }
  }
  if (issuesTruncated) {
    errors.push({ code: "TOO_MANY_VALIDATION_ERRORS", message: `校验问题超过 ${maxIssues} 条，仅返回前 ${maxIssues - 1} 条` });
  }
  return errors;
}


export function detectDataset(source: NetshopSource, fileName: string, headers: readonly string[]) {
  const haystack = `${fileName} ${headers.join(" ")}`;
  if (source === "jd_shop_overview") return "trade_overview";
  if (source === "jd_promotion") return "ad";
  if (source === "jd_b2b") return "b2b";
  if (source === "jd_product_master") return "product_master";
  if (source === "tmall_product_master") return "product_master";
  if (source === "tmall_product_assets") return "spu_assets";
  if (source === "tmall_product_daily") return "spu_daily";
  if (source === "tmall_promotion") return "promotion_daily";
  if (source === "jd_yimei_sku") return "yimei_sku";
  if (source === "inv_selfop") return "inv_selfop";
  if (source === "jd_cs") {
    if (/suggest|推荐订单/i.test(haystack)) return "cs_suggest";
    if (/促成订单|订单编号|下单时间/i.test(haystack)) return "cs_order";
    if (/会话|咨询|接待|聊天|客服/i.test(haystack)) return "cs_chat";
    return "cs";
  }
  if (source === "jd_sku_daily") {
    return detectJdDailyDataset(headers);
  }
  return source;
}


export function warehouseType(fileName: string, row: Record<string, unknown>) {
  const text = `${fileName} ${Object.values(row).map(normalizeText).join(" ")}`;
  if (/配送中心/.test(text)) return "配送中心";
  if (/\bRDC\b/i.test(text)) return "RDC";
  return "";
}


export function usesSnapshotDate(source: NetshopSource) {
  return source === "inv_selfop" || source === "jd_product_master" || source === "tmall_product_master" || source === "tmall_product_assets";
}


export function isTmallSource(source: NetshopSource) {
  return source === "tmall_product_master" || source === "tmall_product_assets" || source === "tmall_product_daily" || source === "tmall_promotion";
}


export function tmallMasterRowKey(input: {
  dataset: string;
  platform: string;
  shopName: string;
  snapshotDate: string;
  spuId: string;
  skuId: string;
  saleAttribute: string;
  rowNumber: number;
}) {
  const dimension = input.skuId || `${input.saleAttribute}|row:${input.rowNumber}`;
  return JSON.stringify([input.dataset, input.platform, input.shopName, input.snapshotDate, input.spuId, dimension]);
}


export function tmallProductAssetRowKey(input: {
  dataset: string;
  platform: string;
  shopName: string;
  snapshotDate: string;
  spuId: string;
}) {
  return JSON.stringify([input.dataset, input.platform, input.shopName, input.snapshotDate, input.spuId]);
}


export function metricsFromRow(row: Record<string, string | number | boolean | null>) {
  const metrics: Record<string, number | string | null> = {};
  for (const [key, value] of Object.entries(row)) {
    const numeric = numberFromUnknown(value);
    if (numeric !== null) metrics[key] = numeric;
    else if (typeof value === "string" && value.length <= 80) metrics[key] = value;
  }
  return metrics;
}


export async function hashText(value: string) {
  return toHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
}


export function validateRows(rows: readonly NetshopRowInput[]) {
  const errors: NetshopImportIssue[] = [];
  if (rows.length === 0) errors.push({ code: "NO_DATA_ROWS", message: "文件没有可导入的数据行" });
  const keys = new Set<string>();
  for (const row of rows) {
    if (!row.sourceRowKey) errors.push({ row: row.sourceRowNumber, code: "MISSING_ROW_KEY", message: "缺少行唯一标识" });
    else if (keys.has(row.sourceRowKey)) errors.push({ row: row.sourceRowNumber, code: "DUPLICATE_ROW_KEY", message: "文件内存在重复行" });
    else keys.add(row.sourceRowKey);
    if (!row.dataset) errors.push({ row: row.sourceRowNumber, code: "MISSING_DATASET", message: "未识别数据集" });
    if ((row.dataset === "sku_daily" || row.dataset === "spu_daily" || row.dataset === "promotion_daily") && !row.businessDate) {
      errors.push({ row: row.sourceRowNumber, field: "时间", code: "MISSING_BUSINESS_DATE", message: "分天数据缺少有效日期" });
    }
    if (row.dataset === "sku_daily" && !row.skuId) errors.push({ row: row.sourceRowNumber, field: "SKU", code: "MISSING_SKU_ID", message: "SKU 分天数据缺少 SKU" });
    if (row.dataset === "spu_daily" && !row.spuId) errors.push({ row: row.sourceRowNumber, field: "SPU", code: "MISSING_SPU_ID", message: "SPU 分天数据缺少 SPU" });
    if (row.dataset === "spu_assets" && !row.spuId) errors.push({ row: row.sourceRowNumber, field: "ID", code: "MISSING_SPU_ID", message: "SPU 商品图数据缺少 SPUID" });
    if (row.dataset === "promotion_daily" && !row.spuId) errors.push({ row: row.sourceRowNumber, field: "主体ID", code: "MISSING_SUBJECT_ID", message: "推广分天数据缺少主体ID" });
    if (errors.length >= 200) break;
  }
  return errors;
}


export function netshopBusinessContentRows(rows: readonly NetshopRowInput[]) {
  return rows.map((row) => ({
    source: row.source,
    dataset: row.dataset,
    platform: row.platform,
    shopName: row.shopName,
    businessDate: row.businessDate,
    snapshotDate: row.snapshotDate,
    productCode: row.productCode,
    productName: row.productName,
    skuId: row.skuId,
    spuId: row.spuId,
    warehouseType: row.warehouseType,
    metrics: row.metrics,
    raw: row.raw,
  }));
}


export type NetshopImportInput = {
  bytes: Uint8Array;
  fileName: string;
  fileSizeBytes: number;
  source: NetshopSource;
  platform?: string;
  shopName?: string;
  note?: string;
  snapshotDate?: string;
  expectedDataset?: "sku_daily" | "spu_daily";
  expectedStartDate?: string;
  expectedEndDate?: string;
};


function buildNetshopRows(input: NetshopImportInput, context: {
  parsed: ParsedTable;
  header: ParsedHeader;
  dataset: string;
  platform: string;
  shopName: string;
  fileHash: string;
  snapshotDate: string;
  assetImages?: ReadonlyMap<number, TmallProductAssetImage>;
}) {
  const snapshotSource = usesSnapshotDate(input.source);
  const parsedRawRows = context.parsed.rows.slice(context.header.index + 1)
    .map((row) => ({
      rowNumber: row.rowNumber,
      raw: isTmallSource(input.source)
        ? tmallRowObject(
            input.source,
            context.header.headers,
            row.values,
            context.header.businessDateFallback,
          )
        : objectFromRow(context.header.headers, row.values),
    }))
    .filter((row) => Object.values(row.raw).some((value) => normalizeText(value)))
    .filter((row) => !isDailyAggregateRow(input.source, row.raw));
  const rawRows = input.source === "tmall_promotion" ? prepareTmallPromotionRows(parsedRawRows) : parsedRawRows;
  if (rawRows.length > MAX_TABULAR_ROWS) throw new Error(`单次最多导入 ${MAX_TABULAR_ROWS} 行`);
  const sourceErrors = input.source === "tmall_product_assets"
    ? prepareTmallProductAssetRows(rawRows, context.assetImages, context.shopName)
    : [];

  const rows: NetshopRowInput[] = [];
  if (sourceErrors.length > 0) return Promise.resolve({ rawRows, rows, sourceErrors });
  return (async () => {
    for (const row of rawRows) {
      const isProductMaster = input.source === "jd_product_master" || input.source === "tmall_product_master" || input.source === "tmall_product_assets";
      const businessDate = isProductMaster ? "" : firstDate(row.raw);
      const tmallSpuId = input.source === "tmall_product_master" || input.source === "tmall_product_assets"
        ? normalizeText(row.raw["商品ID"])
        : input.source === "tmall_product_daily"
          ? normalizeText(row.raw["商品ID"])
          : input.source === "tmall_promotion"
            ? normalizeText(row.raw["主体ID"])
            : "";
      const productCode = input.source === "tmall_product_assets"
        ? ""
        : input.source === "tmall_product_master"
        ? normalizeText(row.raw["SKU商家编码"] ?? row.raw["商品商家编码"])
        : input.source === "tmall_product_daily"
          ? normalizeText(row.raw["货号"])
          : input.source === "jd_product_master"
            ? normalizeText(row.raw["商品编码"])
            : normalizeText(findValue(row.raw, [/货品编号/, /商品编码/, /商品编号/, /商品ID/i, /^sku$/i, /skuid/i]));
      const skuId = input.source === "tmall_product_master"
        ? normalizeText(row.raw["SKUID"])
        : normalizeText(findValue(row.raw, [/^sku$/i, /sku.?id/i, /skuid/i, /SKU编码/i]));
      const spuId = tmallSpuId || (input.source === "jd_product_master"
        ? productCode
        : normalizeText(findValue(row.raw, [/^spu$/i, /spu.?id/i, /spuid/i, /SPU编码/i])));
      const productName = input.source === "tmall_product_assets"
        ? normalizeText(row.raw["商品名称"])
        : input.source === "tmall_product_master"
        ? normalizeText(row.raw["商品标题"])
        : input.source === "tmall_promotion"
          ? normalizeText(row.raw["主体名称"])
          : normalizeText(findValue(row.raw, [/商品名称/, /货品名称/, /产品名称/, /名称/]));
      const rawJson = JSON.stringify(row.raw);
      const rowHash = await hashText(rawJson);
      const sourceRowKey = input.source === "tmall_product_assets"
        ? tmallProductAssetRowKey({
            dataset: context.dataset,
            platform: context.platform,
            shopName: context.shopName,
            snapshotDate: context.snapshotDate,
            spuId,
          })
        : input.source === "tmall_product_master"
        ? tmallMasterRowKey({
            dataset: context.dataset,
            platform: context.platform,
            shopName: context.shopName,
            snapshotDate: context.snapshotDate,
            spuId,
            skuId,
            saleAttribute: normalizeText(row.raw["销售属性"]),
            rowNumber: row.rowNumber,
          })
        : context.dataset === "sku_daily" || context.dataset === "spu_daily" || context.dataset === "promotion_daily"
          ? dailyRowKey(context.dataset, context.platform, context.shopName, businessDate, context.dataset === "sku_daily" ? skuId : spuId)
          : netshopMasterRowKey({ source: input.source, platform: context.platform, shopName: context.shopName, fileHash: context.fileHash, rowNumber: row.rowNumber, rowHash });
      rows.push({
        sourceRowNumber: row.rowNumber,
        sourceRowKey,
        sourceRowHash: rowHash,
        source: input.source,
        dataset: context.dataset,
        platform: context.platform,
        shopName: context.shopName,
        businessDate,
        snapshotDate: snapshotSource ? context.snapshotDate : "",
        productCode,
        productName,
        skuId,
        spuId,
        warehouseType: input.source === "inv_selfop" ? warehouseType(input.fileName, row.raw) : "",
        metrics: isTmallSource(input.source) ? tmallMetrics(input.source, row.raw) : metricsFromRow(row.raw),
        raw: row.raw,
      });
    }
    return { rawRows, rows, snapshotSource, sourceErrors };
  })();
}


export async function inspectTmallImportBytes(input: NetshopImportInput) {
  if (!isTmallSource(input.source)) throw new Error("样本检查器仅接受天猫来源");
  if (input.source === "tmall_product_master" && !/\.xlsx$/i.test(input.fileName)) throw new Error("天猫货品主数据只接受 .xlsx 文件");
  if (input.source === "tmall_product_assets" && !/\.xlsx$/i.test(input.fileName)) throw new Error("天猫 SPU 商品图只接受 .xlsx 文件");
  if (input.source === "tmall_product_daily" && !/\.xls$/i.test(input.fileName)) throw new Error("生意参谋商品日数据只接受二进制 .xls 文件");
  if (input.source === "tmall_promotion" && !/\.zip$/i.test(input.fileName)) throw new Error("天猫推广数据只接受包含单个 CSV 的 .zip 文件");
  const fileHash = toHex(await sha256(input.bytes));
  const parsed = parseFile(input.bytes, input.fileName, input.source);
  const assetImages = input.source === "tmall_product_assets"
    ? await extractTmallProductAssetImages(input.bytes)
    : undefined;
  const header = findTmallHeader(input.source, parsed, input.expectedStartDate, input.expectedEndDate);
  const dataset = detectDataset(input.source, input.fileName, header.headers);
  const snapshotDate = usesSnapshotDate(input.source) ? isoDateFromValue(input.snapshotDate) : "";
  if (usesSnapshotDate(input.source) && !snapshotDate) throw new Error("天猫快照数据必须提供有效 snapshot_date=YYYY-MM-DD");
  const tmallStore = resolveEnabledTmallShop(input.shopName);
  const built = await buildNetshopRows(input, {
    parsed,
    header,
    dataset,
    platform: TMALL_PLATFORM,
    shopName: tmallStore.shopName,
    fileHash,
    snapshotDate,
    assetImages,
  });
  const errors = built.sourceErrors.length > 0 ? built.sourceErrors : validateRows(built.rows);
  if (input.source === "tmall_promotion") {
    for (const row of built.rawRows) {
      if (normalizeText(row.raw["主体类型"]) !== "商品") {
        errors.push({ row: row.rowNumber, field: "主体类型", code: "UNSUPPORTED_SUBJECT_TYPE", message: "推广导入当前只接受主体类型为“商品”的数据" });
      }
    }
  }
  if (input.source === "tmall_product_daily" || input.source === "tmall_promotion") {
    const coverage = dailyDateCoverage(input.expectedStartDate, input.expectedEndDate, built.rows.map((row) => row.businessDate));
    if (!coverage.validRange) errors.push({ code: "MISSING_EXPECTED_DATE_RANGE", message: "分天导入必须提供有效的目标起止日期" });
    else {
      if (coverage.missingDates.length) errors.push({ code: "MISSING_EXPECTED_DATES", message: `目标区间缺少日期：${coverage.missingDates.join(", ")}` });
      if (coverage.outOfRangeDates.length) errors.push({ code: "OUT_OF_RANGE_DATES", message: `文件包含目标区间外日期：${coverage.outOfRangeDates.join(", ")}` });
    }
  }
  const sumMetric = (key: string) => built.rows.reduce((sum, row) => sum + Number(row.metrics[key] ?? 0), 0);
  const dates = built.rows.map((row) => row.businessDate).filter(Boolean).sort();
  return {
    dataset,
    platform: TMALL_PLATFORM,
    shopName: tmallStore.shopName,
    sheetName: parsed.sheetName,
    rows: built.rows,
    errors,
    totals: {
      rowCount: built.rows.length,
      uniqueProductCount: new Set(built.rows.map((row) => row.spuId).filter(Boolean)).size,
      uniqueSkuCount: new Set(built.rows.map((row) => row.skuId).filter(Boolean)).size,
      missingSkuCount: built.rows.filter((row) => !row.skuId).length,
      inventoryQuantity: sumMetric("inventoryQuantity"),
      transactionAmountCents: sumMetric("transactionAmountCents"),
      refundAmountCents: sumMetric("refundAmountCents"),
      spendCents: sumMetric("spendCents"),
      netTransactionAmountCents: sumMetric("netTransactionAmountCents"),
      impressions: sumMetric("impressions"),
      clicks: sumMetric("clicks"),
      netOrders: sumMetric("netOrders"),
      dateMin: dates[0] ?? null,
      dateMax: dates[dates.length - 1] ?? null,
      ...(assetImages ? { imageCount: assetImages.size } : {}),
    },
  };
}


export type NetshopImportDatabaseDependencies = Pick<
  typeof import("@/lib/netshop/database"),
  | "ensureNetshopSchema"
  | "getNetshopDatabase"
  | "normalizeJdProductMasterRows"
  | "reconcileNetshopMasterProducts"
  | "sanitizeNetshopIssues"
  | "saveNetshopImport"
  | "verifyNetshopImportBatch"
  | "findNetshopImportBatchById"
  | "readNetshopScopeOwnership"
  | "readNetshopScopeRows"
>;


export type NetshopImportFingerprintDependencies = Pick<
  typeof import("@/lib/imports/content-fingerprint"),
  | "auditRejectedImportResult"
  | "buildImportAttemptHash"
  | "buildImportContentFingerprint"
  | "ensureImportFingerprintSchema"
  | "failImportFingerprint"
  | "nextImportScopeStateToken"
  | "readImportScopeStateToken"
  | "recordImportFingerprint"
  | "renewImportFingerprintReservation"
  | "reserveImportFingerprint"
>;


export type NetshopProductAssetDependencies = {
  persistTmallProductAssetImages: typeof persistTmallProductAssetImages;
};


export type NormalizedNetshopImportPayload = {
  schemaVersion: "netshop-normalized-v1";
  disposition: "prepared" | "rejected";
  fileName: string;
  fileSizeBytes: number;
  rawFileHash: string;
  source: string;
  platform: string;
  shopName: string;
  warnings: NetshopImportIssue[];
  dataset?: string;
  sheetName?: string;
  note?: string;
  snapshotDate?: string;
  startDate?: string;
  endDate?: string;
  rows?: NetshopRowInput[];
  errors?: NetshopImportIssue[];
  message?: string;
  parserTotals?: Record<string, number | string | null>;
  imagePersistence?: { total: number; unique: number; verified: number } | Record<string, never>;
};


function normalizedNetshopRejection(
  input: NetshopImportInput,
  rawFileHash: string,
  issue: NetshopImportIssue,
  context: { platform?: string; shopName?: string } = {},
): NormalizedNetshopImportPayload {
  return {
    schemaVersion: "netshop-normalized-v1",
    disposition: "rejected",
    fileName: safeFileName(input.fileName),
    fileSizeBytes: input.fileSizeBytes,
    rawFileHash,
    source: input.source,
    platform: context.platform ?? normalizeText(input.platform),
    shopName: context.shopName ?? normalizeText(input.shopName),
    warnings: [],
    errors: [issue],
    message: issue.message,
  };
}


/**
 * Parse and validate source bytes at the Worker boundary without reading or
 * writing D1.  The Django writer treats this as untrusted input, revalidates
 * every row and independently recomputes the content fingerprint before it
 * can publish PostgreSQL facts.
 */
export async function prepareNormalizedNetshopImport(
  input: NetshopImportInput,
  productAssetDependencies: NetshopProductAssetDependencies = {
    persistTmallProductAssetImages,
  },
): Promise<NormalizedNetshopImportPayload> {
  const rawFileHash = toHex(await sha256(input.bytes));
  let parsed: ParsedTable;
  let assetImages: ReadonlyMap<number, TmallProductAssetImage> | undefined;
  try {
    if (input.source === "tmall_product_master" && !/\.xlsx$/i.test(input.fileName)) throw new Error("天猫货品主数据只接受 .xlsx 文件");
    if (input.source === "tmall_product_assets" && !/\.xlsx$/i.test(input.fileName)) throw new Error("天猫 SPU 商品图只接受 .xlsx 文件");
    if (input.source === "tmall_product_daily" && !/\.xls$/i.test(input.fileName)) throw new Error("生意参谋商品日数据只接受二进制 .xls 文件");
    if (input.source === "tmall_promotion" && !/\.zip$/i.test(input.fileName)) throw new Error("天猫推广数据只接受包含单个 CSV 的 .zip 文件");
    parsed = parseFile(input.bytes, input.fileName, input.source);
    if (input.source === "tmall_product_assets") assetImages = await extractTmallProductAssetImages(input.bytes);
  } catch (error) {
    const message = error instanceof Error ? error.message : "文件解析失败";
    return normalizedNetshopRejection(input, rawFileHash, { code: "PARSE_ERROR", message });
  }

  let header: ParsedHeader;
  try {
    header = findTmallHeader(input.source, parsed, input.expectedStartDate, input.expectedEndDate);
  } catch (error) {
    const message = error instanceof Error ? error.message : "表头识别失败";
    return normalizedNetshopRejection(input, rawFileHash, { code: "HEADER_NOT_FOUND", message });
  }

  let dataset: string;
  try {
    dataset = detectDataset(input.source, input.fileName, header.headers);
  } catch (error) {
    const message = error instanceof Error ? error.message : "数据集识别失败";
    return normalizedNetshopRejection(input, rawFileHash, { code: "DATASET_HEADER_MISMATCH", message });
  }
  if (input.source === "jd_sku_daily" && input.expectedDataset && input.expectedDataset !== dataset) {
    const message = `上传文件数据集为 ${dataset}，与预期 ${input.expectedDataset} 不一致`;
    return normalizedNetshopRejection(input, rawFileHash, { code: "EXPECTED_DATASET_MISMATCH", message });
  }

  let platform: string;
  let shopName: string;
  try {
    platform = isTmallSource(input.source) ? TMALL_PLATFORM : normalizeText(input.platform) || DEFAULT_PLATFORM;
    shopName = isTmallSource(input.source)
      ? resolveEnabledTmallShop(input.shopName).shopName
      : normalizeText(input.shopName) || DEFAULT_SHOP_NAME;
  } catch (error) {
    const message = error instanceof Error ? error.message : "天猫店铺身份无效";
    return normalizedNetshopRejection(input, rawFileHash, { code: "TMALL_SHOP_NOT_ALLOWED", message });
  }
  const snapshotDate = input.source === "tmall_product_master" || input.source === "tmall_product_assets"
    ? isoDateFromValue(input.snapshotDate)
    : isoDateFromValue(input.snapshotDate) || fileDate(input.fileName) || "";
  const snapshotSource = usesSnapshotDate(input.source);
  if (snapshotSource && !snapshotDate) {
    const message = `${input.source} 快照导入必须提供有效 snapshot_date=YYYY-MM-DD`;
    return normalizedNetshopRejection(
      input,
      rawFileHash,
      { code: "MISSING_SNAPSHOT_DATE", message },
      { platform, shopName },
    );
  }

  const built = await buildNetshopRows(input, {
    parsed,
    header,
    dataset,
    platform,
    shopName,
    fileHash: rawFileHash,
    snapshotDate,
    assetImages,
  });
  const errors = built.sourceErrors.length > 0 ? [...built.sourceErrors] : validateRows(built.rows);
  if (input.source === "tmall_promotion") {
    for (const row of built.rawRows) {
      if (normalizeText(row.raw["主体类型"]) !== "商品") {
        errors.push({ row: row.rowNumber, field: "主体类型", code: "UNSUPPORTED_SUBJECT_TYPE", message: "推广导入当前只接受主体类型为“商品”的数据" });
      }
    }
  }
  if (input.source === "jd_sku_daily" || input.source === "tmall_product_daily" || input.source === "tmall_promotion") {
    const coverage = dailyDateCoverage(
      input.expectedStartDate,
      input.expectedEndDate,
      built.rows.map((row) => row.businessDate),
    );
    if (!coverage.validRange) {
      errors.push({ code: "MISSING_EXPECTED_DATE_RANGE", message: "分天导入必须提供有效的目标起止日期" });
    } else {
      if (coverage.missingDates.length) errors.push({ code: "MISSING_EXPECTED_DATES", message: `目标区间缺少日期：${coverage.missingDates.join(", ")}` });
      if (coverage.outOfRangeDates.length) errors.push({ code: "OUT_OF_RANGE_DATES", message: `文件包含目标区间外日期：${coverage.outOfRangeDates.join(", ")}` });
    }
  }
  if (errors.length > 0) {
    return {
      ...normalizedNetshopRejection(
        input,
        rawFileHash,
        errors[0],
        { platform, shopName },
      ),
      errors: errors.slice(0, 200),
      message: "文件校验未通过，未写入数据",
    };
  }

  const missingSkuRows = input.source === "tmall_product_master"
    ? built.rows.filter((row) => !row.skuId).length
    : 0;
  const missingSkuMerchantCodeRows = input.source === "tmall_product_master"
    ? built.rows.filter((row) => !normalizeText(row.raw["SKU商家编码"])).length
    : 0;
  const merchantCodeCounts = new Map<string, number>();
  if (input.source === "tmall_product_master") {
    for (const row of built.rows) {
      const value = normalizeText(row.raw["SKU商家编码"]);
      if (value) merchantCodeCounts.set(value, (merchantCodeCounts.get(value) ?? 0) + 1);
    }
  }
  const duplicateMerchantCodeRows = [...merchantCodeCounts.values()]
    .filter((count) => count > 1)
    .reduce((sum, count) => sum + count, 0);
  const warnings: NetshopImportIssue[] = [
    ...(missingSkuRows > 0 ? [{ code: "MISSING_SKU_ID", message: `${missingSkuRows} 行缺少 SKU ID，已使用商品ID、销售属性和源行号构造隔离键` }] : []),
    ...(missingSkuMerchantCodeRows > 0 ? [{ code: "MISSING_MERCHANT_SKU_CODE", message: `${missingSkuMerchantCodeRows} 行缺少 SKU 商家编码；该字段仅保留作业务映射，不参与唯一键` }] : []),
    ...(duplicateMerchantCodeRows > 0 ? [{ code: "DUPLICATE_MERCHANT_CODE", message: `${duplicateMerchantCodeRows} 行使用重复 SKU 商家编码；商家编码仅作映射，不作为主键` }] : []),
  ];

  let imagePersistence: { total: number; unique: number; verified: number } | Record<string, never> = {};
  if (input.source === "tmall_product_assets") {
    try {
      imagePersistence = await productAssetDependencies.persistTmallProductAssetImages([
        ...(assetImages?.values() ?? []),
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "商品图片存储回查失败";
      return normalizedNetshopRejection(
        input,
        rawFileHash,
        { code: "PRODUCT_IMAGE_PERSIST_FAILED", message },
        { platform, shopName },
      );
    }
  }
  const dates = built.rows.map((row) => row.businessDate).filter(Boolean).sort();
  const sumMetric = (key: string) => built.rows.reduce((sum, row) => sum + Number(row.metrics[key] ?? 0), 0);
  return {
    schemaVersion: "netshop-normalized-v1",
    disposition: "prepared",
    fileName: safeFileName(input.fileName),
    fileSizeBytes: input.fileSizeBytes,
    rawFileHash,
    source: input.source,
    dataset,
    platform,
    shopName,
    sheetName: parsed.sheetName,
    note: normalizeText(input.note),
    snapshotDate: snapshotSource ? snapshotDate : "",
    startDate: input.expectedStartDate ?? (dates[0] ?? ""),
    endDate: input.expectedEndDate ?? (dates[dates.length - 1] ?? ""),
    rows: built.rows,
    warnings,
    parserTotals: {
      sourceRowCount: built.rawRows.length,
      rowCount: built.rows.length,
      uniqueProductCount: new Set(built.rows.map((row) => row.spuId).filter(Boolean)).size,
      uniqueSkuCount: new Set(built.rows.map((row) => row.skuId).filter(Boolean)).size,
      dateMin: dates[0] ?? null,
      dateMax: dates[dates.length - 1] ?? null,
      inventoryQuantity: sumMetric("inventoryQuantity"),
      transactionAmountCents: sumMetric("transactionAmountCents"),
      refundAmountCents: sumMetric("refundAmountCents"),
      spendCents: sumMetric("spendCents"),
      netTransactionAmountCents: sumMetric("netTransactionAmountCents"),
    },
    imagePersistence,
  };
}


export function readNetshopForm(formData: FormData) {
  const source = normalizeSource(formData.get("source"));
  const file = formData.get("file");
  return {
    source,
    file,
    platform: typeof formData.get("platform") === "string" ? String(formData.get("platform")) : undefined,
    shopName: typeof formData.get("shop_name") === "string"
      ? String(formData.get("shop_name"))
      : typeof formData.get("shopName") === "string"
        ? String(formData.get("shopName"))
        : undefined,
    note: typeof formData.get("note") === "string" ? String(formData.get("note")) : undefined,
    snapshotDate: typeof formData.get("snapshot_date") === "string"
      ? String(formData.get("snapshot_date"))
      : typeof formData.get("snapshotDate") === "string"
        ? String(formData.get("snapshotDate"))
        : undefined,
    expectedDataset: (formData.get("expected_dataset") ?? formData.get("expectedDataset")) === "sku_daily"
      ? "sku_daily"
      : (formData.get("expected_dataset") ?? formData.get("expectedDataset")) === "spu_daily"
        ? "spu_daily"
        : undefined,
    expectedStartDate: typeof (formData.get("expected_start_date") ?? formData.get("expectedStartDate")) === "string"
      ? String(formData.get("expected_start_date") ?? formData.get("expectedStartDate"))
      : undefined,
    expectedEndDate: typeof (formData.get("expected_end_date") ?? formData.get("expectedEndDate")) === "string"
      ? String(formData.get("expected_end_date") ?? formData.get("expectedEndDate"))
      : undefined,
  };
}
