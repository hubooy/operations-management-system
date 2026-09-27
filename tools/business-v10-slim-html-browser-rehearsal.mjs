// Headless file:// comparison of synthetic default and opt-in v10 HTML only.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";
import { chromium } from "playwright-core";

const [baselinePath, slimPath, outputDir, hostilePath] = process.argv.slice(2);
if (!baselinePath || !slimPath || !outputDir) throw new Error("baseline.html slim.html NEW_OUTPUT_DIR [hostile.html] required");
await fs.mkdir(outputDir, { recursive: false });
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  headless: true,
});
const needle = "SKU-" + createHash("sha256").update("synthetic-v10-a:99999").digest("hex").slice(0, 16);
const evidence = { schemaVersion: "business-v10-slim-browser-rehearsal-v1", syntheticOnly: true,
  browser: await browser.version(), externalRequests: [], results: [], tamperedRejected: false,
  rowDigestTamperRejected: false,
  unsupportedBrowserRejected: false,
  hostileMarkupSafe: hostilePath ? false : null };

async function pageFor(file) {
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 }, acceptDownloads: true });
  const devtools = await page.context().newCDPSession(page);
  const errors = [];
  page.on("pageerror", error => errors.push(String(error)));
  await page.route(/^https?:/, async route => {
    evidence.externalRequests.push(route.request().url());
    await route.abort();
  });
  const started = performance.now();
  await page.goto(pathToFileURL(path.resolve(file)).href);
  await page.locator("#nav button").first().waitFor();
  await page.waitForFunction(() => document.querySelector("#count")?.textContent?.includes("1 / 1 行"));
  const initialMs = performance.now() - started;
  const heap = async () => (await devtools.send("Runtime.getHeapUsage")).usedSize;
  return { page, errors, initialMs, initialHeapBytes: await heap(), heap };
}

try {
  for (const [name, file] of [["default-v10", baselinePath], ["slim-v10", slimPath]]) {
    const current = await pageFor(file);
    try {
      const { page } = current;
      assert.equal(await page.locator("#nav button").count(), 9);
      const selected = page.locator("#nav button", { hasText: "合成推广原始明细" });
      assert.equal(await selected.count(), 1);
      const openStart = performance.now();
      await selected.click();
      await page.waitForFunction(() => document.querySelector("#count")?.textContent?.includes("100,000 / 100,000 行"), null, { timeout: 120000 });
      const openMs = performance.now() - openStart;
      const openedHeapBytes = await current.heap();
      assert.equal(await page.locator("#tbody tr").count(), 100);
      await page.locator("#sort").selectOption("5");
      assert.equal(await page.locator("#tbody tr").count(), 100);
      await page.locator("details").first().locator("summary").click();
      await page.locator("#chart-column").selectOption("5");
      assert.equal(await page.locator("#chart svg").count(), 1);
      await page.locator("#sort").selectOption("");
      await page.locator("#next").click();
      assert.match(await page.locator("#count").innerText(), /第 2 \/ 1000 页/);
      const searchStart = performance.now();
      await page.locator("#search").fill(needle);
      await page.waitForFunction(() => document.querySelector("#count")?.textContent?.includes("1 / 100,000 行"));
      const searchMs = performance.now() - searchStart;
      assert.equal(await page.locator("#tbody tr").count(), 1);
      assert.match(await page.locator("#tbody tr").innerText(), new RegExp(needle));
      const downloadPromise = page.waitForEvent("download");
      await page.locator("#export").click();
      const download = await downloadPromise;
      const csvPath = path.join(outputDir, `${name}-matched.csv`);
      await download.saveAs(csvPath);
      assert.match(await fs.readFile(csvPath, "utf8"), new RegExp(needle));
      await page.locator("#nav button").first().click();
      await page.waitForFunction(() => document.querySelector("#count")?.textContent?.includes("1 / 1 行"));
      await selected.click();
      await selected.click();
      await page.waitForFunction(() => document.querySelector("#count")?.textContent?.includes("100,000 / 100,000 行"), null, { timeout: 120000 });
      assert.equal(await page.locator("#tbody tr").count(), 100);
      assert.deepEqual(current.errors, []);
      evidence.results.push({ name, htmlBytes: (await fs.stat(file)).size,
        initialMs: Math.round(current.initialMs), openLargeTableMs: Math.round(openMs),
        searchMs: Math.round(searchMs), initialHeapBytes: current.initialHeapBytes,
        openedHeapBytes, rows: 100000, matchedRows: 1,
        csvDownloaded: true, sortAndChartVerified: true,
        rapidSwitchesVerified: true });
    } finally { await current.page.close(); }
  }

  const tampered = await fs.readFile(slimPath, "utf8");
  const matches = [...tampered.matchAll(/"rowsGzipSha256":"[a-f0-9]{64}"/g)];
  assert.ok(matches.length >= 4);
  const target = matches[3], changed = tampered.slice(0, target.index) +
    '"rowsGzipSha256":"' + "0".repeat(64) + '"' + tampered.slice(target.index + target[0].length);
  const tamperedPath = path.join(outputDir, "tampered-large-table.html");
  await fs.writeFile(tamperedPath, changed);
  const negative = await pageFor(tamperedPath);
  try {
    await negative.page.locator("#nav button", { hasText: "合成推广原始明细" }).click();
    await negative.page.waitForFunction(() => document.querySelector("#note")?.textContent?.includes("离线表无法完整载入"));
    assert.match(await negative.page.locator("#note").innerText(), /压缩行摘要不符/);
    assert.equal(await negative.page.locator("#tbody tr").count(), 0);
    assert.deepEqual(negative.errors, []);
    evidence.tamperedRejected = true;
  } finally { await negative.page.close(); }

  const full = JSON.parse(await fs.readFile(path.join(path.dirname(slimPath), "complete-manifest.json"), "utf8"));
  const largeDigest = full.volumes[0].tables.find(item => item.key === "synthetic-promotion-raw")?.rowDigest;
  assert.match(largeDigest, /^[a-f0-9]{64}$/);
  const rowProof = `"rowDigest":"${largeDigest}"`;
  assert.equal(tampered.split(rowProof).length, 2);
  const rowProofPath = path.join(outputDir, "tampered-row-proof.html");
  await fs.writeFile(rowProofPath, tampered.replace(rowProof, `"rowDigest":"${"0".repeat(64)}"`));
  const badProof = await pageFor(rowProofPath);
  try {
    await badProof.page.locator("#nav button", { hasText: "合成推广原始明细" }).click();
    await badProof.page.waitForFunction(() => document.querySelector("#note")?.textContent?.includes("离线表无法完整载入"));
    assert.match(await badProof.page.locator("#note").innerText(), /完整行摘要不符/);
    assert.equal(await badProof.page.locator("#tbody tr").count(), 0);
    assert.deepEqual(badProof.errors, []);
    evidence.rowDigestTamperRejected = true;
  } finally { await badProof.page.close(); }

  const unsupported = await browser.newPage();
  try {
    const errors = [];
    unsupported.on("pageerror", error => errors.push(String(error)));
    await unsupported.route(/^https?:/, async route => {
      evidence.externalRequests.push(route.request().url());
      await route.abort();
    });
    await unsupported.addInitScript(() => {
      Object.defineProperty(window, "DecompressionStream", { value: undefined });
    });
    await unsupported.goto(pathToFileURL(path.resolve(slimPath)).href);
    await unsupported.waitForFunction(() => document.querySelector("#note")?.textContent?.includes("离线表无法完整载入"));
    assert.match(await unsupported.locator("#note").innerText(), /不支持离线GZIP与SHA-256/);
    assert.equal(await unsupported.locator("#tbody tr").count(), 0);
    assert.deepEqual(errors, []);
    evidence.unsupportedBrowserRejected = true;
  } finally { await unsupported.close(); }

  if (hostilePath) {
    const hostile = await pageFor(hostilePath);
    try {
      const safe = await hostile.page.evaluate(() => ({ unsafe: Boolean(window.UNSAFE),
        cells: [...document.querySelectorAll("#tbody td")].map(cell => cell.textContent) }));
      assert.equal(safe.unsafe, false);
      assert.ok(safe.cells.some(cell => cell.includes("</script><script>window.UNSAFE=true</script>")));
      assert.deepEqual(hostile.errors, []);
      evidence.hostileMarkupSafe = true;
    } finally { await hostile.page.close(); }
  }
  assert.deepEqual(evidence.externalRequests, []);
  await fs.writeFile(path.join(outputDir, "browser-evidence.json"), JSON.stringify({ ...evidence, passed: true }, null, 2));
  process.stdout.write(JSON.stringify({ ...evidence, passed: true }) + "\n");
} catch (error) {
  await fs.writeFile(path.join(outputDir, "browser-evidence.json"), JSON.stringify({ ...evidence, passed: false, error: String(error) }, null, 2));
  throw error;
} finally {
  await browser.close();
}
