import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import test from "node:test";

const MARKET_ENTRY_MAX_BYTES = 640_000;
const MARKET_MASTER_ADMIN_CHUNK_MAX_BYTES = 180_000;
const ADMIN_RUNTIME_MARKER = "create_price_recognition_job";

type ClientManifestEntry = {
  file?: string;
  imports?: string[];
  src?: string;
};

function collectStaticClientFiles(
  manifest: Record<string, ClientManifestEntry>,
  entryKey: string,
) {
  const pending = [entryKey];
  const visited = new Set<string>();
  const files = new Set<string>();
  while (pending.length > 0) {
    const key = pending.pop();
    if (!key || visited.has(key)) continue;
    visited.add(key);
    const entry = manifest[key];
    if (!entry) continue;
    if (entry.file) files.add(entry.file);
    for (const importedKey of entry.imports ?? []) pending.push(importedKey);
  }
  return files;
}

test("market ranking keeps master administration behind a direct lazy boundary", async () => {
  const [marketView, masterAdmin, settingsView] = await Promise.all([
    readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/market-master-admin-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/settings-view.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(
    marketView,
    /Component: LazyMarketMasterAdminPanel \} = createReloadableLazy\("market", \(\) => import\("\.\/market-master-admin-panel"\)\);/,
  );
  assert.doesNotMatch(marketView, /from "\.\/market-master-admin-panel"/);
  assert.doesNotMatch(marketView, new RegExp(ADMIN_RUNTIME_MARKER));
  assert.doesNotMatch(marketView, /run_brand_recognition_job_batch|market-master-product-grid/);
  assert.match(masterAdmin, new RegExp(ADMIN_RUNTIME_MARKER));
  assert.match(masterAdmin, /run_brand_recognition_job_batch/);
  assert.match(masterAdmin, /market-master-product-grid/);

  assert.match(
    settingsView,
    /Component: LazyMarketMasterAdminPanel \} = createReloadableLazy\("settings", \(\) => import\("\.\/market-master-admin-panel"\)\);/,
  );
  assert.doesNotMatch(
    settingsView,
    /import\("\.\/market-view"\)\.then\(\(module\) => \(\{\s*default: module\.MarketMasterAdminPanel/,
  );
  assert.match(settingsView, /activeTab === "master" && <section[\s\S]*?<LazyMarketMasterAdminPanel/);
  assert.match(marketView, /activeSection === "settings" && settingsData && <MarketSettingsWorkspace/);
});

test("fresh production artifacts keep market administration out of the page entry budget", async (context) => {
  const manifestUrl = new URL("../dist/client/.vite/manifest.json", import.meta.url);
  let manifestStat;
  try {
    manifestStat = await stat(manifestUrl);
  } catch {
    context.skip("production client artifacts are not present");
    return;
  }

  const sourceStats = await Promise.all([
    stat(new URL("../app/market-view.tsx", import.meta.url)),
    stat(new URL("../app/market-master-admin-panel.tsx", import.meta.url)),
    stat(new URL("../app/market-master-admin-contract.ts", import.meta.url)),
    stat(new URL("../app/settings-view.tsx", import.meta.url)),
    stat(new URL("../app/shell/reloadable-lazy.tsx", import.meta.url)),
  ]);
  if (manifestStat.mtimeMs < Math.max(...sourceStats.map((item) => item.mtimeMs))) {
    context.skip("production client artifacts predate the chunk boundary sources");
    return;
  }

  const manifest = JSON.parse(await readFile(manifestUrl, "utf8")) as Record<string, ClientManifestEntry>;
  const pageEntry = manifest["app/page.tsx"]?.file;
  assert.ok(pageEntry, "client manifest must expose app/page.tsx");
  const pageStaticFiles = collectStaticClientFiles(manifest, "app/page.tsx");
  const pageEntryUrl = new URL(`../dist/client/${pageEntry}`, import.meta.url);
  const [pageEntrySource, pageEntryStat] = await Promise.all([
    readFile(pageEntryUrl, "utf8"),
    stat(pageEntryUrl),
  ]);
  assert.ok(
    pageEntryStat.size <= MARKET_ENTRY_MAX_BYTES,
    `market page entry is ${pageEntryStat.size} bytes; budget is ${MARKET_ENTRY_MAX_BYTES}`,
  );
  assert.doesNotMatch(pageEntrySource, new RegExp(ADMIN_RUNTIME_MARKER));

  const assetsUrl = new URL("../dist/client/assets/", import.meta.url);
  const javascriptAssets = (await readdir(assetsUrl)).filter((name) => name.endsWith(".js"));
  const markedAssets: Array<{ name: string; bytes: number }> = [];
  for (const name of javascriptAssets) {
    const assetUrl = new URL(name, assetsUrl);
    const source = await readFile(assetUrl, "utf8");
    if (!source.includes(ADMIN_RUNTIME_MARKER)) continue;
    markedAssets.push({ name, bytes: (await stat(assetUrl)).size });
  }

  assert.equal(markedAssets.length, 1, "one lazy client chunk must own the market admin runtime");
  assert.notEqual(`assets/${markedAssets[0]?.name}`, pageEntry);
  assert.equal(
    pageStaticFiles.has(`assets/${markedAssets[0]?.name}`),
    false,
    "the market administration runtime must not be reachable through the page entry's static import graph",
  );
  assert.ok(
    (markedAssets[0]?.bytes ?? Number.POSITIVE_INFINITY) <= MARKET_MASTER_ADMIN_CHUNK_MAX_BYTES,
    `market admin chunk is ${markedAssets[0]?.bytes} bytes; budget is ${MARKET_MASTER_ADMIN_CHUNK_MAX_BYTES}`,
  );
});
