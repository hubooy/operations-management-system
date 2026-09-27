import { readFile, writeFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const read = async (path) => JSON.parse((await readFile(new URL(path, root), "utf8")).replace(/^\uFEFF/, ""));

// Only this allowlisted display projection enters the browser. Never bundle
// browser profiles, download paths, credentials, or executable node parameters.
export async function buildImportChainCatalog() {
  const jd = (await read("config/jd-store-accounts.json")).stores.filter((s) => s.enabled);
  const tmall = (await read("config/tmall-store-accounts.json")).stores.filter((s) => s.enabled);
  const market = await read("config/jd-market-ranking-daily.json");
  const entities = [{ key: "jackyun", name: "吉客云 ERP", platform: "ERP" },
    ...jd.map((s) => ({ key: s.storeKey, name: s.shopName, platform: "京东" })),
    ...tmall.map((s) => ({ key: s.storeKey, name: s.shopName.replace(/^天猫-/, ""), platform: "天猫" }))];
  const chains = [
    { key: "jackyun", label: "吉客云 ERP · 五表", platform: "ERP", modules: ["货品主数据", "分仓库存", "库龄", "销售明细", "组合装"], steps: ["建立计划", "五表导出", "完整文件校验", "按依赖顺序导入", "独立批次回查"] },
    { key: "jd", label: "京东 · 商品数据", platform: "京东", modules: ["商品 SKU 主数据", "SKU 分天", "SPU 分天"], steps: ["固定日期与店铺", "逐店串行下载并导入", "批次与日期覆盖回查"] },
    { key: "jd_market", label: "京东商智 · 市场榜单", platform: "京东", modules: market.categories.map((c) => c.systemCategory), steps: ["计算榜单缺失日", "分块下载、校验并导入", "原目标日期覆盖回查"] },
    { key: "jd_promotion", label: "京准通 · AI 推广", platform: "京东", modules: ["推广商品日数据"], steps: ["固定店铺与日期", "生成并下载报表", "校验、导入与回查"] },
    { key: "tmall", label: "天猫 · 商品、推广与主数据", platform: "天猫", modules: ["生意参谋商品日数据", "推广商品日数据", "店铺货品主数据"], steps: ["规划缺失日期", "商品日数据下载", "签收、导入并回查", "推广下载、导入并回查", "按周期更新主数据"] },
  ];
  const rules = [];
  const add = async (chainKey, entityKeys, file, extra = {}) => {
    const definition = await read(`automation/n8n/${file}.workflow.json`);
    const schedules = definition.nodes.filter((n) => n.type === "n8n-nodes-base.scheduleTrigger")
      .flatMap((n) => n.parameters.rule.interval)
      .map((s) => s.expression || `每 ${s.daysInterval || 1} 天 ${s.triggerAtHour || 0}:${String(s.triggerAtMinute || 0).padStart(2, "0")}`);
    rules.push({ chainKey, entityKeys, workflowId: definition.id, name: definition.name,
      timezone: definition.settings?.timezone || "未配置", schedules, definitionFile: `automation/n8n/${file}.workflow.json`, ...extra });
  };
  await add("jackyun", ["jackyun"], "jackyun-five-dataset-api");
  await add("jd", jd.map((s) => s.storeKey), "jd-multi-store-daily.chromium-silent-copy");
  await add("jd_market", [market.storeKey], "jd-market-ranking-daily.chromium-silent-copy");
  await add("jd_promotion", ["jd-yiyong-director"], "jd-promotion-daily");
  await add("jd_promotion", ["jd-maidehao-operator1"], "jd-promotion-cut-meat-20260813-14");
  for (const s of tmall) {
    const file = s.storeKey === "tmall-yijiu" || s.storeKey === "tmall-yiyong"
      ? `${s.storeKey}-direct-pm-candidate` : `${s.storeKey}-sycm-cookie-daily`;
    await add("tmall", [s.storeKey], file, { masterIntervalDays: s.productMasterCadence?.intervalDays });
  }
  return { source: "repository_definitions", entities, chains, rules };
}

if (process.argv[1] && import.meta.url === (await import("node:url")).pathToFileURL(process.argv[1]).href) {
  const catalog = await buildImportChainCatalog();
  await writeFile(new URL("lib/imports/chain-catalog.generated.json", root), `${JSON.stringify(catalog, null, 2)}\n`);
  await writeFile(new URL("backend/workflow/import_chain_catalog.json", root), `${JSON.stringify({ workflowIds: catalog.rules.map(r => r.workflowId) }, null, 2)}\n`);
}
