import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const chromePath = process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
test("sales overview preserves multiline code paste, sends all codes and restores them after reload", {
  skip: !existsSync(chromePath), timeout: 60_000,
}, async () => {
  const bundle = await build({
    stdin: { contents: `import { createRoot } from 'react-dom/client';
      import SalesView from './app/sales-module-view';
      createRoot(document.getElementById('root')).render(<SalesView range="自定义"
        customStartDate="2026-09-01" customEndDate="2026-09-30" currentUser={null}
        moduleView="overview" onModuleViewChange={() => {}} />);`,
    loader: "tsx", resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
    bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
    define: { "process.env.NODE_ENV": '"test"' },
  });
  const browser = await chromium.launch({ executablePath: chromePath, headless: true });
  try {
    const page = await browser.newPage();
    const errors: string[] = [];
    const requests: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => {
      const url = route.request().url();
      if (new URL(url).pathname === "/api/sales/summary") {
        requests.push(url);
        return route.fulfill({ json: { current: { lineCount: 0, orderCount: 0, grossSalesCents: 0, netSalesCents: 0 }, channels: [] } });
      }
      return route.fulfill({ contentType: "text/html", body: '<div id="root"></div>' });
    });
    await page.goto("https://sales-fixture.invalid/");
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    const field = page.getByRole("textbox", { name: "销售分析货品编码或名称", exact: true });
    await field.waitFor();
    assert.equal(await field.getAttribute("maxlength"), "1000");
    const codes = Array.from({ length: 100 }, (_, index) => `SKU-${String(index).padStart(3, "0")}`);
    const pasted = codes.join(",\n");
    const sent = page.waitForRequest(request => new URL(request.url()).searchParams.get("productQuery")?.split(",").length === 100);
    await field.fill(pasted);
    const filteredRequest = await sent;
    assert.deepEqual(new URL(filteredRequest.url()).searchParams.getAll("productQuery"), [codes.join(",")]);
    assert.ok([...new URL(filteredRequest.url()).searchParams].length < 100);
    assert.equal(await field.inputValue(), pasted);
    const restored = page.waitForResponse(response => new URL(response.url()).searchParams.get("productQuery")?.split(",").length === 100);
    await page.reload();
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await restored;
    assert.equal(await field.inputValue(), pasted);
    const beforeInvalid = requests.length;
    await field.fill(`${pasted}\nSKU-101`);
    await page.getByRole("alert").filter({ hasText: "最多 100 项" }).waitFor();
    // Wait for the debounce by completing the subsequent valid reset request.
    const cleared = page.waitForResponse(response => new URL(response.url()).pathname === "/api/sales/summary" && !new URL(response.url()).searchParams.has("productQuery"));
    await field.fill("");
    await cleared;
    assert.equal(requests.length, beforeInvalid + 1);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
