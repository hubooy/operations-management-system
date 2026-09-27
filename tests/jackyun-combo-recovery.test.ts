import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { chromium } from "playwright-core";
import { parseErpReferenceXlsx } from "../lib/imports/erp-reference";
import { createXlsxWorkbookBytes } from "../lib/imports/xlsx-write";
import { PlaywrightPageClient } from "../lib/jackyun/playwright-client";
import { confirmJackyunComboExport } from "../tools/jackyun-browser-controller";
import { auditedComboNameRejection, isAuditedComboNameRepair } from "../lib/jackyun/combo-name-recovery";

test("only the audited pre-transaction combo rejection can resume the same file after normalization", async () => {
  const failedAudit = JSON.parse(await readFile(new URL("./fixtures/jackyun-845-combo-rejection.json", import.meta.url), "utf8"));
  const bound = auditedComboNameRejection;
  const input = { runId: bound.runId, module: "combos", sourceSha256: bound.sourceSha256, inputContractHash: bound.inputContractHash,
    priorModule: { module: "combos", status: "failed", sourceSha256: bound.sourceSha256, inputContractHash: bound.inputContractHash, error: "parentName 无效" },
    failedAudit, relationCountVerified: true };
  assert.equal(isAuditedComboNameRepair(input), true);
  for (const changed of [{ runId: "other" }, { module: "sales" }, { sourceSha256: "a".repeat(64) }, { inputContractHash: "b".repeat(64) },
    { relationCountVerified: false }, { failedAudit: { ...failedAudit, error: { message: "unknown result" } } },
    { priorModule: { ...input.priorModule, status: "completed" } }, { priorModule: { ...input.priorModule, batchId: "already-written" } }]) {
    assert.equal(isAuditedComboNameRepair({ ...input, ...changed }), false);
  }
});

test("combo display whitespace is normalized in both templates with an audit warning, preserving identities and quantities", () => {
  for (const sheets of [
    [{ name: "组合", rows: [["组合装编号", "组合装名称", "子件编号", "子件名称", "子件数量"], ["P-1", "甲\t 乙", "C-1", "丙\r\n丁", 2]] }],
    [{ name: "母件", rows: [["货品编号", "货品名称"], ["P-1", "甲\t 乙"]] },
      { name: "子件", rows: [["母件编号", "编号", "名称", "数量"], ["P-1", "C-1", "丙\r\n丁", 2]] }],
  ]) {
    const parsed = parseErpReferenceXlsx("combos", createXlsxWorkbookBytes(sheets));
    assert.equal(parsed.errors.length, 0); assert.equal(parsed.rows.length, 1);
    assert.deepEqual(parsed.rows.map(row => ({ ...row, sourceRowNumber: 0 })), [{ sourceRowNumber: 0, parentCode: "P-1", parentName: "甲 乙", childCode: "C-1", childName: "丙 丁", childQuantityMilli: 2000 }]);
    assert.equal(parsed.warnings.filter(w => w.code === "NORMALIZED_DISPLAY_WHITESPACE").length, 1);
  }
});

const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
test("combo confirmation clicks only the exact module dialog once and requires its disappearance", {
  skip: process.platform !== "win32" || !existsSync(chromePath), timeout: 30000,
}, async () => {
  const b = await chromium.launch({ executablePath: chromePath, headless: true });
  try {
    const page = await b.newPage({ viewport: { width: 1200, height: 900 } });
    await page.addInitScript("window.__name = value => value");
    await page.route("**/*", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: new URL(route.request().url()).pathname === "/"
      ? '<button id="decoy" onclick="window.decoyClicked=true">确定</button><iframe src="/combos_fixture" style="border:0;width:1000px;height:700px"></iframe>'
      : '<style>.mini-messagebox{position:absolute;left:300px;top:250px;background:#eee;padding:25px}a{display:inline-block;padding:10px}</style><div class="mini-messagebox">导出列中存在图片列，最多只能导出2000条，确定导出？<a class="mini-button" onclick="window.clicked=(window.clicked||0)+1;this.parentElement.remove()">确定</a><a class="mini-button">取消</a></div>' }));
    const reset = () => page.goto("https://combo-fixture.invalid/");
    await reset();
    const client = new PlaywrightPageClient(page, await page.context().newCDPSession(page));
    const parts = ["导出列中存在图片列", "最多只能导出2000条", "确定导出"];
    const invoke = () => confirmJackyunComboExport(client, ["combos_fixture"], parts, "确定", 1000, 30);
    await invoke();
    assert.equal(await page.evaluate(() => (window as unknown as { decoyClicked?: boolean }).decoyClicked), undefined);
    assert.equal(await page.frames()[1].evaluate(() => (window as unknown as { clicked: number }).clicked), 1);
    await reset();
    await page.frames()[1].evaluate(() => {
      const button = document.querySelector<HTMLAnchorElement>('.mini-messagebox a')!;
      const handler = button.onclick;
      button.onclick = null;
      setTimeout(() => { button.onclick = handler; }, 150);
    });
    await invoke();
    assert.equal(await page.frames()[1].evaluate(() => (window as unknown as { clicked: number }).clicked), 1);
    for (const fault of ["duplicate", "disabled", "covered", "stays-open", "unbound", "unbinds-after-click"]) {
      await reset();
      await page.frames()[1].evaluate(fault => {
        const dialog = document.querySelector<HTMLElement>(".mini-messagebox")!, button = dialog.querySelector<HTMLElement>("a")!;
        if (fault === "duplicate") document.body.append(dialog.cloneNode(true));
        if (fault === "disabled") button.classList.add("mini-disabled");
        if (fault === "covered") { const cover = document.createElement("div");cover.style.cssText="position:fixed;inset:0;z-index:999;background:white";document.body.append(cover); }
        if (fault === "stays-open") button.onclick = () => { (window as unknown as { clicked: number }).clicked = ((window as unknown as { clicked: number }).clicked || 0) + 1; };
        if (fault === "unbound") button.onclick = null;
        if (fault === "unbinds-after-click") button.onclick = () => { (window as unknown as { clicked: number }).clicked = 1; button.onclick = null; };
      }, fault);
      await assert.rejects(invoke());
      assert.equal(await page.frames()[1].evaluate(() => (window as unknown as { clicked: number }).clicked || 0), ["stays-open", "unbinds-after-click"].includes(fault) ? 1 : 0);
    }
  } finally { await b.close(); }
});
