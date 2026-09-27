import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {
  enabledTmallStoreCatalog,
  resolveEnabledTmallShop,
  tmallStoreRegistryData,
} from "../lib/netshop/tmall-store-catalog";
import {
  resolveEnabledRegisteredTmallStore,
  resolveRegisteredTmallStore,
  resolveTmallBrowserLaunchTarget,
  type TmallStore,
  validateTmallStoreRegistry,
} from "../lib/netshop/tmall-store-registry";

function store(storeKey: string, shopName: string, port: number): TmallStore {
  return {
    storeKey,
    platform: "天猫" as const,
    shopName,
    enabled: true,
    initialStartDate: "2026-07-31",
    portalUrl: "https://sycm.taobao.com/portal/home.htm",
    browser: { profileDir: `.runtime/${storeKey}`, debugPort: port, downloadDir: `downloads/${storeKey}` },
  };
}

test("天猫店铺注册表隔离 profile、端口和下载目录", () => {
  const result = validateTmallStoreRegistry({ version: 1, stores: [store("a", "A店", 9301), store("b", "B店", 9302)] }, "C:\\workspace");
  assert.equal(result.length, 2);
  assert.notEqual(result[0]!.browser.profileDir, result[1]!.browser.profileDir);
  assert.notEqual(result[0]!.browser.downloadDir, result[1]!.browser.downloadDir);
});

test("天猫店铺注册表拒绝敏感字段和跨店重复资源", () => {
  const withPassword = { ...store("a", "A店", 9301), password: "do-not-store" };
  assert.throws(() => validateTmallStoreRegistry({ version: 1, stores: [withPassword] }), /不得保存敏感字段/);
  const duplicate = store("b", "B店", 9301);
  duplicate.browser.profileDir = store("a", "A店", 9301).browser.profileDir;
  assert.throws(() => validateTmallStoreRegistry({ version: 1, stores: [store("a", "A店", 9301), duplicate] }), /存在重复/);
  assert.throws(() => validateTmallStoreRegistry({
    version: 1,
    stores: [{ ...store("c", "C店", 9303), loginMode: "plain_password" }],
  }), /字段无效/);
  assert.throws(() => validateTmallStoreRegistry({
    version: 1,
    stores: [{ ...store("c", "C店", 9303), productMasterExportMode: "unsafe_mode" }],
  }), /字段无效/);
  assert.throws(() => validateTmallStoreRegistry({
    version: 1,
    stores: [{ ...store("c", "C店", 9303), productMasterCadence: { intervalDays: 0, initialDueDate: "2026-08-25" } }],
  }), /字段无效/);
  assert.throws(() => validateTmallStoreRegistry({
    version: 1,
    stores: [{ ...store("c", "C店", 9303), productMasterCadence: { intervalDays: 3, initialDueDate: "bad-date" } }],
  }), /字段无效/);
  assert.throws(() => validateTmallStoreRegistry({
    version: 1,
    stores: [{ ...store("c", "C店", 9303), productMasterCadence: { intervalDays: 3, initialDueDate: "2026-02-30" } }],
  }), /字段无效/);
});

test("天猫店铺注册表支持显式 Chromium 根目录和 Profile", () => {
  const shared = store("tmall-yijiu", "天猫-志高亿玖专卖店", 9334);
  shared.browser = {
    ...shared.browser,
    executablePath: "%LOCALAPPDATA%/Chromium/Application/chrome.exe",
    userDataDir: "%LOCALAPPDATA%/Chromium/User Data",
    profileName: "Profile 4",
    profileDir: "%LOCALAPPDATA%/Chromium/User Data/Profile 4",
  };
  const [result] = validateTmallStoreRegistry(
    { version: 1, stores: [shared] },
    "D:\\workspace",
    "C:\\Users\\test\\AppData\\Local",
  );
  assert.equal(result!.browser.profileName, "Profile 4");
  assert.match(result!.browser.userDataDir!, /Chromium[\\/]User Data$/);
  assert.match(result!.browser.profileDir, /Chromium[\\/]User Data[\\/]Profile 4$/);
  assert.match(result!.browser.executablePath!, /Chromium[\\/]Application[\\/]chrome\.exe$/);
  assert.deepEqual(resolveTmallBrowserLaunchTarget(result!, "D:\\fallback\\chrome.exe"), {
    executablePath: result!.browser.executablePath,
    profileDirectory: result!.browser.userDataDir,
    profileName: "Profile 4",
  });
});

test("跨店不同 Profile 也不能复用同一个 Chromium userDataDir", () => {
  const stores = [store("a", "A店", 9301), store("b", "B店", 9302)];
  for (const [index, item] of stores.entries()) {
    const profileName = `Profile ${index + 1}`;
    item.browser = { ...item.browser, executablePath: "browser/chrome.exe", userDataDir: "shared-root", profileName, profileDir: `shared-root/${profileName}` };
  }
  assert.throws(() => validateTmallStoreRegistry({ version: 1, stores }), /重复 Chromium userDataDir/);
});

test("天猫店铺注册表拒绝 userDataDir 与 profileDir 错位", () => {
  const mismatched = store("tmall-yijiu", "天猫-志高亿玖专卖店", 9334);
  mismatched.browser = {
    ...mismatched.browser,
    executablePath: "%LOCALAPPDATA%/Chromium/Application/chrome.exe",
    userDataDir: "%LOCALAPPDATA%/Chromium/User Data",
    profileName: "Profile 4",
    profileDir: "%LOCALAPPDATA%/Chromium/User Data/Profile 3",
  };
  assert.throws(() => validateTmallStoreRegistry(
    { version: 1, stores: [mismatched] },
    "D:\\workspace",
    "C:\\Users\\test\\AppData\\Local",
  ), /profileDir 必须精确等于 userDataDir\/profileName/);
});

test("服务端只接受注册表中启用的天猫店铺", () => {
  assert.equal(enabledTmallStoreCatalog().length, 6);
  assert.throws(() => resolveEnabledTmallShop(), /必须明确指定已启用的店铺/);
  const yijiu = resolveEnabledTmallShop("天猫-志高亿玖专卖店");
  assert.equal(yijiu.storeKey, "tmall-yijiu");
  assert.equal(yijiu.browser.userDataDir, "%LOCALAPPDATA%/Chromium-Tmall-Yijiu/User Data");
  assert.equal(yijiu.browser.profileName, "Default");
  assert.equal(yijiu.browser.profileDir, "%LOCALAPPDATA%/Chromium-Tmall-Yijiu/User Data/Default");
  assert.equal(yijiu.browser.debugPort, 9334);
  assert.equal(yijiu.loginMode, "windows_dpapi_credentials");
  assert.equal(yijiu.productMasterCadence?.intervalDays, 1);
  assert.equal(resolveEnabledTmallShop("天猫-志高丽力专卖店").storeKey, "tmall-lili");
  assert.throws(() => resolveEnabledTmallShop("天猫-志高乐度专卖店"), /未注册或未启用/);
});

test("新增五店完成首次登录后启用且继续使用独立 Chromium 根目录", () => {
  const selectedKeys = ["tmall-lili", "tmall-tuofeng", "tmall-yiyong", "tmall-cuizhiwang", "tmall-masitu"];
  const stores = validateTmallStoreRegistry(
    { version: 1, stores: tmallStoreRegistryData.stores },
    "D:\\workspace",
    "C:\\Users\\test\\AppData\\Local",
  );
  const selected = selectedKeys.map((storeKey) => resolveRegisteredTmallStore(stores, storeKey));
  assert.equal(selected.every((item) => item.enabled === true), true);
  assert.equal(selected.every((item) => item.loginMode === "windows_dpapi_credentials"), true);
  assert.equal(selected.every((item) => item.initialStartDate === "2026-08-01"), true);
  for (const item of selected) {
    assert.equal(item.productMasterCadence?.intervalDays, ["tmall-yiyong", "tmall-lili"].includes(item.storeKey) ? 1 : 3);
  }
  assert.deepEqual(
    ["tmall-yijiu", "tmall-lili", "tmall-tuofeng", "tmall-cuizhiwang", "tmall-masitu", "tmall-yiyong"]
      .map((storeKey) => [storeKey, resolveRegisteredTmallStore(stores, storeKey).productMasterCadence?.initialDueDate]),
    [
      ["tmall-yijiu", "2026-08-27"],
      ["tmall-lili", "2026-08-27"],
      ["tmall-tuofeng", "2026-08-25"],
      ["tmall-cuizhiwang", "2026-08-25"],
      ["tmall-masitu", "2026-08-26"],
      ["tmall-yiyong", "2026-08-26"],
    ],
  );
  assert.deepEqual(
    ["tmall-yijiu", "tmall-tuofeng", "tmall-cuizhiwang", "tmall-masitu"].map(
      (storeKey) => resolveRegisteredTmallStore(stores, storeKey).productMasterExportMode,
    ),
    ["on_sale_pagewise_excel", "on_sale_pagewise_excel", "on_sale_pagewise_excel", "on_sale_pagewise_excel"],
  );
  assert.equal(resolveRegisteredTmallStore(stores, "tmall-lili").productMasterExportMode, undefined);
  assert.equal(resolveRegisteredTmallStore(stores, "tmall-yiyong").productMasterExportMode, undefined);
  assert.equal(selected.every((item) => item.browser.profileName === "Default"), true);
  assert.equal(new Set(selected.map((item) => item.browser.userDataDir?.toLowerCase())).size, selected.length);
  assert.equal(new Set(selected.map((item) => item.browser.debugPort)).size, selected.length);
  assert.equal(new Set(selected.map((item) => item.browser.downloadDir.toLowerCase())).size, selected.length);
  for (const item of selected) {
    assert.equal(item.browser.profileDir.toLowerCase(), path.join(item.browser.userDataDir!, "Default").toLowerCase());
    assert.equal(resolveEnabledRegisteredTmallStore(stores, item.storeKey).shopName, item.shopName);
  }
  assert.throws(() => resolveEnabledRegisteredTmallStore(stores, "tmall-ledu"), /尚未启用/);
  assert.throws(() => resolveRegisteredTmallStore(stores, "tmall-unknown"), /未找到天猫店铺注册项/);
});
