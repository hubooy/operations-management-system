// Private headless file:// capacity probe for a complete synthetic v10 HTML.
// Never opens customer files, Office, production routes or remote resources.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";
import { chromium } from "playwright-core";

const [source, outputDir] = process.argv.slice(2);
if (!source || !outputDir) throw new Error("synthetic-volume.html NEW_OUTPUT_DIR required");
const HTML_MAX = 256 * 1024 * 1024;
const MIN_RAM_TO_START = 2.5 * 1024 ** 3;
const MIN_RAM_FOR_LARGE = 3.0 * 1024 ** 3;
const MIN_RAM_DURING = 2.0 * 1024 ** 3;
const MAX_HEAP = 512 * 1024 * 1024;
const MAX_SMALL_MS = 20_000;
const MAX_LARGE_MS = 45_000;
const ROWS = 575_095;
const html = path.resolve(source);
const stats = await fs.stat(html);
if (!stats.isFile() || stats.size < 1 || stats.size > HTML_MAX) throw new Error("synthetic HTML exceeds bounded candidate size");
await fs.mkdir(outputDir, { recursive: false });
const evidencePath = path.join(outputDir, "browser-evidence.json");
const evidence = { schemaVersion: "business-v10-slim-large-browser-v1",
  syntheticOnly: true, nativeExcelOpened: false, externalRequests: [],
  htmlBytes: stats.size, browser: null, baselineFreeRamBytes: os.freemem(),
  limits: { minRamToStartBytes: MIN_RAM_TO_START, minRamForLargeBytes: MIN_RAM_FOR_LARGE,
    minRamDuringBytes: MIN_RAM_DURING, maxV8OldSpaceMiB: 512,
    maxJsHeapBytes: MAX_HEAP, maxSmallMs: MAX_SMALL_MS, maxLargeMs: MAX_LARGE_MS },
  smallTable: null, largeTable: null, status: "running" };
await fs.writeFile(evidencePath, JSON.stringify(evidence, null, 2));
let browser;
let deadline;
let watcher;
let interrupted = null;
const errors = [];
try {
  if (evidence.baselineFreeRamBytes < MIN_RAM_TO_START) {
    evidence.status = "skipped_low_memory";
  } else {
    browser = await chromium.launch({
      executablePath: process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      headless: true, args: ["--js-flags=--max-old-space-size=512", "--disable-background-networking"],
    });
    evidence.browser = await browser.version();
    const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
    const cdp = await page.context().newCDPSession(page);
    page.on("pageerror", error => errors.push(String(error)));
    await page.route(/^https?:/, async route => {
      evidence.externalRequests.push(route.request().url());
      await route.abort();
    });
    const initial = performance.now();
    deadline = setTimeout(() => {
      interrupted = "small_table_deadline";
      void browser.close();
    }, MAX_SMALL_MS);
    await page.goto(pathToFileURL(html).href, { timeout: MAX_SMALL_MS });
    await page.waitForFunction(() => document.querySelector("#count")?.textContent?.includes("1 / 1 行"), null,
      { timeout: MAX_SMALL_MS });
    clearTimeout(deadline);
    deadline = undefined;
    const initialHeap = (await cdp.send("Runtime.getHeapUsage")).usedSize;
    evidence.smallTable = { loaded: true, elapsedMs: Math.round(performance.now() - initial),
      jsHeapBytes: initialHeap, freeRamAfterBytes: os.freemem(),
      navTables: await page.locator("#nav button").count() };
    assert.equal(evidence.smallTable.navTables, 9);
    assert.ok(initialHeap <= MAX_HEAP);
    assert.deepEqual(errors, []);
    assert.deepEqual(evidence.externalRequests, []);
    if (os.freemem() < MIN_RAM_FOR_LARGE) {
      evidence.largeTable = { status: "skipped_low_memory", freeRamBytes: os.freemem() };
      evidence.status = "small_only_passed";
    } else {
      const selected = page.locator("#nav button", { hasText: "合成推广原始明细" });
      assert.equal(await selected.count(), 1);
      evidence.largeTable = { status: "running", freeRamBeforeBytes: os.freemem() };
      await fs.writeFile(evidencePath, JSON.stringify(evidence, null, 2));
      const opened = performance.now();
      watcher = setInterval(() => {
        if (!interrupted && os.freemem() < MIN_RAM_DURING) {
          interrupted = "system_free_ram_floor";
          void browser.close();
        }
      }, 500);
      deadline = setTimeout(() => {
        if (!interrupted) interrupted = "large_table_deadline";
        void browser.close();
      }, MAX_LARGE_MS);
      await selected.click();
      await page.waitForFunction(() => document.querySelector("#count")?.textContent?.includes("575,095 / 575,095 行"),
        null, { timeout: MAX_LARGE_MS });
      const openMs = Math.round(performance.now() - opened);
      clearTimeout(deadline);
      clearInterval(watcher);
      deadline = watcher = undefined;
      if (interrupted) throw new Error(interrupted);
      const openedHeap = (await cdp.send("Runtime.getHeapUsage")).usedSize;
      if (openedHeap > MAX_HEAP) throw new Error("large table JS heap exceeded cap");
      const needle = "SKU-" + createHash("sha256").update(`synthetic-v10-a:${ROWS}`).digest("hex").slice(0, 16);
      const searchStart = performance.now();
      await page.locator("#search").fill(needle);
      await page.waitForFunction(() => document.querySelector("#count")?.textContent?.includes("1 / 575,095 行"),
        null, { timeout: 15_000 });
      assert.match(await page.locator("#tbody tr").innerText(), new RegExp(needle));
      assert.deepEqual(errors, []);
      assert.deepEqual(evidence.externalRequests, []);
      evidence.largeTable = { status: "passed", rows: ROWS,
        openMs,
        searchMs: Math.round(performance.now() - searchStart),
        jsHeapBytes: openedHeap, freeRamAfterBytes: os.freemem(),
        lastSyntheticSkuFound: true };
      evidence.status = "passed";
    }
  }
} catch (error) {
  evidence.status = "failed";
  evidence.largeTable = { ...(evidence.largeTable || {}), status: "failed",
    error: interrupted || String(error), freeRamAfterBytes: os.freemem() };
  evidence.pageErrors = errors;
} finally {
  if (deadline) clearTimeout(deadline);
  if (watcher) clearInterval(watcher);
  if (browser) {
    try { await browser.close(); } catch (error) {
      evidence.browserCloseError = String(error);
      evidence.status = "failed";
    }
  }
  evidence.finalFreeRamBytes = os.freemem();
  await fs.writeFile(evidencePath, JSON.stringify(evidence, null, 2));
  process.stdout.write(JSON.stringify(evidence) + "\n");
}
if (evidence.status === "failed") process.exitCode = 1;
