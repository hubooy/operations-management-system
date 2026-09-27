import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { prepareNormalizedNetshopImport } from "../lib/netshop/normalized-import";

const python = path.resolve(".runtime/test-venv/Scripts/python.exe");
// Execute the real scalar/projection functions with the actual normalized
// payload. No Django initialization, fake database publication or network.
const projection = String.raw`
import ast,json,sys,math,re
from pathlib import Path
from collections.abc import Mapping
sys.path.insert(0,"backend")
from business_analysis import test_promotion_views as f
from business_analysis import promotion_keyword_sku as joint
rows=json.loads(sys.stdin.buffer.read().decode("utf-8"))
code=ast.parse(Path("backend/netshop/import_service.py").read_text(encoding="utf-8"))
names={"_metric_number","_js_round","_metric_integer","_raw_text","_raw_number","_row_projection"}
scope={"Mapping":Mapping,"math":math,"re":re,"JS_SAFE_INTEGER":2**53-1,"NetshopApiError":ValueError}
exec(compile(ast.Module(body=[n for n in code.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),"actual-import-projection","exec"),scope)
projected=[]
facts=[]
for i,row in enumerate(rows,1):
    source={"id":i,"source":"jd_promotion","source_row_hash":f.digest(row),"last_import_batch_id":"synthetic-normalized",
        "platform":row["platform"],"shop_name":row["shopName"],"business_date":row["businessDate"],"snapshot_date":"",
        "sku_id":row["skuId"],"spu_id":row["spuId"],"product_code":row["productCode"],"product_name":row["productName"],
        "category":"","metrics_json":row["metrics"],"raw_json":row["raw"]}
    typed=scope["_row_projection"](row)
    source.update(typed)
    projected.append(f.PROJECT["_project"](source,f.PROJECT["PROMOTION_METRICS"]))
    facts.append({"raw":row["raw"],"metrics":{names[0]:projected[-1]["metrics"][key] for key,(column,names) in f.PROJECT["PROMOTION_METRICS"].items()}})
data=f.fixture(facts)
with joint.table(*data) as table:
    groups=list(table.scan())
    header=table.header()
print(json.dumps({"projected":projected,"groups":groups,"authorityVerified":header["authorityVerified"]},ensure_ascii=True))
`;

async function imported(roles: [string, string][]) {
  const fields: [string, string][] = [["日期", "2026-08-01"], ...roles,
    ["关键词", "切肉机"], ["搜索词", "顾客另一搜索词"], ["计划ID", "P-001"],
    ["单元ID", "U-001"], ["匹配类型", "精确"], ["花费", "1.23"],
    ["总订单金额", "5.00"], ["展现数", "100"], ["点击数", "10"], ["总订单行", "2"]];
  const bytes = new TextEncoder().encode("\uFEFF" + fields.map(([k]) => k).join(",") + "\n" + fields.map(([, v]) => v).join(","));
  const result = await prepareNormalizedNetshopImport({ bytes, fileName: "synthetic-jd-ad-2026-08-01.csv",
    fileSizeBytes: bytes.length, source: "jd_promotion", platform: "京东", shopName: "合成甲店" });
  assert.equal(result.disposition, "prepared", JSON.stringify(result.errors));
  assert.equal(result.dataset, "ad");
  assert.equal(result.rows?.length, 1);
  return result.rows!;
}

function projected(rows: Awaited<ReturnType<typeof imported>>) {
  assert.ok(existsSync(python), "Use the repository isolated pure-test Python runtime");
  const result = spawnSync(python, ["-c", projection], { encoding: "utf8", input: JSON.stringify(rows), timeout: 15000 });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test("real normalized JD import preserves promoted role when first generic SKU is trigger or attribution", async () => {
  for (const first of ["触发SKU ID", "跟单SKU ID"]) {
    const rows = await imported([[first, "FIRST-OTHER"], ["智能投放推广SKU ID", "PROMOTED-001"]]);
    // This is the existing generic-SKU limitation, deliberately not fixed here.
    assert.equal(rows[0].skuId, "FIRST-OTHER");
    assert.equal(rows[0].raw["智能投放推广SKU ID"], "PROMOTED-001");
    const output = projected(rows);
    assert.equal(output.projected[0].skuId, "FIRST-OTHER");
    assert.equal(output.projected[0].dimensions.promotedSkuId, "PROMOTED-001");
    assert.equal(output.groups[0].entity.promotedSkuId, "PROMOTED-001");
    assert.equal(output.groups[0].entity.keyword, "切肉机");
    assert.equal(output.groups[0].metrics.spendCents.value, 123);
    assert.equal(output.groups[0].metrics.reportedGmvCents.value, 500);
    assert.equal(output.authorityVerified, false);
  }
});

test("all exact promoted-SKU projection aliases survive the actual importer", async () => {
  for (const key of ["智能投放推广SKU ID", "智能投放推广SKU", "推广SKU"]) {
    const rows = await imported([["触发SKU ID", "TRIGGER"], [key, "001"], ["跟单SKU ID", "ATTRIBUTED"]]);
    const output = projected(rows);
    assert.equal(output.projected[0].dimensions.promotedSkuId, "001");
    assert.equal(output.projected[0].dimensions.triggerSkuId, "TRIGGER");
    assert.equal(output.projected[0].dimensions.attributedSkuId, "ATTRIBUTED");
    assert.equal(output.groups[0].identityQualified, true);
  }
});

test("trigger/attribution/generic SKU alone never becomes the promoted SKU", async () => {
  const rows = await imported([["触发SKU ID", "TRIGGER"], ["跟单SKU ID", "ATTRIBUTED"], ["SKU", "GENERIC"]]);
  const output = projected(rows);
  assert.equal(output.projected[0].dimensions.promotedSkuId, null);
  assert.equal(output.groups[0].entity.promotedSkuId, null);
  assert.equal(output.groups[0].identityQualified, false);
  assert.deepEqual(output.groups[0].missingIdentityFields, ["promotedSkuId"]);
  assert.equal(output.groups[0].metrics.spendCents.value, 123);
});
