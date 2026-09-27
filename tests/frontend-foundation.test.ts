import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("semantic design tokens cover every stylesheet variable reference", async () => {
  const [styles, tokens] = await Promise.all([
    source("../app/globals.css"),
    source("../app/styles/tokens.css"),
  ]);
  assert.match(styles, /@import "\.\/styles\/tokens\.css"/);
  const combined = `${tokens}\n${styles}`;
  const defined = new Set([...combined.matchAll(/--([a-z0-9-]+)\s*:/gi)].map((match) => match[1]));
  const used = new Set([...combined.matchAll(/var\(--([a-z0-9-]+)/gi)].map((match) => match[1]));
  assert.deepEqual([...used].filter((name) => !defined.has(name)).sort(), []);
  assert.match(tokens, /--color-metric-up: #c83e4d/);
  assert.match(tokens, /--color-metric-down: #16825d/);
});

test("application shell keeps mobile period access and navigation focus behavior", async () => {
  const [page, styles, shell, header] = await Promise.all([
    source("../app/page.tsx"),
    source("../app/globals.css"),
    source("../app/shell/app-shell.tsx"),
    source("../app/shell/global-header.tsx"),
  ]);
  assert.doesNotMatch(styles, /@media \(max-width: 420px\)[\s\S]{0,500}\.date-selector \{ display: none; \}/);
  assert.match(shell, /document\.body\.style\.overflow = "hidden"/);
  assert.match(shell, /event\.key === "Escape"/);
  assert.match(shell, /className="mobile-navigation-close"/);
  assert.match(header, /aria-controls="primary-navigation"/);
  assert.match(page, /window\.history\.pushState/);
  assert.match(page, /window\.history\.replaceState/);
  assert.match(page, /window\.addEventListener\("popstate"/);
  assert.doesNotMatch(page, /需要帮助|查看使用指南|sidebar-help/);
  assert.doesNotMatch(styles, /sidebar-help/);
});

test("BI pilot cancels stale requests and uses the shared JSON client", async () => {
  const dashboard = await source("../app/dashboard-module-view.tsx");
  assert.match(dashboard, /requestGenerationRef/);
  assert.match(dashboard, /requestControllerRef\.current\?\.abort\(\)/);
  assert.match(dashboard, /requestJson<BiDashboardResponse>/);
  assert.match(dashboard, /\/api\/bi\/overview/);
  assert.doesNotMatch(dashboard, /\/api\/sales\/summary|\/api\/inventory\/overview/);
  assert.match(dashboard, /generation !== requestGenerationRef\.current/);
});
