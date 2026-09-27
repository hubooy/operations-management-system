// Local browser UI with actual synthetic multi-volume files, simulated API only.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), output = path.resolve(".runtime/batch20-volume-ui");
const source = path.resolve(".runtime/batch19-synthetic-volumes");
await fs.mkdir(output, { recursive: true });
const bytes = new Map(), hash = raw => createHash("sha256").update(raw).digest("hex");
for (let volume = 1; volume <= 2; volume++) for (const format of ["html", "xlsx"]) {
  bytes.set(`${volume}/${format}`, await fs.readFile(path.join(source, `volume-${volume}.${format}`)));
}
bytes.set("0/json", await fs.readFile(path.join(source, "manifest.json")));
const descriptor = (volumeIndex, format) => {
  const raw = bytes.get(`${volumeIndex}/${format}`);
  return { volumeIndex, format, bytes: raw.length, sha256: hash(raw), chunkCount: Math.ceil(raw.length/524288) };
};
const bindingDigest = "a".repeat(64), principalA = "b".repeat(64), principalB = "c".repeat(64);
const manifest = { schemaVersion: "business-file-delivery-v2", rendererVersion: 4, bindingDigest, attempt: 1, draft: true, volumeCount: 2,
  files: [descriptor(1, "html"), descriptor(1, "xlsx"), descriptor(2, "html"), descriptor(2, "xlsx")], manifestFile: descriptor(0, "json") };
const item = { id: "volume_file_1", reportId: "report_1", draft: true, rendererVersion: 4, status: "ready", version: 8, attempt: 1,
  bindingDigest, errorCode: "", progress: { stage: "ready" }, manifest, storedBytes: [...bytes.values()].reduce((sum, b) => sum+b.length, 0), createdAt: "2026-09-17T00:00:00Z" };
await fs.writeFile(path.join(output, "entry.tsx"), `import React,{StrictMode} from 'react';import{createRoot}from'react-dom/client';import View from ${JSON.stringify(path.join(root,"app/ai-business-report-files.tsx"))};import ${JSON.stringify(path.join(root,"app/ai-report-workbench.css"))};createRoot(document.getElementById('root')!).render(<StrictMode><main style={{padding:16}}><h1>合成多卷交付验收</h1><View reportId="report_1" allowFormal={true} volumeMode={true}/></main></StrictMode>);`);
await build({ entryPoints: [path.join(output, "entry.tsx")], bundle: true, outfile: path.join(output, "app.js"), platform: "browser", jsx: "automatic", alias: { "@": root }, logLevel: "silent" });
const server = http.createServer(async (request, response) => {
  const name = request.url === "/app.js" ? "app.js" : request.url === "/app.css" ? "app.css" : null;
  response.setHeader("content-type", name === "app.js" ? "text/javascript" : name === "app.css" ? "text/css" : "text/html;charset=utf-8");
  response.end(name ? await fs.readFile(path.join(output, name)) : '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><style>body{margin:0;font-family:Arial,sans-serif}button{padding:8px}</style><div id="root"></div><script src="/app.js"></script>');
});
let browser, principal = principalA, present = false, corrupt = false, switchAtChunk = false;
const errors = [], checks = [], requests = [], downloads = [];
const evidence = path.join(output, "evidence.json");
await fs.writeFile(evidence, JSON.stringify({ status: "running", syntheticOnly: true }));
try {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  page.setDefaultTimeout(15000);
  page.on("pageerror", error => errors.push(String(error)));
  page.on("download", download => downloads.push(download));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, origin, "external requests forbidden");
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ path: url.pathname, method: route.request().method() });
    if (url.pathname === "/api/ai/business-evidence") return route.fulfill({ json: { principalKey: principal, items: [] } });
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      assert.equal(body.deliveryMode, "volumes");
      assert.equal(body.expectedPrincipalKey, principalA);
      assert.equal(body.draft, true);
      present = true;
      return route.fulfill({ json: { item } });
    }
    if (url.pathname === "/api/ai/reports/report_1/files") return route.fulfill({ json: { items: present && principal === principalA ? [{ ...item, manifest: null }] : [] } });
    if (principal !== principalA) return route.fulfill({ status: 403, json: { error: "合成账号已切换" } });
    if (url.pathname === "/api/ai/business-files/volume_file_1") return route.fulfill({ json: { item } });
    const found = url.pathname.match(/\/volumes\/(\d+)\/chunks\/(html|xlsx|json)$/);
    assert.ok(found, url.pathname);
    const index = Number(found[1]), format = found[2], sequence = Number(url.searchParams.get("sequence")), raw = bytes.get(`${index}/${format}`);
    const chunk = raw.subarray((sequence-1)*524288, sequence*524288);
    if (switchAtChunk) { switchAtChunk = false; principal = principalB; }
    return route.fulfill({ json: { schemaVersion: "business-volume-chunk-v1", runId: item.id, volumeIndex: index, format, attempt: 1,
      sequence, bytes: chunk.length, sha256: corrupt ? "0".repeat(64) : hash(chunk), fileSha256: hash(raw), bindingDigest, base64: chunk.toString("base64") } });
  });
  await page.goto(origin);
  await page.getByRole("button", { name: "生成多卷草稿", exact: true }).click();
  await page.getByRole("button", { name: "查看分卷文件", exact: true }).click();
  await page.getByRole("button", { name: "下载完整交付清单 JSON", exact: true }).waitFor();
  checks.push("explicit_v4_creation_and_complete_two_volume_directory");
  assert.equal(downloads.length, 0, "listing must not auto-download");
  for (const [index, format, label] of [[1, "html", /^第 1 卷 · HTML/], [2, "xlsx", /^第 2 卷 · Excel/], [0, "json", "下载完整交付清单 JSON"]]) {
    const pending = page.waitForEvent("download");
    await page.getByRole("button", { name: label }).click();
    const download = await pending, target = path.join(output, `download-${index}.${format}`);
    await download.saveAs(target);
    assert.deepEqual(await fs.readFile(target), bytes.get(`${index}/${format}`));
  }
  checks.push("selected_html_xlsx_and_json_downloads_match_exact_bytes");
  await page.screenshot({ path: path.join(output, "desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, "mobile.png"), fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  checks.push("mobile_no_page_overflow");
  const before = downloads.length;
  corrupt = true;
  await page.getByRole("button", { name: /^第 2 卷 · HTML/ }).click();
  await page.getByRole("alert").first().waitFor();
  assert.equal(downloads.length, before);
  corrupt = false;
  checks.push("corrupt_chunk_produces_no_download");
  switchAtChunk = true;
  await page.getByRole("button", { name: /^第 2 卷 · HTML/ }).click();
  await page.getByRole("alert").filter({ hasText: /账号|权限/ }).first().waitFor();
  assert.equal(downloads.length, before);
  checks.push("account_switch_during_download_produces_no_file");
  assert.deepEqual(errors, []);
  const result = { status: "passed", syntheticOnly: true, checks, pageErrors: errors, downloads: downloads.length,
    postCount: requests.filter(r => r.method === "POST").length, productionWrites: false };
  await fs.writeFile(evidence, JSON.stringify(result, null, 2));
  process.stdout.write(JSON.stringify(result)+"\n");
} catch (error) {
  await fs.writeFile(evidence, JSON.stringify({ status: "failed", syntheticOnly: true, checks, errors: [...errors, String(error)] }, null, 2));
  throw error;
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
