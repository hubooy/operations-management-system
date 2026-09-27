import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createServer } from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = resolve(import.meta.dirname, ".."), output = resolve(root, ".runtime/business-file-ui");
await mkdir(output, { recursive: true });
await writeFile(resolve(output, "index.html"), '<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/bundle.css"><style>body{margin:0;font-family:Arial,sans-serif;color:#12382c}button{padding:8px;cursor:pointer}</style><div id="root"></div><script src="/bundle.js"></script></html>');
await writeFile(resolve(output, "main.tsx"), `import React,{StrictMode} from 'react'; import {createRoot} from 'react-dom/client'; import View from '../../app/ai-business-report-files'; import '../../app/ai-report-workbench.css'; createRoot(document.getElementById('root')).render(<StrictMode><main style={{padding:24}}><p>合成文件任务演练</p><View key="report_1" reportId="report_1" allowFormal={true}/></main></StrictMode>);`);
await build({ entryPoints: [resolve(output, "main.tsx")], outfile: resolve(output, "bundle.js"), bundle: true, jsx: "automatic", platform: "browser", alias: { "@": root } });
const server = createServer(async (request, response) => {
  const name = request.url === "/bundle.js" ? "bundle.js" : request.url === "/bundle.css" ? "bundle.css" : "index.html";
  response.setHeader("content-type", name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html");
  response.end(await readFile(resolve(output, name)));
});
const bytes = Buffer.from("<!doctype html><meta charset=utf-8><p>合成报告文件</p>"), digest = createHash("sha256").update(bytes).digest("hex");
const item = { id: "file_1", reportId: "report_1", draft: true, status: "queued", version: 1, attempt: 1, bindingDigest: "a".repeat(64), errorCode: "", progress: {}, manifest: null };
let browser, present = false, rejectWrite = false, holdNext = false, held, holdStarted;
const errors = [], checks = [];
try {
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(3119, "127.0.0.1", resolve); });
  browser = await chromium.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(30000);
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, "http://127.0.0.1:3119", "no external requests");
    if (!url.pathname.startsWith("/api/")) return route.continue();
    if (url.pathname === "/api/ai/business-evidence") return route.fulfill({ json: { principalKey: "c".repeat(64), items: [] } });
    if (route.request().method() === "POST") {
      if (rejectWrite) return route.fulfill({ status: 409, json: { error: "合成版本冲突" } });
      const body = route.request().postDataJSON();
      if (url.pathname.endsWith("/control")) {
        assert.equal(body.expectedVersion, item.version);
        item.status = body.action === "pause" ? "paused" : body.action === "cancel" ? "cancelled" : "queued";
        item.version++;
      } else present = true;
      return route.fulfill({ json: { item } });
    }
    if (url.pathname.endsWith("/chunks/html")) return route.fulfill({ json: { schemaVersion: "business-file-chunk-v1", runId: item.id, attempt: 1, sequence: 1, format: "html", bytes: bytes.length, sha256: digest, fileSha256: digest, base64: bytes.toString("base64") } });
    if (url.pathname === "/api/ai/business-files/file_1") return route.fulfill({ json: { item } });
    if (holdNext) { holdNext = false; held = route; holdStarted?.(); return; }
    return route.fulfill({ json: { items: present ? [item] : [] } });
  });
  await page.goto("http://127.0.0.1:3119/.runtime/business-file-ui/index.html");
  await page.getByRole("button", { name: "生成双文件草稿", exact: true }).waitFor();
  console.log("mounted");
  await page.waitForTimeout(500);
  // Hold an old empty read, then ensure the successful mutation forces a newer read.
  holdNext = true;
  const holding = new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error("held read missing")), 5000); holdStarted = () => { clearTimeout(timer); resolve(); }; });
  await page.getByRole("button", { name: "刷新文件状态" }).click();
  await holding;
  await page.getByRole("button", { name: "生成双文件草稿", exact: true }).click();
  await page.getByRole("button", { name: "暂停生成", exact: true }).waitFor();
  if (held) await held.fulfill({ json: { items: [] } }).catch(() => {});
  assert.equal(await page.getByRole("button", { name: "暂停生成", exact: true }).count(), 1);
  checks.push("strict_mode_and_mutation_refresh_reject_stale_read");
  console.log(checks.at(-1));
  await page.getByRole("button", { name: "暂停生成", exact: true }).click();
  await page.getByRole("button", { name: "恢复生成", exact: true }).waitFor();
  await page.getByRole("button", { name: "恢复生成", exact: true }).click();
  await page.getByRole("button", { name: "暂停生成", exact: true }).waitFor();
  checks.push("pause_resume_use_current_version");
  rejectWrite = true;
  await page.getByRole("button", { name: "暂停生成", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "合成版本冲突" }).waitFor();
  await page.getByRole("button", { name: "刷新文件状态" }).click();
  assert.equal(await page.getByRole("alert").filter({ hasText: "合成版本冲突" }).count(), 1);
  checks.push("read_success_preserves_write_error");
  item.status = "ready";
  item.manifest = { schemaVersion: "business-file-delivery-v1", attempt: 1, bindingDigest: item.bindingDigest, files: { html: { bytes: bytes.length, chunkCount: 1, chunkBytes: 524288, sha256: digest, fileName: "合成经营报告.html", mimeType: "text/html; charset=utf-8" } } };
  await page.getByRole("button", { name: "刷新文件状态" }).click();
  await page.getByRole("button", { name: "下载 HTML", exact: true }).waitFor();
  const pending = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 HTML", exact: true }).click();
  const download = await pending, path = resolve(output, "download.html");
  await download.saveAs(path);
  assert.deepEqual(await readFile(path), bytes);
  assert.equal(download.suggestedFilename(), "合成经营报告.html");
  checks.push("browser_download_matches_complete_bytes");
  await page.screenshot({ path: resolve(output, "files.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: resolve(output, "mobile.png"), fullPage: true });
  checks.push("mobile_no_horizontal_overflow");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: "passed", checks, productionWrites: false }));
} finally { if (held) await held.abort().catch(() => {}); await browser?.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
