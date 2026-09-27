import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { validateJdStoreRegistry } from "../lib/jd/store-registry";

const store = (storeKey: string, shopId: string, port: number, profileName: string, downloadDir: string) => ({
  storeKey, accountLabel: storeKey, platform: "京东", shopName: storeKey, shopId, enabled: true,
  browser: {
    executablePath: "%LOCALAPPDATA%/Chromium/Application/chrome.exe",
    userDataDir: "%LOCALAPPDATA%/Chromium/User Data",
    profileName,
    profileDir: `%LOCALAPPDATA%/Chromium/User Data/${profileName}`,
    debugPort: port,
    downloadDir,
  },
});

test("store registry resolves and rejects shared browser or download isolation paths", () => {
  const registry = { version: 1, stores: [store("one", "1", 9222, "Default", "D:/downloads/one"), store("two", "2", 9223, "Profile 1", "D:/downloads/two")] };
  const result = validateJdStoreRegistry(registry, "D:/repo", "C:/Users/test/AppData/Local");
  assert.match(result[0]!.browser.profileDir, /Chromium[\\/]User Data[\\/]Default/);
  assert.equal(result[0]!.browser.userDataDir, result[1]!.browser.userDataDir);
  assert.throws(() => validateJdStoreRegistry({ ...registry, stores: [registry.stores[0], store("two", "2", 9223, "Default", "D:/downloads/two")] }, "D:/repo", "C:/Users/test/AppData/Local"), /profileDir/);
  assert.throws(() => validateJdStoreRegistry({ version: 1, stores: [{ ...registry.stores[0], browser: { ...registry.stores[0]!.browser, profileName: "../Default" } }] }, "D:/repo", "C:/Users/test/AppData/Local"), /字段无效/);
  assert.throws(() => validateJdStoreRegistry({ version: 1, stores: [{ ...registry.stores[0], browser: { ...registry.stores[0]!.browser, profileDir: "%LOCALAPPDATA%/Chromium/User Data/Profile 9" } }] }, "D:/repo", "C:/Users/test/AppData/Local"), /精确等于/);
  assert.throws(() => validateJdStoreRegistry({ version: 1, stores: [{ ...registry.stores[0], platform: "淘宝" }] }, "D:/repo", "C:/Users/test/AppData/Local"), /字段无效/);
  assert.throws(() => validateJdStoreRegistry({ version: 1, stores: [{ ...registry.stores[0], storeKey: "Store" }] }, "D:/repo", "C:/Users/test/AppData/Local"), /字段无效/);
  assert.throws(() => validateJdStoreRegistry({ version: 1, stores: [{ ...registry.stores[0], shopId: "A-1" }] }, "D:/repo", "C:/Users/test/AppData/Local"), /字段无效/);
});

test("controlled registry maps all four JD shops to their Chromium profiles", async () => {
  const parsed = JSON.parse(await readFile("config/jd-store-accounts.json", "utf8"));
  const stores = validateJdStoreRegistry(parsed, "D:/repo", "C:/Users/test/AppData/Local");
  assert.deepEqual(stores.map(({ shopName, browser }) => [shopName, browser.profileName]), [
    ["志高商用设备旗舰店", "Default"],
    ["志高商用厨电旗舰店", "Profile 1"],
    ["志高切肉机旗舰店", "Profile 2"],
    ["志高商用洗碗机旗舰店", "Profile 3"],
  ]);
  assert.equal(new Set(stores.map((item) => item.browser.profileDir.toLowerCase())).size, 4);
  assert.ok(stores.every((item) => /Chromium[\\/]Application[\\/]chrome\.exe$/i.test(item.browser.executablePath)));
  assert.ok(stores.every((item) => item.loginMode === "windows_dpapi_credentials"));
  assert.equal(stores.find((item) => item.storeKey === "jd-yiyong-director")?.promotionInitialStartDate, "2026-07-01");
  assert.equal(stores.find((item) => item.storeKey === "jd-maidehao-operator1")?.promotionInitialStartDate, "2026-07-01");
});

test("JD store registry rejects unknown login modes and secret fields", () => {
  const base = store("one", "1", 9222, "Default", "D:/downloads/one");
  assert.throws(() => validateJdStoreRegistry({ version: 1, stores: [{ ...base, loginMode: "plain_password" }] }, "D:/repo", "C:/Users/test/AppData/Local"), /字段无效/);
  assert.throws(() => validateJdStoreRegistry({ version: 1, stores: [{ ...base, promotionInitialStartDate: "2026-02-30" }] }, "D:/repo", "C:/Users/test/AppData/Local"), /字段无效/);
  assert.throws(() => validateJdStoreRegistry({ version: 1, stores: [{ ...base, password: "forbidden" }] }, "D:/repo", "C:/Users/test/AppData/Local"), /不得保存敏感字段/);
});
