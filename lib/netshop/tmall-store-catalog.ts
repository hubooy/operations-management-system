import registryData from "@/config/tmall-store-accounts.json" with { type: "json" };

export type BundledTmallStore = {
  storeKey: string;
  platform: "天猫";
  shopName: string;
  enabled: boolean;
  loginMode?: "manual" | "saved_browser_credentials" | "windows_dpapi_credentials";
  productMasterExportMode?: "product_manager" | "on_sale_pagewise_excel";
  productMasterCadence?: {
    intervalDays: number;
    initialDueDate: string;
  };
  initialStartDate: string | null;
  portalUrl: string;
  browser: {
    executablePath?: string;
    userDataDir?: string;
    profileName?: string;
    profileDir: string;
    debugPort: number;
    downloadDir: string;
  };
};

type BundledRegistry = { version: number; stores: BundledTmallStore[] };
export const tmallStoreRegistryData = registryData as BundledRegistry;

function validIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function assertNoSecrets(value: unknown, location: string): void {
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const nextLocation = `${location}.${key}`;
    if (/password|secret|token|cookie|session/i.test(key)) {
      throw new Error(`天猫店铺注册表不得保存敏感字段: ${nextLocation}`);
    }
    assertNoSecrets(child, nextLocation);
  }
}

function validateBundledRegistry() {
  assertNoSecrets(tmallStoreRegistryData, "registry");
  if (tmallStoreRegistryData.version !== 1 || !Array.isArray(tmallStoreRegistryData.stores)) {
    throw new Error("天猫店铺注册表格式无效");
  }
  const keys = new Set<string>();
  const shops = new Set<string>();
  for (const [index, store] of tmallStoreRegistryData.stores.entries()) {
    if (!store.storeKey || !store.shopName || store.platform !== "天猫" || typeof store.enabled !== "boolean"
      || store.loginMode !== undefined && !["manual", "saved_browser_credentials", "windows_dpapi_credentials"].includes(store.loginMode)
      || store.productMasterExportMode !== undefined
        && !["product_manager", "on_sale_pagewise_excel"].includes(store.productMasterExportMode)
      || store.productMasterCadence !== undefined && (
        !Number.isInteger(store.productMasterCadence.intervalDays)
        || store.productMasterCadence.intervalDays < 1 || store.productMasterCadence.intervalDays > 30
        || !validIsoDate(store.productMasterCadence.initialDueDate)
      )) {
      throw new Error(`天猫店铺注册表字段无效: stores[${index}]`);
    }
    if (keys.has(store.storeKey) || shops.has(store.shopName)) {
      throw new Error(`天猫店铺注册表存在重复键或店铺: ${store.storeKey}`);
    }
    keys.add(store.storeKey);
    shops.add(store.shopName);
  }
  return tmallStoreRegistryData.stores;
}

const bundledStores = validateBundledRegistry();

export function enabledTmallStoreCatalog() {
  return bundledStores.filter((store) => store.enabled);
}

export function resolveEnabledTmallShop(shopName?: string) {
  const enabled = enabledTmallStoreCatalog();
  const normalized = String(shopName ?? "").replace(/\s+/g, " ").trim();
  if (!normalized) {
    if (enabled.length === 1) return enabled[0]!;
    throw new Error("天猫导入必须明确指定已启用的店铺");
  }
  const store = enabled.find((item) => item.shopName === normalized);
  if (!store) throw new Error(`天猫店铺未注册或未启用: ${normalized}`);
  return store;
}
