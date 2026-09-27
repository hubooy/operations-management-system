import { strToU8, zipSync } from "fflate";

export type FollowupWorkbookImage = {
  bytes: Uint8Array;
  extension: "png" | "jpeg" | "gif";
  pixelWidth: number;
  pixelHeight: number;
};

export type FollowupWorkbookInput = {
  timelineStart: string;
  dataCutoffDate: string | null;
  weeks: Array<{ label: string; dateRange: string; dataComplete: boolean }>;
  items: Array<{
    brand: string;
    name: string;
    productImageUrl: string;
    weeklyNetQuantities: number[];
    productImage: FollowupWorkbookImage | null;
    trendImage: FollowupWorkbookImage;
  }>;
};

function escapeXml(value: string) {
  return value.replace(
    /[<>&"']/g,
    (character) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" })[character] ?? character,
  );
}

function columnName(index: number) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
}

function inlineCell(reference: string, value: string, style: number) {
  return `<c r="${reference}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${escapeXml(value)}</t></is></c>`;
}

function numberCell(reference: string, value: number, style: number) {
  return `<c r="${reference}" s="${style}"><v>${Number.isFinite(value) ? Math.trunc(value) : 0}</v></c>`;
}

function fittedImagePlacement(image: FollowupWorkbookImage, boxWidth: number, boxHeight: number) {
  if (!Number.isFinite(image.pixelWidth) || !Number.isFinite(image.pixelHeight) || image.pixelWidth <= 0 || image.pixelHeight <= 0) {
    throw new Error("周报图片尺寸无效。");
  }
  const scale = Math.min(boxWidth / image.pixelWidth, boxHeight / image.pixelHeight);
  const width = image.pixelWidth * scale;
  const height = image.pixelHeight * scale;
  return { width, height, xOffset: (boxWidth - width) / 2, yOffset: (boxHeight - height) / 2 };
}

function pixelsToEmu(value: number) {
  return Math.round(value * 9525);
}

function drawingAnchor(imageId: number, row: number, column: number, image: FollowupWorkbookImage, boxWidth: number, boxHeight: number) {
  const rowOffset = row === 1 ? 7 : 4;
  const placement = fittedImagePlacement(image, boxWidth, boxHeight);
  const width = pixelsToEmu(placement.width);
  const height = pixelsToEmu(placement.height);
  const columnOffset = pixelsToEmu(8 + placement.xOffset);
  const verticalOffset = pixelsToEmu(rowOffset + placement.yOffset);
  return `<xdr:oneCellAnchor><xdr:from><xdr:col>${column}</xdr:col><xdr:colOff>${columnOffset}</xdr:colOff><xdr:row>${row - 1}</xdr:row><xdr:rowOff>${verticalOffset}</xdr:rowOff></xdr:from><xdr:ext cx="${width}" cy="${height}"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="${imageId}" name="周报图片 ${imageId}"/><xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId${imageId}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${width}" cy="${height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor>`;
}

const stylesXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="3"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FF17233C"/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF4477C8"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDBE5F5"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border/><border><left/><right style="thin"><color rgb="FFB7C9E7"/></right><top/><bottom style="thin"><color rgb="FFB7C9E7"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="9"><xf xfId="0" fontId="0" fillId="0" borderId="0"/><xf xfId="0" fontId="1" fillId="2" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf xfId="0" fontId="0" fillId="3" borderId="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf xfId="0" fontId="0" fillId="0" borderId="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf xfId="0" fontId="2" fillId="3" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf xfId="0" fontId="2" fillId="0" borderId="1" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf xfId="0" fontId="2" fillId="3" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf><xf xfId="0" fontId="2" fillId="0" borderId="1" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf><xf xfId="0" fontId="0" fillId="0" borderId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;

export function createNewProductFollowupWorkbookBytes(input: FollowupWorkbookInput) {
  if (!input.weeks.length) throw new Error("周报至少需要一个周维度。");
  if (input.items.some((item) => item.weeklyNetQuantities.length !== input.weeks.length)) {
    throw new Error("产品线与周维度数量不一致。");
  }
  const headers = ["品牌", "产品图", "产品名称", "趋势", ...input.weeks.map((week) => `${week.label}\n(${week.dateRange})${week.dataComplete ? "" : "\n数据未完整"}`)];
  const rows = [`<row r="1" ht="58" customHeight="1">${headers.map((value, index) => inlineCell(`${columnName(index)}1`, value, 1)).join("")}</row>`];
  const images: Array<FollowupWorkbookImage & { row: number; column: number; width: number; height: number }> = [];
  input.items.forEach((item, index) => {
    const rowNumber = index + 2;
    const alternate = index % 2 === 0;
    const normalStyle = alternate ? 2 : 3;
    const boldStyle = alternate ? 4 : 5;
    const nameStyle = alternate ? 6 : 7;
    const cells = [
      inlineCell(`A${rowNumber}`, item.brand || "志高", boldStyle),
      inlineCell(`B${rowNumber}`, item.productImage ? "" : item.productImageUrl ? "查看产品图" : "暂无", normalStyle),
      inlineCell(`C${rowNumber}`, item.name, nameStyle),
      inlineCell(`D${rowNumber}`, "", normalStyle),
      ...item.weeklyNetQuantities.map((value, weekIndex) => numberCell(`${columnName(weekIndex + 4)}${rowNumber}`, value, normalStyle)),
    ];
    rows.push(`<row r="${rowNumber}" ht="58" customHeight="1">${cells.join("")}</row>`);
    if (item.productImage) images.push({ ...item.productImage, row: rowNumber, column: 1, width: 50, height: 50 });
    images.push({ ...item.trendImage, row: rowNumber, column: 3, width: 138, height: 42 });
  });
  const noteRow = input.items.length + 3;
  rows.push(`<row r="${noteRow}" ht="28" customHeight="1">${inlineCell(`A${noteRow}`, `周维度自 ${input.timelineStart} 起持续累积 · 数值口径：吉客云货品代码净销量`, 8)}${inlineCell(`E${noteRow}`, `销售数据截至：${input.dataCutoffDate ?? "暂无"}`, 8)}</row>`);
  const lastColumn = columnName(headers.length - 1);
  const drawing = images.length ? '<drawing r:id="rId1"/>' : "";
  const sheetXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><dimension ref="A1:${lastColumn}${noteRow}"/><sheetViews><sheetView workbookViewId="0"><pane xSplit="4" ySplit="1" topLeftCell="E2" activePane="bottomRight" state="frozen"/><selection pane="bottomRight" activeCell="E2" sqref="E2"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="22"/><cols><col min="1" max="1" width="12" customWidth="1"/><col min="2" max="2" width="14" customWidth="1"/><col min="3" max="3" width="28" customWidth="1"/><col min="4" max="4" width="25" customWidth="1"/><col min="5" max="${headers.length}" width="16" customWidth="1"/></cols><sheetData>${rows.join("")}</sheetData><autoFilter ref="A1:${lastColumn}${Math.max(1, input.items.length + 1)}"/>${drawing}</worksheet>`;
  const files: Record<string, Uint8Array> = {
    "[Content_Types].xml": strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="gif" ContentType="image/gif"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${images.length ? '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>' : ""}</Types>`),
    "_rels/.rels": strToU8('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
    "xl/workbook.xml": strToU8('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="上新周报" sheetId="1" r:id="rId1"/></sheets></workbook>'),
    "xl/_rels/workbook.xml.rels": strToU8('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'),
    "xl/styles.xml": strToU8(stylesXml),
    "xl/worksheets/sheet1.xml": strToU8(sheetXml),
  };
  if (images.length) {
    files["xl/worksheets/_rels/sheet1.xml.rels"] = strToU8('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/></Relationships>');
    files["xl/drawings/drawing1.xml"] = strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">${images.map((image, index) => drawingAnchor(index + 1, image.row, image.column, image, image.width, image.height)).join("")}</xdr:wsDr>`);
    files["xl/drawings/_rels/drawing1.xml.rels"] = strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${images.map((image, index) => `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image${index + 1}.${image.extension}"/>`).join("")}</Relationships>`);
    images.forEach((image, index) => { files[`xl/media/image${index + 1}.${image.extension}`] = image.bytes; });
  }
  return zipSync(files, { level: 6, mtime: new Date(2000, 0, 1, 0, 0, 0) });
}
