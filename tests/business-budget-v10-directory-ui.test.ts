import assert from "node:assert/strict";
import test from "node:test";
import { createElement, isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { BudgetV10Directory, FileDownloadProgress, budgetV10PageAvailable, budgetV10UiEnabled } from "../app/ai-business-report-files";
import type { BusinessFileRun, BusinessVolumeManifest } from "../lib/ai/business-file-download";

const sha = (letter: string) => letter.repeat(64);
const manifest: BusinessVolumeManifest = {
  schemaVersion: "business-file-delivery-v2", rendererVersion: 10,
  bindingDigest: sha("a"), attempt: 2, draft: false, volumeCount: 2,
  files: [
    { volumeIndex: 1, format: "html", bytes: 1024, sha256: sha("b"), chunkCount: 1 },
    { volumeIndex: 1, format: "xlsx", bytes: 2048, sha256: sha("c"), chunkCount: 1 },
    { volumeIndex: 2, format: "html", bytes: 3072, sha256: sha("d"), chunkCount: 1 },
    { volumeIndex: 2, format: "xlsx", bytes: 4096, sha256: sha("e"), chunkCount: 1 },
  ],
  manifestFile: { volumeIndex: 0, format: "json", bytes: 512,
    sha256: sha("f"), chunkCount: 1 },
};
const item: BusinessFileRun = {
  id: "run_1", reportId: "report_1", rendererVersion: 10, status: "ready",
  draft: false, version: 8, attempt: 2, bindingDigest: sha("a"),
  storedBytes: 10752, errorCode: "", progress: { stage: "ready" }, manifest,
};

test("budget v10 page gate is exact and defaults closed", () => {
  const old = process.env.NEXT_PUBLIC_AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED;
  try {
    delete process.env.NEXT_PUBLIC_AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED;
    assert.equal(budgetV10UiEnabled(), false);
    process.env.NEXT_PUBLIC_AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED = "TRUE";
    assert.equal(budgetV10UiEnabled(), false);
    process.env.NEXT_PUBLIC_AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED = "true";
    assert.equal(budgetV10UiEnabled(), true);
  } finally {
    if (old === undefined) delete process.env.NEXT_PUBLIC_AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED;
    else process.env.NEXT_PUBLIC_AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED = old;
  }
  assert.equal(budgetV10PageAvailable(item, false), false);
  assert.equal(budgetV10PageAvailable(item, true), true);
  assert.equal(budgetV10PageAvailable({ ...item, status: "paused" }, true), false);
  assert.equal(budgetV10PageAvailable({ ...item, draft: true }, true), false);
  assert.equal(budgetV10PageAvailable({ ...item, rendererVersion: 9 }, true), false);
});

test("enabled ready v10 directory shows exact manifest and every volume SHA", () => {
  const selected: Array<[string, number]> = [];
  const view = BudgetV10Directory({ item, manifest, enabled: true, disabled: false,
    onSave: (format, index) => selected.push([format, index]),
  });
  const html = renderToStaticMarkup(view);
  assert.match(html, /aria-label="预算候选分卷文件列表"/);
  assert.match(html, /内部验收候选/);
  assert.match(html, /Office 原生打开、公式复算和真实规模仍待核验/);
  assert.match(html, /下载完整交付清单 JSON/);
  assert.equal((html.match(/<button/g) ?? []).length, 5);
  for (const file of [manifest.manifestFile, ...manifest.files]) {
    assert.match(html, new RegExp(file.sha256));
  }
  for (const index of [1, 2]) {
    assert.match(html, new RegExp(`第 ${index} 卷 · HTML`));
    assert.match(html, new RegExp(`第 ${index} 卷 · Excel`));
  }
  function clickButtons(value: unknown) {
    if (Array.isArray(value)) { value.forEach(clickButtons); return; }
    if (!isValidElement<{ children?: unknown; onClick?: () => void }>(value)) return;
    if (value.type === "button") value.props.onClick?.();
    clickButtons(value.props.children);
  }
  clickButtons(view);
  assert.deepEqual(selected, [["json", 0], ["html", 1], ["xlsx", 1],
    ["html", 2], ["xlsx", 2]]);
});

test("closed, stale, or disabled v10 directory cannot expose buttons", () => {
  const render = (candidate: BusinessFileRun, selected: BusinessVolumeManifest, enabled: boolean) =>
    renderToStaticMarkup(createElement(BudgetV10Directory, {
      item: candidate, manifest: selected, enabled, disabled: false, onSave: () => {},
    }));
  assert.equal(render(item, manifest, false), "");
  assert.equal(render({ ...item, status: "paused" }, manifest, true), "");
  assert.equal(render(item, { ...manifest, bindingDigest: sha("0") }, true), "");
  assert.equal(render(item, { ...manifest, rendererVersion: 9 }, true), "");
  const disabled = renderToStaticMarkup(createElement(BudgetV10Directory, {
    item, manifest, enabled: true, disabled: true, onSave: () => {},
  }));
  assert.equal((disabled.match(/<button disabled=""/g) ?? []).length, 5);
});

test("download progress keeps the existing percentage and cancellation control", () => {
  assert.equal(renderToStaticMarkup(createElement(FileDownloadProgress, {
    percent: 37, onCancel: () => {},
  })), '<p role="status">下载校验 37% <button>取消下载</button></p>');
});
