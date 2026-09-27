import assert from "node:assert/strict";
import test from "node:test";
import { strFromU8, unzipSync } from "fflate";
import { createNewProductFollowupWorkbookBytes } from "../lib/imports/new-product-followup-xlsx";
import { parseXlsxFirstSheet } from "../lib/imports/xlsx";

test("new-product follow-up XLSX keeps preview colors, values, freeze pane and embedded images", () => {
  const productPng = {
    bytes: Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10]),
    extension: "png" as const,
    pixelWidth: 200,
    pixelHeight: 100,
  };
  const trendPng = { ...productPng, pixelWidth: 300, pixelHeight: 84 };
  const workbook = createNewProductFollowupWorkbookBytes({
    timelineStart: "2026-08-03",
    dataCutoffDate: "2026-09-03",
    weeks: [
      { label: "第1周", dateRange: "08.03-08.09", dataComplete: true },
      { label: "第2周", dateRange: "08.10-08.16", dataComplete: false },
    ],
    items: [{
      brand: "志高",
      name: "空气净化器新品",
      productImageUrl: "https://example.test/product.png",
      weeklyNetQuantities: [12, 24],
      productImage: productPng,
      trendImage: trendPng,
    }],
  });
  const files = unzipSync(workbook);
  const sheet = strFromU8(files["xl/worksheets/sheet1.xml"]);
  const styles = strFromU8(files["xl/styles.xml"]);
  const drawing = strFromU8(files["xl/drawings/drawing1.xml"]);
  assert.match(sheet, /xSplit="4" ySplit="1"/);
  assert.match(sheet, /autoFilter ref="A1:F2"/);
  assert.match(styles, /FF4477C8/);
  assert.match(styles, /FFDBE5F5/);
  assert.equal((drawing.match(/<xdr:oneCellAnchor>/g) || []).length, 2);
  assert.match(drawing, /<xdr:col>1<\/xdr:col><xdr:colOff>76200<\/xdr:colOff><xdr:row>1<\/xdr:row><xdr:rowOff>157163<\/xdr:rowOff>.*?<xdr:ext cx="476250" cy="238125"\/>/);
  assert.ok(files["xl/media/image1.png"]);
  assert.ok(files["xl/media/image2.png"]);
  const parsed = parseXlsxFirstSheet(workbook);
  assert.deepEqual(parsed.rows[0]?.cells.slice(0, 4), ["品牌", "产品图", "产品名称", "趋势"]);
  assert.deepEqual(parsed.rows[1]?.cells.slice(0, 6), ["志高", "", "空气净化器新品", "", 12, 24]);
});
