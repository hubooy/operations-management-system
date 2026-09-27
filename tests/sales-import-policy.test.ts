import assert from "node:assert/strict";
import test from "node:test";
import {
  isApprovedSalesChannel,
  isExcludedSalesWarehouse,
  isZeroCostProductName,
  salesImportPolicy,
} from "../lib/sales/import-policy";

test("sales import policy keeps the exact 30-shop whitelist", () => {
  assert.deepEqual(salesImportPolicy.approvedSalesChannels, [
    "志高商用厨电京东自营旗舰店",
    "京东-志高商用厨电旗舰店",
    "京东-志高商用厨电自营旗舰店",
    "京东-志高商用洗碗机旗舰店（志高炊之王）",
    "京东-志高商用西厨旗舰店（志高乐度）",
    "京东-志高商用设备旗舰店（亿用）",
    "京东-志高切肉机旗舰店（志高迈德豪）",
    "阿里巴巴-炊之王店",
    "阿里巴巴-亿用店",
    "阿里巴巴-震坤行",
    "拼多多-志高中式厨电旗舰店（志高亿玥）",
    "拼多多-志高中式厨电旗舰店（志高亿玖）",
    "拼多多-志高商用厨具旗舰店（志高丽力）",
    "拼多多-志高商用厨电旗舰店",
    "拼多多-志高商用餐厨旗舰店（志高乐度）",
    "拼多多-志高商用设备旗舰店",
    "丽力淘工厂",
    "炊之王淘宝企业店",
    "亿玥淘工厂",
    "天猫-志高丽力专卖店",
    "天猫-志高乐度专卖店",
    "天猫-志高拓丰专卖店",
    "天猫-志高亿用专卖店",
    "天猫-志高亿玖专卖店",
    "天猫-志高炊之王专卖店",
    "天猫-志高迈德豪专卖店",
    "天猫-志高马思图专卖店",
    "天猫-志高驰梦专卖店",
    "抖店-志高商业设备旗舰店",
    "抖店-志高炊之王专卖店",
  ]);
  assert.equal(isApprovedSalesChannel("亿玥淘工厂"), true);
  assert.equal(isApprovedSalesChannel("阿里巴巴-炊之王店"), true);
  assert.equal(isApprovedSalesChannel("阿里巴巴-亿用店"), true);
  assert.equal(isApprovedSalesChannel("阿里巴巴-震坤行"), true);
  assert.equal(isApprovedSalesChannel("阿里巴巴-振坤行"), false);
  assert.equal(isApprovedSalesChannel("拼多多-志高中式厨电旗舰店（志高亿玖）"), true);
  assert.equal(isApprovedSalesChannel("天猫-非名单店铺"), false);
});

test("sales import policy uses exact normalized warehouse and price-adjustment values", () => {
  assert.equal(isExcludedSalesWarehouse(" 刷刷仓 "), true);
  assert.equal(isExcludedSalesWarehouse("刷刷仓备用"), false);
  assert.equal(isZeroCostProductName("补差价专用"), true);
  assert.equal(isZeroCostProductName("补差价专用-测试"), false);
});
