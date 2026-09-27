import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { parseCustomerServiceAnalysisReply } from "../lib/customer-service/contracts";
import { buildCustomerServiceProductMappings } from "../lib/customer-service/product-mapping";

test("customer-service AI analysis accepts only bounded enum values and requested ids", () => {
  const rows = parseCustomerServiceAnalysisReply(`\n\`\`\`json\n[{"id":12,"robotScope":"contains_robot","problemType":"安装使用","conversionStatus":"not_converted","serviceIssues":"未及时说明接线步骤","summaryText":"顾客咨询接线，客服给出步骤。"},{"id":99,"robotScope":"bad","problemType":"未知","conversionStatus":"converted"}]\n\`\`\``, new Set([12]));
  assert.deepEqual(rows, [{ id: 12, robotScope: "contains_robot", problemType: "安装使用", conversionStatus: "not_converted", serviceIssues: "未及时说明接线步骤", summaryText: "顾客咨询接线，客服给出步骤。" }]);
});

test("customer-service AI analysis rejects non-JSON model output", () => {
  assert.throws(() => parseCustomerServiceAnalysisReply("分析完成", new Set([1])), /有效 JSON/);
});

test("customer-service AI analysis accepts unknown conversion status", () => {
  const rows = parseCustomerServiceAnalysisReply(
    `[{"id":7,"robotScope":"exclude_robot","problemType":"商品咨询","conversionStatus":"unknown","serviceIssues":"未发现明显服务问题","summaryText":"聊天记录不足，无法判断是否成交。"}]`,
    new Set([7]),
  );
  assert.equal(rows[0]?.conversionStatus, "unknown");
});

test("customer-service page checks model readiness and analyzes every visible unlabelled row in bounded batches", async () => {
  const [page, route] = await Promise.all([
    readFile(new URL("../app/customer-service-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/customer-service/analyze/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(route, /export async function GET\(\)/);
  assert.match(route, /resolveChatModel/);
  assert.match(page, /analysisReady !== true/);
  assert.match(page, /for \(let offset = 0; offset < ids\.length; offset \+= 8\)/);
  assert.doesNotMatch(page, /filter\(\(item\) => !item\.analyzedAt\)\.slice\(0, 8\)/);
});

test("customer-service imports scope file identity by shop", async () => {
  const [directRoute, chunkRoute] = await Promise.all([
    readFile(new URL("../app/api/customer-service/import/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/customer-service/import/chunks/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(directRoute, /`\$\{resolvedShopName\}:\$\{await digest\(sessionBytes\)\}/);
  assert.match(chunkRoute, /`\$\{resolvedShopName\}:\$\{await digest\(sessionBytes\)\}/);
});

test("customer-service page keeps the paired-file import available beside analysis", async () => {
  const page = await readFile(new URL("../app/customer-service-view.tsx", import.meta.url), "utf8");
  assert.match(page, /<CustomerServiceImportCard canImport=\{canImport\} onCompleted=\{load\} \/>/);
  assert.match(page, /可在本页直接导入/);
});

test("customer-service category filter and display use the netshop SKU to Jackyun sales chain", async () => {
  const [page, route, database, mapping] = await Promise.all([
    readFile(new URL("../app/customer-service-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/customer-service/conversations/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/customer-service/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/customer-service/product-mapping.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /吉客云类目筛选/);
  assert.match(page, /categories\.forEach\(\(value\) => params\.append\("category", value\)\)/);
  assert.match(route, /searchParams\.getAll\("category"\)/);
  assert.match(database, /createDjangoCustomerService/);
  assert.match(database, /operation: "customer_service_products"/);
  assert.match(database, /readSalesProducts/);
  assert.match(mapping, /onlineSpecCode: String\(raw\["商家SKU"\]/);
  assert.doesNotMatch(database, /FROM sales_order_lines/);
  assert.doesNotMatch(database, /FROM customer_service_conversations/);
  assert.match(database, /categories: categoryOptions/);
  assert.doesNotMatch(database, /catalog\.get\(`\$\{item\.shopName\}/);
});

test("customer-service product mapping supports a unique reverse fallback", () => {
  const masterRows = [
    { sku_id: "JD-1", spu_id: "SPU-1", product_code: "SPU-1", raw_json: JSON.stringify({ 商家SKU: "SHOP-1" }) },
    { sku_id: "JD-2", spu_id: "SPU-2", product_code: "SPU-2", raw_json: JSON.stringify({ 商家SKU: "SHOP-2" }) },
  ];
  const salesRows = [
    { online_spec_code: "SHOP-1", product_code: "ERP-1", category: "饮水设备" },
    { online_spec_code: "SHOP-2", product_code: "ERP-2", category: "制冰设备" },
  ];
  const mappings = buildCustomerServiceProductMappings(["JD-1", "SHOP-2"], masterRows, salesRows);
  assert.deepEqual(mappings.get("JD-1"), {
    matchedSkuId: "JD-1", spuId: "SPU-1", onlineSpecCode: "SHOP-1", erpProductCode: "ERP-1", category: "饮水设备", matchDirection: "forward",
  });
  assert.deepEqual(mappings.get("SHOP-2"), {
    matchedSkuId: "JD-2", spuId: "SPU-2", onlineSpecCode: "SHOP-2", erpProductCode: "ERP-2", category: "制冰设备", matchDirection: "reverse",
  });
});

test("customer-service reverse fallback rejects an ambiguous online specification code", () => {
  const masterRows = [
    { sku_id: "JD-A", spu_id: "", product_code: "", raw_json: JSON.stringify({ 商家SKU: "SHARED" }) },
    { sku_id: "JD-B", spu_id: "", product_code: "", raw_json: JSON.stringify({ 商家SKU: "SHARED" }) },
  ];
  const mappings = buildCustomerServiceProductMappings(["SHARED"], masterRows, [
    { online_spec_code: "SHARED", product_code: "ERP-X", category: "共享类目" },
  ]);
  assert.equal(mappings.has("SHARED"), false);
});

test("customer-service list exposes SKUID, Jackyun number, and category through bounded consumers", async () => {
  const [page, database, mapping] = await Promise.all([
    readFile(new URL("../app/customer-service-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/customer-service/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/customer-service/product-mapping.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /SKUID \/ 吉客云编号/);
  assert.match(page, /吉客云编号 \{item\.erpProductCode\}/);
  assert.match(database, /productCodes\.forEach/);
  assert.match(database, /matchedSkuId: matched\?\.matchedSkuId/);
  assert.match(database, /CUSTOMER_SERVICE_CONVERSATIONS_PATH/);
  assert.match(mapping, /candidates\.length !== 1/);
});

test("customer-service unknown conversion stays synchronized across AI prompt, UI, and tool schema", async () => {
  const [analysis, page, registry] = await Promise.all([
    readFile(new URL("../lib/customer-service/analysis.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/customer-service-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/tool-registry.ts", import.meta.url), "utf8"),
  ]);
  assert.match(analysis, /unknown（聊天记录不足，无法判断是否转化）/);
  assert.match(page, /\{ value: "unknown", label: "未知" \}/);
  assert.match(registry, /conversionStatus: \{ type: "string", enum: \["converted", "not_converted", "unknown"\] \}/);
  assert.match(registry, /category: \{ type: "string", maxLength: 120/);
});
