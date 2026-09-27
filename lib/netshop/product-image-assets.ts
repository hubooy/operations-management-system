import { strFromU8, unzipSync } from "fflate";

import { decodeXmlEntities } from "@/lib/imports/xlsx";

const MAX_COMPRESSED_WORKBOOK_BYTES = 64 * 1024 * 1024;
const MAX_ASSET_ARCHIVE_ENTRIES = 1_000;
const MAX_ASSET_XML_BYTES = 5 * 1024 * 1024;
const MAX_ASSET_XML_TOTAL_BYTES = 16 * 1024 * 1024;
const MAX_PRODUCT_IMAGE_BYTES = 6 * 1024 * 1024;
const MAX_PRODUCT_IMAGE_TOTAL_BYTES = 64 * 1024 * 1024;
const MAX_PRODUCT_IMAGES = 500;
const MAX_ASSET_RELATIONSHIPS = 1_000;
const MAX_RELATIONSHIP_TARGET_LENGTH = 1_024;
const MAX_PRODUCT_IMAGE_PIXELS = 40_000_000;
const MAX_PRODUCT_IMAGE_DIMENSION = 16_384;
const PRODUCT_IMAGE_OBJECT_PREFIX = "netshop-product-images/v1";

export type TmallProductAssetImage = {
  rowNumber: number;
  contentHash: string;
  objectKey: string;
  mimeType: "image/jpeg" | "image/png" | "image/webp";
  sizeBytes: number;
  bytes: Uint8Array;
};

export type StoredNetshopProductImage = Pick<
  TmallProductAssetImage,
  "contentHash" | "objectKey" | "mimeType" | "sizeBytes"
>;

type Relationship = {
  id: string;
  target: string;
  type: string;
  external: boolean;
};

function normalizeZipPath(value: string) {
  return value.replace(/\\/g, "/").replace(/^\/+/, "");
}

function resolveZipTarget(sourcePath: string, target: string) {
  if (target.length > MAX_RELATIONSHIP_TARGET_LENGTH) throw new Error("XLSX 关系目标路径过长");
  const normalizedTarget = target.replace(/\\/g, "/");
  const rawParts = normalizedTarget.startsWith("/")
    ? normalizedTarget.slice(1).split("/")
    : `${sourcePath.slice(0, sourcePath.lastIndexOf("/") + 1)}${normalizedTarget}`.split("/");
  const resolved: string[] = [];
  for (const part of rawParts) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (resolved.length === 0) throw new Error("XLSX 关系路径越界");
      resolved.pop();
      continue;
    }
    resolved.push(part);
  }
  const path = resolved.join("/");
  if (!path) throw new Error("XLSX 关系路径为空");
  return path;
}

function relationshipPartPath(sourcePath: string) {
  const slash = sourcePath.lastIndexOf("/");
  const directory = slash < 0 ? "" : sourcePath.slice(0, slash + 1);
  const fileName = slash < 0 ? sourcePath : sourcePath.slice(slash + 1);
  return `${directory}_rels/${fileName}.rels`;
}

function xmlAttribute(attributes: string, localName: string) {
  const expression = /([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  let match: RegExpExecArray | null;
  while ((match = expression.exec(attributes)) !== null) {
    const qualifiedName = match[1];
    const candidate = qualifiedName.slice(qualifiedName.lastIndexOf(":") + 1);
    if (qualifiedName === localName || candidate === localName) {
      return decodeXmlEntities(match[2] ?? match[3] ?? "");
    }
  }
  return null;
}

function parseRelationships(xml: string) {
  const relationships: Relationship[] = [];
  const expression = /<(?:[A-Za-z_][\w.-]*:)?Relationship\b([^>]*)\/?\s*>/gi;
  let match: RegExpExecArray | null;
  while ((match = expression.exec(xml)) !== null) {
    const id = xmlAttribute(match[1], "Id");
    const target = xmlAttribute(match[1], "Target");
    const type = xmlAttribute(match[1], "Type");
    if (!id || !target || !type) continue;
    if (relationships.length >= MAX_ASSET_RELATIONSHIPS) throw new Error("XLSX 图片关系数量过多");
    if (id.length > 128 || target.length > MAX_RELATIONSHIP_TARGET_LENGTH || type.length > 512) {
      throw new Error("XLSX 图片关系字段过长");
    }
    relationships.push({
      id,
      target,
      type,
      external: xmlAttribute(match[1], "TargetMode")?.toLowerCase() === "external",
    });
  }
  return relationships;
}

function requiredPart(parts: Record<string, Uint8Array>, path: string) {
  const bytes = parts[path];
  if (!bytes) throw new Error(`XLSX 缺少图片关系文件：${path}`);
  return bytes;
}

function decodeXml(bytes: Uint8Array, path: string) {
  if (bytes.byteLength > MAX_ASSET_XML_BYTES) throw new Error(`XLSX 图片关系文件过大：${path}`);
  return strFromU8(bytes).replace(/^\uFEFF/, "");
}

function firstSheetRelationshipId(workbookXml: string) {
  const match = /<(?:[A-Za-z_][\w.-]*:)?sheet\b([^>]*)\/?\s*>/i.exec(workbookXml);
  return match ? xmlAttribute(match[1], "id") : null;
}

function readIntegerElement(xml: string, localName: string) {
  const escaped = localName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(
    `<(?:[A-Za-z_][\\w.-]*:)?${escaped}\\b[^>]*>(\\d+)<\\/(?:[A-Za-z_][\\w.-]*:)?${escaped}\\s*>`,
    "i",
  ).exec(xml);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function drawingAnchors(drawingXml: string) {
  const anchors: Array<{ rowNumber: number; columnNumber: number; relationshipId: string }> = [];
  const expression = /<(?:[A-Za-z_][\w.-]*:)?(?:twoCellAnchor|oneCellAnchor)\b[^>]*>([\s\S]*?)<\/(?:[A-Za-z_][\w.-]*:)?(?:twoCellAnchor|oneCellAnchor)\s*>/gi;
  let match: RegExpExecArray | null;
  while ((match = expression.exec(drawingXml)) !== null) {
    if (anchors.length >= MAX_PRODUCT_IMAGES) throw new Error(`商品图片超过 ${MAX_PRODUCT_IMAGES} 张上限`);
    const body = match[1];
    const from = /<(?:[A-Za-z_][\w.-]*:)?from\b[^>]*>([\s\S]*?)<\/(?:[A-Za-z_][\w.-]*:)?from\s*>/i.exec(body)?.[1];
    const blipAttributes = /<(?:[A-Za-z_][\w.-]*:)?blip\b([^>]*)\/?\s*>/i.exec(body)?.[1];
    const row = from ? readIntegerElement(from, "row") : null;
    const column = from ? readIntegerElement(from, "col") : null;
    const relationshipId = blipAttributes ? xmlAttribute(blipAttributes, "embed") : null;
    if (row === null || column === null || !relationshipId) throw new Error("商品图片缺少有效的单元格锚点或图片关系");
    anchors.push({ rowNumber: row + 1, columnNumber: column + 1, relationshipId });
  }
  return anchors;
}

function validateDimensions(width: number, height: number) {
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height)
    || width <= 0 || height <= 0
    || width > MAX_PRODUCT_IMAGE_DIMENSION || height > MAX_PRODUCT_IMAGE_DIMENSION
    || width * height > MAX_PRODUCT_IMAGE_PIXELS) {
    throw new Error("商品主图像素尺寸无效或超过安全上限");
  }
}

function uint32BigEndian(bytes: Uint8Array, offset: number) {
  return (bytes[offset] * 0x1000000) + (bytes[offset + 1] << 16) + (bytes[offset + 2] << 8) + bytes[offset + 3];
}

function uint32LittleEndian(bytes: Uint8Array, offset: number) {
  return (bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16) | (bytes[offset + 3] << 24)) >>> 0;
}

function validateJpeg(bytes: Uint8Array) {
  if (bytes.byteLength < 32 || bytes[0] !== 0xff || bytes[1] !== 0xd8
    || bytes[bytes.byteLength - 2] !== 0xff || bytes[bytes.byteLength - 1] !== 0xd9) return false;
  const startOfFrame = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
  let offset = 2;
  let dimensions: { width: number; height: number } | null = null;
  let foundScan = false;
  while (offset < bytes.byteLength - 2) {
    if (bytes[offset] !== 0xff) return false;
    while (offset < bytes.byteLength && bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset];
    offset += 1;
    if (marker === 0xd9) break;
    if (marker === 0x00 || marker === 0xd8 || marker === 0x01 || marker >= 0xd0 && marker <= 0xd7) continue;
    if (offset + 2 > bytes.byteLength) return false;
    const segmentLength = (bytes[offset] << 8) | bytes[offset + 1];
    if (segmentLength < 2 || offset + segmentLength > bytes.byteLength) return false;
    if (startOfFrame.has(marker)) {
      if (segmentLength < 8) return false;
      dimensions = {
        height: (bytes[offset + 3] << 8) | bytes[offset + 4],
        width: (bytes[offset + 5] << 8) | bytes[offset + 6],
      };
    }
    if (marker === 0xda) {
      foundScan = true;
      break;
    }
    offset += segmentLength;
  }
  if (!dimensions || !foundScan) return false;
  validateDimensions(dimensions.width, dimensions.height);
  return true;
}

function validatePng(bytes: Uint8Array) {
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (bytes.byteLength < 45 || signature.some((byte, index) => bytes[index] !== byte)) return false;
  let offset = 8;
  let foundHeader = false;
  let foundData = false;
  let foundEnd = false;
  while (offset + 12 <= bytes.byteLength) {
    const length = uint32BigEndian(bytes, offset);
    const type = strFromU8(bytes.subarray(offset + 4, offset + 8));
    const next = offset + 12 + length;
    if (length > bytes.byteLength || next > bytes.byteLength) return false;
    if (!foundHeader) {
      if (type !== "IHDR" || length !== 13) return false;
      validateDimensions(uint32BigEndian(bytes, offset + 8), uint32BigEndian(bytes, offset + 12));
      foundHeader = true;
    } else if (type === "IHDR") {
      return false;
    }
    if (type === "IDAT") foundData = true;
    if (type === "IEND") {
      if (length !== 0 || next !== bytes.byteLength) return false;
      foundEnd = true;
      break;
    }
    offset = next;
  }
  return foundHeader && foundData && foundEnd;
}

function validateWebp(bytes: Uint8Array) {
  if (bytes.byteLength < 30 || strFromU8(bytes.subarray(0, 4)) !== "RIFF"
    || strFromU8(bytes.subarray(8, 12)) !== "WEBP"
    || uint32LittleEndian(bytes, 4) !== bytes.byteLength - 8) return false;
  let offset = 12;
  let dimensions: { width: number; height: number } | null = null;
  while (offset + 8 <= bytes.byteLength) {
    const type = strFromU8(bytes.subarray(offset, offset + 4));
    const length = uint32LittleEndian(bytes, offset + 4);
    const data = offset + 8;
    const next = data + length + (length & 1);
    if (length > bytes.byteLength || next > bytes.byteLength) return false;
    if (type === "VP8X" && length >= 10) {
      dimensions = {
        width: 1 + bytes[data + 4] + (bytes[data + 5] << 8) + (bytes[data + 6] << 16),
        height: 1 + bytes[data + 7] + (bytes[data + 8] << 8) + (bytes[data + 9] << 16),
      };
    } else if (type === "VP8L" && length >= 5 && bytes[data] === 0x2f) {
      dimensions = {
        width: 1 + bytes[data + 1] + ((bytes[data + 2] & 0x3f) << 8),
        height: 1 + ((bytes[data + 2] & 0xc0) >> 6) + (bytes[data + 3] << 2) + ((bytes[data + 4] & 0x0f) << 10),
      };
    } else if (type === "VP8 " && length >= 10
      && bytes[data + 3] === 0x9d && bytes[data + 4] === 0x01 && bytes[data + 5] === 0x2a) {
      dimensions = {
        width: (bytes[data + 6] | (bytes[data + 7] << 8)) & 0x3fff,
        height: (bytes[data + 8] | (bytes[data + 9] << 8)) & 0x3fff,
      };
    }
    offset = next;
  }
  if (!dimensions || offset !== bytes.byteLength) return false;
  validateDimensions(dimensions.width, dimensions.height);
  return true;
}

function sniffImage(bytes: Uint8Array): { mimeType: TmallProductAssetImage["mimeType"]; extension: string } {
  if (validateJpeg(bytes)) return { mimeType: "image/jpeg", extension: "jpg" };
  if (validatePng(bytes)) return { mimeType: "image/png", extension: "png" };
  if (validateWebp(bytes)) return { mimeType: "image/webp", extension: "webp" };
  throw new Error("商品主图只接受结构完整、像素尺寸安全的 JPEG、PNG 或 WebP 图片");
}

async function sha256(bytes: Uint8Array) {
  const input = bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength
    ? bytes.buffer as ArrayBuffer
    : bytes.slice().buffer as ArrayBuffer;
  const digest = await crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function productImageObjectKey(contentHash: string, mimeType: StoredNetshopProductImage["mimeType"]) {
  const extension = mimeType === "image/png" ? "png" : mimeType === "image/webp" ? "webp" : "jpg";
  return `${PRODUCT_IMAGE_OBJECT_PREFIX}/${contentHash}.${extension}`;
}

export async function extractTmallProductAssetImages(input: Uint8Array) {
  if (input.byteLength === 0) throw new Error("XLSX 文件为空");
  if (input.byteLength > MAX_COMPRESSED_WORKBOOK_BYTES) throw new Error("XLSX 压缩文件超过 64MB 上限");
  let archiveEntryCount = 0;
  let advertisedXmlBytes = 0;
  let advertisedImageBytes = 0;
  let advertisedImageCount = 0;
  let parts: Record<string, Uint8Array>;
  try {
    parts = unzipSync(input, {
      filter(file) {
        archiveEntryCount += 1;
        if (archiveEntryCount > MAX_ASSET_ARCHIVE_ENTRIES) throw new Error("XLSX 文件条目过多");
        const path = normalizeZipPath(file.name);
        const selectedXml = path === "xl/workbook.xml"
          || path === "xl/_rels/workbook.xml.rels"
          || /^xl\/worksheets\/_rels\/[^/]+\.rels$/i.test(path)
          || /^xl\/drawings\/[^/]+\.xml$/i.test(path)
          || /^xl\/drawings\/_rels\/[^/]+\.rels$/i.test(path);
        if (selectedXml) {
          if (file.originalSize > MAX_ASSET_XML_BYTES) throw new Error(`XLSX 图片关系文件过大：${path}`);
          advertisedXmlBytes += file.originalSize;
          if (advertisedXmlBytes > MAX_ASSET_XML_TOTAL_BYTES) throw new Error("XLSX 图片关系 XML 总量超过 16MB 上限");
        }
        if (/^xl\/media\/[^/]+$/i.test(path)) {
          advertisedImageCount += 1;
          if (advertisedImageCount > MAX_PRODUCT_IMAGES + 50) throw new Error("XLSX 媒体文件条目过多");
          if (file.originalSize > MAX_PRODUCT_IMAGE_BYTES) throw new Error(`单张商品图片超过 ${MAX_PRODUCT_IMAGE_BYTES} 字节上限`);
          advertisedImageBytes += file.originalSize;
          if (advertisedImageBytes > MAX_PRODUCT_IMAGE_TOTAL_BYTES) throw new Error("商品图片解压后总量超过 64MB 上限");
          return true;
        }
        return selectedXml;
      },
    });
  } catch (error) {
    throw new Error(error instanceof Error ? `商品图片解析失败：${error.message}` : "商品图片解析失败");
  }
  const normalized = Object.fromEntries(Object.entries(parts).map(([path, bytes]) => [normalizeZipPath(path), bytes]));
  let actualXmlBytes = 0;
  let actualMediaBytes = 0;
  for (const [path, bytes] of Object.entries(normalized)) {
    if (/^xl\/media\/[^/]+$/i.test(path)) actualMediaBytes += bytes.byteLength;
    else actualXmlBytes += bytes.byteLength;
  }
  if (actualXmlBytes > MAX_ASSET_XML_TOTAL_BYTES) throw new Error("XLSX 图片关系 XML 总量超过 16MB 上限");
  if (actualMediaBytes > MAX_PRODUCT_IMAGE_TOTAL_BYTES) throw new Error("商品图片解压后总量超过 64MB 上限");
  const workbookPath = "xl/workbook.xml";
  const workbookRelsPath = "xl/_rels/workbook.xml.rels";
  const workbookXml = decodeXml(requiredPart(normalized, workbookPath), workbookPath);
  const sheetRelationshipId = firstSheetRelationshipId(workbookXml);
  if (!sheetRelationshipId) throw new Error("首个工作表缺少关系 ID");
  const workbookRelationships = parseRelationships(decodeXml(requiredPart(normalized, workbookRelsPath), workbookRelsPath));
  const sheetRelationship = workbookRelationships.find((item) => item.id === sheetRelationshipId && !item.external);
  if (!sheetRelationship || !sheetRelationship.type.toLowerCase().endsWith("/worksheet")) throw new Error("无法定位商品图工作表");
  const worksheetPath = resolveZipTarget(workbookPath, sheetRelationship.target);
  const worksheetRelsPath = relationshipPartPath(worksheetPath);
  const worksheetRelationships = parseRelationships(decodeXml(requiredPart(normalized, worksheetRelsPath), worksheetRelsPath));
  const drawings = worksheetRelationships.filter((item) => !item.external && item.type.toLowerCase().endsWith("/drawing"));
  if (drawings.length !== 1) throw new Error("商品图工作表必须且只能关联一个图片绘图层");
  const drawingPath = resolveZipTarget(worksheetPath, drawings[0].target);
  const drawingRelsPath = relationshipPartPath(drawingPath);
  const drawingXml = decodeXml(requiredPart(normalized, drawingPath), drawingPath);
  const drawingRelationships = parseRelationships(decodeXml(requiredPart(normalized, drawingRelsPath), drawingRelsPath));
  const relationById = new Map(drawingRelationships.map((item) => [item.id, item]));
  const anchors = drawingAnchors(drawingXml);
  if (anchors.length === 0) throw new Error("商品图工作表没有可识别的内嵌图片");
  const rowNumbers = new Set<number>();
  let actualImageBytes = 0;
  const images: TmallProductAssetImage[] = [];
  for (const anchor of anchors) {
    if (anchor.columnNumber !== 1) throw new Error(`第 ${anchor.rowNumber} 行商品图片未锚定在“主图”列`);
    if (rowNumbers.has(anchor.rowNumber)) throw new Error(`第 ${anchor.rowNumber} 行存在多张商品图片`);
    rowNumbers.add(anchor.rowNumber);
    const relationship = relationById.get(anchor.relationshipId);
    if (!relationship || relationship.external || !relationship.type.toLowerCase().endsWith("/image")) {
      throw new Error(`第 ${anchor.rowNumber} 行商品图片关系无效`);
    }
    const imagePath = resolveZipTarget(drawingPath, relationship.target);
    if (!/^xl\/media\/[^/]+$/i.test(imagePath)) throw new Error(`第 ${anchor.rowNumber} 行商品图片路径不受支持`);
    const bytes = requiredPart(normalized, imagePath);
    if (bytes.byteLength === 0 || bytes.byteLength > MAX_PRODUCT_IMAGE_BYTES) throw new Error(`第 ${anchor.rowNumber} 行商品图片大小无效`);
    actualImageBytes += bytes.byteLength;
    if (actualImageBytes > MAX_PRODUCT_IMAGE_TOTAL_BYTES) throw new Error("商品图片解压后总量超过 64MB 上限");
    const { mimeType } = sniffImage(bytes);
    const contentHash = await sha256(bytes);
    images.push({
      rowNumber: anchor.rowNumber,
      contentHash,
      objectKey: productImageObjectKey(contentHash, mimeType),
      mimeType,
      sizeBytes: bytes.byteLength,
      bytes,
    });
  }
  return new Map(images.map((image) => [image.rowNumber, image]));
}

async function imageBucket() {
  const { env } = await import("cloudflare:workers");
  if (!env.SALES_IMPORT_FILES) throw new Error("R2 商品图片存储未配置");
  return env.SALES_IMPORT_FILES;
}

export async function persistTmallProductAssetImages(
  images: readonly TmallProductAssetImage[],
  options: { onBatchPersisted?: () => Promise<void> } = {},
) {
  const unique = [...new Map(images.map((image) => [image.contentHash, image])).values()];
  const bucket = await imageBucket();
  let verified = 0;
  for (let offset = 0; offset < unique.length; offset += 8) {
    const batch = unique.slice(offset, offset + 8);
    await Promise.all(batch.map(async (image) => {
      const expectedKey = productImageObjectKey(image.contentHash, image.mimeType);
      if (image.objectKey !== expectedKey || image.sizeBytes !== image.bytes.byteLength) throw new Error("商品图片元数据与内容不一致");
      const existing = await bucket.head(expectedKey);
      if (!existing
        || existing.size !== image.sizeBytes
        || existing.customMetadata?.sha256 !== image.contentHash
        || existing.httpMetadata?.contentType !== image.mimeType) {
        await bucket.put(expectedKey, image.bytes, {
          httpMetadata: { contentType: image.mimeType, cacheControl: "private, max-age=31536000, immutable" },
          customMetadata: { source: "tmall-product-assets", sha256: image.contentHash },
        });
      }
      const stored = await bucket.head(expectedKey);
      if (!stored
        || stored.size !== image.sizeBytes
        || stored.customMetadata?.sha256 !== image.contentHash
        || stored.httpMetadata?.contentType !== image.mimeType) {
        throw new Error(`商品图片写入后回查失败：${image.contentHash.slice(0, 12)}`);
      }
      verified += 1;
    }));
    await options.onBatchPersisted?.();
  }
  return { total: images.length, unique: unique.length, verified };
}

export function productAssetMetadata(image: TmallProductAssetImage) {
  return {
    "图片内容SHA256": image.contentHash,
    "图片对象键": image.objectKey,
    "图片MIME": image.mimeType,
    "图片字节数": image.sizeBytes,
  } as const;
}

export function storedNetshopProductImage(raw: Record<string, unknown>): StoredNetshopProductImage | null {
  const contentHash = String(raw["图片内容SHA256"] ?? "").trim().toLowerCase();
  const objectKey = String(raw["图片对象键"] ?? "").trim();
  const mimeType = String(raw["图片MIME"] ?? "").trim() as StoredNetshopProductImage["mimeType"];
  const sizeBytes = Number(raw["图片字节数"] ?? 0);
  if (!/^[a-f0-9]{64}$/.test(contentHash)
    || !["image/jpeg", "image/png", "image/webp"].includes(mimeType)
    || !Number.isSafeInteger(sizeBytes) || sizeBytes <= 0 || sizeBytes > MAX_PRODUCT_IMAGE_BYTES
    || objectKey !== productImageObjectKey(contentHash, mimeType)) return null;
  return { contentHash, objectKey, mimeType, sizeBytes };
}

export function netshopProductImageUrl(raw: Record<string, unknown>) {
  const stored = storedNetshopProductImage(raw);
  return stored ? `/api/netshop/product-images/${stored.contentHash}` : "";
}

export async function readNetshopProductImageObject(metadata: StoredNetshopProductImage) {
  const valid = storedNetshopProductImage({
    "图片内容SHA256": metadata.contentHash,
    "图片对象键": metadata.objectKey,
    "图片MIME": metadata.mimeType,
    "图片字节数": metadata.sizeBytes,
  });
  if (!valid) return null;
  const bucket = await imageBucket();
  const object = await bucket.get(valid.objectKey);
  return object
    && object.size === valid.sizeBytes
    && object.customMetadata?.sha256 === valid.contentHash
    && object.httpMetadata?.contentType === valid.mimeType
    ? { object, ...valid }
    : null;
}
