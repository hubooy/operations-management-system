import { readFile } from "node:fs/promises";
import path from "node:path";

const projectRoot = path.resolve(process.cwd());
export type JdStore = {
  storeKey: string;
  accountLabel: string;
  platform: "京东";
  shopName: string;
  shopId: string;
  enabled: boolean;
  loginMode?: "manual" | "windows_dpapi_credentials";
  promotionInitialStartDate?: string;
  browser: {
    executablePath: string;
    userDataDir: string;
    profileName: string;
    profileDir: string;
    debugPort: number;
    downloadDir: string;
  };
};

function resolveRegistryPath(value: string, rootDirectory: string, localAppData: string | undefined): string {
  const localAppDataPrefix = /^%LOCALAPPDATA%(?:[\\/]|$)/i;
  let expanded = value;
  if (localAppDataPrefix.test(value)) {
    if (!localAppData?.trim()) throw new Error("京东店铺注册表使用了 %LOCALAPPDATA%，但当前环境未提供 LOCALAPPDATA。");
    expanded = path.join(localAppData, value.replace(localAppDataPrefix, ""));
  }
  if (/%[^%]+%/.test(expanded)) throw new Error(`京东店铺注册表包含不受支持的环境变量路径：${value}`);
  return path.resolve(rootDirectory, expanded);
}

function assertNoSecrets(value: unknown, location: string): void {
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const nextLocation = `${location}.${key}`;
    if (/password|secret|token|cookie/i.test(key)) throw new Error(`京东店铺注册表不得保存敏感字段: ${nextLocation}`);
    assertNoSecrets(child, nextLocation);
  }
}

function validIsoDate(value: string | undefined) {
  if (value === undefined) return true;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

export function validateJdStoreRegistry(parsed: unknown, rootDirectory = projectRoot, localAppData = process.env.LOCALAPPDATA): JdStore[] {
  if (!parsed || typeof parsed !== "object") throw new Error("京东店铺注册表格式无效");
  const registry = parsed as { version?: unknown; stores?: unknown };
  if (registry.version !== 1 || !Array.isArray(registry.stores)) throw new Error("京东店铺注册表格式无效");
  const seen = new Set<string>();
  const shopIds = new Set<string>();
  const ports = new Set<number>();
  const profiles = new Set<string>();
  const downloads = new Set<string>();
  return registry.stores.map((rawStore, index) => {
    if (!rawStore || typeof rawStore !== "object") throw new Error(`京东店铺注册表第 ${index + 1} 项无效`);
    const store = rawStore as JdStore;
    assertNoSecrets(store, `stores.${store.storeKey}`);
    if (!store.storeKey?.trim() || !/^[a-z0-9][a-z0-9-]*$/.test(store.storeKey) || !store.accountLabel?.trim() || !store.shopName?.trim() || !/^\d+$/.test(store.shopId ?? "")
      || typeof store.enabled !== "boolean" || store.platform !== "京东" || !store.browser
      || store.loginMode !== undefined && !["manual", "windows_dpapi_credentials"].includes(store.loginMode)
      || !validIsoDate(store.promotionInitialStartDate)
      || typeof store.browser.executablePath !== "string" || !store.browser.executablePath.trim()
      || typeof store.browser.userDataDir !== "string" || !store.browser.userDataDir.trim()
      || typeof store.browser.profileName !== "string" || !/^(?:Default|Profile [1-9]\d*)$/.test(store.browser.profileName)
      || typeof store.browser.profileDir !== "string" || !store.browser.profileDir.trim()
      || typeof store.browser.downloadDir !== "string" || !store.browser.downloadDir.trim()
      || !Number.isInteger(store.browser.debugPort) || store.browser.debugPort < 1 || store.browser.debugPort > 65_535) {
      throw new Error(`京东店铺注册表字段无效: stores[${index}]`);
    }
    const executablePath = resolveRegistryPath(store.browser.executablePath, rootDirectory, localAppData);
    const userDataDir = resolveRegistryPath(store.browser.userDataDir, rootDirectory, localAppData);
    const profileDir = resolveRegistryPath(store.browser.profileDir, rootDirectory, localAppData);
    const downloadDir = resolveRegistryPath(store.browser.downloadDir, rootDirectory, localAppData);
    const expectedProfileDir = path.join(userDataDir, store.browser.profileName);
    if (profileDir.toLowerCase() !== expectedProfileDir.toLowerCase()) {
      throw new Error(`京东店铺注册表 profileDir 必须精确等于 userDataDir/profileName：${store.storeKey}`);
    }
    const storeKey = store.storeKey.toLowerCase();
    if (seen.has(storeKey) || shopIds.has(store.shopId) || ports.has(store.browser.debugPort)
      || profiles.has(profileDir.toLowerCase()) || downloads.has(downloadDir.toLowerCase())) {
      throw new Error(`京东店铺注册表存在重复键、shopId、端口、profileDir 或 downloadDir: ${store.storeKey}`);
    }
    seen.add(storeKey); shopIds.add(store.shopId); ports.add(store.browser.debugPort);
    profiles.add(profileDir.toLowerCase()); downloads.add(downloadDir.toLowerCase());
    return { ...store, browser: { ...store.browser, executablePath, userDataDir, profileDir, downloadDir } };
  });
}

export async function loadJdStores(): Promise<JdStore[]> {
  const file = path.join(projectRoot, "config", "jd-store-accounts.json");
  return validateJdStoreRegistry(JSON.parse(await readFile(file, "utf8")), projectRoot);
}

export async function getJdStore(storeKey: string): Promise<JdStore> {
  const store = (await loadJdStores()).find((item) => item.storeKey === storeKey);
  if (!store) throw new Error(`未找到京东店铺注册项: ${storeKey}`);
  return store;
}
