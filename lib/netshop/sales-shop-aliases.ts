import jdStoreRegistry from "@/config/jd-store-accounts.json";
import salesImportPolicy from "@/config/sales-import-policy.json";

export type NetshopSalesOutletMatch = {
  platform: string;
  canonicalShopName: string;
  rawShopName: string;
  rawChannel: string | null;
};

type ControlledJdSalesAliasSpec = {
  storeKey: string;
  rawShopName: string;
  rawChannel: string;
};

// These pairs bridge the controlled JD shop registry to the exact shop/channel
// values emitted by the approved ERP sales import. They are intentionally
// explicit: suffix stripping or independent shop/channel alias sets could join
// one store's sales to another store with a similar name.
const controlledJdSalesAliasSpecs: readonly ControlledJdSalesAliasSpec[] = [
  {
    storeKey: "jd-yiyong-director",
    rawShopName: "志高商用设备旗舰店（亿用）",
    rawChannel: "京东-志高商用设备旗舰店（亿用）",
  },
  {
    storeKey: "jd-chudian-weizhang",
    rawShopName: "志高商用厨电旗舰店",
    rawChannel: "京东-志高商用厨电旗舰店",
  },
  {
    storeKey: "jd-maidehao-operator1",
    rawShopName: "志高切肉机旗舰店（志高迈德豪）",
    rawChannel: "京东-志高切肉机旗舰店（志高迈德豪）",
  },
  {
    storeKey: "jd-cuizhiwang-dengweizhang",
    rawShopName: "志高商用洗碗机旗舰店（志高炊之王）",
    rawChannel: "京东-志高商用洗碗机旗舰店（志高炊之王）",
  },
] as const;

function buildControlledJdSalesAliases() {
  const approvedChannels = new Set(salesImportPolicy.approvedSalesChannels);
  const stores = new Map(jdStoreRegistry.stores.map((store) => [store.storeKey, store]));
  const aliases = new Map<string, NetshopSalesOutletMatch>();

  for (const spec of controlledJdSalesAliasSpecs) {
    const store = stores.get(spec.storeKey);
    if (!store || store.platform !== "京东" || !store.shopName.trim()) {
      throw new Error(`京东销售别名引用了无效店铺注册项: ${spec.storeKey}`);
    }
    if (!approvedChannels.has(spec.rawChannel)) {
      throw new Error(`京东销售别名渠道未在销售导入白名单中: ${spec.rawChannel}`);
    }
    if (spec.rawChannel !== `京东-${spec.rawShopName}`) {
      throw new Error(`京东销售别名店铺与渠道不是受控精确组合: ${spec.storeKey}`);
    }
    const canonicalKey = `${store.platform}\u001f${store.shopName.trim()}`;
    if (aliases.has(canonicalKey)) {
      throw new Error(`京东销售别名重复定义标准店铺: ${store.shopName}`);
    }
    aliases.set(canonicalKey, {
      platform: store.platform,
      canonicalShopName: store.shopName.trim(),
      rawShopName: spec.rawShopName,
      rawChannel: spec.rawChannel,
    });
  }

  for (const store of jdStoreRegistry.stores) {
    if (!store.enabled || store.platform !== "京东") continue;
    const canonicalKey = `${store.platform}\u001f${store.shopName.trim()}`;
    if (!aliases.has(canonicalKey)) {
      throw new Error(`启用的京东店铺缺少受控销售别名: ${store.storeKey}`);
    }
  }
  return aliases;
}

const controlledJdSalesAliases = buildControlledJdSalesAliases();

export function resolveNetshopSalesOutletMatches(
  platformValue: string,
  canonicalShopNameValue: string,
): NetshopSalesOutletMatch[] {
  const platform = platformValue.trim();
  const canonicalShopName = canonicalShopNameValue.trim();
  if (!platform || !canonicalShopName) return [];
  const controlled = controlledJdSalesAliases.get(`${platform}\u001f${canonicalShopName}`);
  if (controlled) return [{ ...controlled }];
  return [{
    platform,
    canonicalShopName,
    rawShopName: canonicalShopName,
    rawChannel: null,
  }];
}
