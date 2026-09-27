// Render only locally generated synthetic volume fixtures, without network I/O.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const directory = path.resolve(process.argv[2] || ".runtime/batch19-synthetic-volumes");
const manifest = JSON.parse(await fs.readFile(path.join(directory, "manifest.json"), "utf8"));
assert.equal(manifest.schemaVersion, "business-volume-files-v1");
assert.equal(manifest.status, "complete");
assert.ok(Number.isInteger(manifest.volumeCount) && manifest.volumeCount >= 1 && manifest.volumeCount <= 100);
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", headless: true });
const failures = [], requests = [], results = [];
const evidencePath = path.join(directory, "browser-evidence.json");
await fs.writeFile(evidencePath, JSON.stringify({ syntheticOnly: true, status: "running" }));
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  page.on("pageerror", error => failures.push(String(error)));
  await page.route(/^https?:/, async route => { requests.push(route.request().url()); await route.abort(); });
  for (let index = 1; index <= manifest.volumeCount; index++) {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(pathToFileURL(path.join(directory, `volume-${index}.html`)).href);
    const expected = manifest.volumes[index-1];
    await page.locator("#nav button").first().waitFor();
    assert.equal(await page.locator("#nav button").count(), expected.tables.length);
    const labels = await page.locator("#nav button").allTextContents();
    const selected = Math.max(0, labels.findIndex(label => label.startsWith("erp-current_店铺_环比")));
    await page.locator("#nav button").nth(selected).click();
    assert.match(await page.locator("h1").innerText(), new RegExp(`第${index}/${manifest.volumeCount}卷`));
    assert.match(await page.locator("#proof").innerText(), /完整表.*行.*列/);
    await page.locator("#search").fill("__no_synthetic_match__");
    assert.equal(await page.locator("#tbody tr").count(), 0);
    await page.locator("#search").fill("");
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export").click();
    const download = await downloadPromise;
    await download.saveAs(path.join(directory, `volume-${index}-filtered.csv`));
    await page.screenshot({ path: path.join(directory, `volume-${index}-desktop.png`), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(directory, `volume-${index}-mobile.png`), fullPage: true });
    const width = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: window.innerWidth }));
    assert.ok(width.page <= width.viewport+1, JSON.stringify(width));
    results.push({ volumeIndex: index, tables: expected.tables.length, search: true, csv: true, desktop: true, mobile: true });
  }
  assert.deepEqual(failures, []);
  assert.deepEqual(requests, []);
  await fs.writeFile(evidencePath, JSON.stringify({ syntheticOnly: true, status: "passed", results, failures, externalRequests: requests }, null, 2));
  process.stdout.write(JSON.stringify({ passed: true, results, failures, externalRequests: requests })+"\n");
} catch (error) {
  await fs.writeFile(evidencePath, JSON.stringify({ syntheticOnly: true, status: "failed", results, failures: [...failures, String(error)], externalRequests: requests }, null, 2));
  throw error;
} finally {
  await browser.close();
}
