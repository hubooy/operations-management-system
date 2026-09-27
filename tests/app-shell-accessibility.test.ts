import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { focusTrapTargetIndex } from "../app/shell/app-shell";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("mobile drawer focus wrapping is deterministic without a browser DOM", () => {
  assert.equal(focusTrapTargetIndex(-1, 3, false), 0);
  assert.equal(focusTrapTargetIndex(-1, 3, true), 2);
  assert.equal(focusTrapTargetIndex(0, 3, true), 2);
  assert.equal(focusTrapTargetIndex(2, 3, false), 0);
  assert.equal(focusTrapTargetIndex(1, 3, false), null);
  assert.equal(focusTrapTargetIndex(0, 0, false), null);
});

test("application landmarks and the mobile navigation state stay accessible", async () => {
  const shell = await source("../app/shell/app-shell.tsx");
  assert.match(shell, /<div ref=\{shellRef\} className=\{`app-shell/);
  assert.match(shell, /<main id="main-content" className="workspace"/);
  assert.match(shell, /aria-hidden=\{mobileDrawerHidden \|\| undefined\}/);
  assert.match(shell, /inert=\{mobileDrawerHidden \|\| undefined\}/);
  assert.match(shell, /role=\{mobileDrawerActive \? "dialog" : undefined\}/);
  assert.match(shell, /aria-modal=\{mobileDrawerActive \|\| undefined\}/);
  assert.match(shell, /foregroundDialog/);
  assert.match(shell, /cancelInitialFocus\(\)/);
  assert.match(shell, /prefers-reduced-motion: reduce/);
});

test("collapsed navigation exposes stable group names without dangling tooltip references", async () => {
  const sidebar = await source("../app/shell/sidebar-navigation.tsx");
  assert.match(sidebar, /aria-current=\{selected \? "page" : undefined\}/);
  assert.match(sidebar, /aria-label=\{collapsed \? group\.label : undefined\}/);
  assert.match(sidebar, /aria-labelledby=\{collapsed \? undefined : groupLabelId\}/);
  assert.match(sidebar, /const tooltipVisible = collapsed && tooltip\?\.id === tooltipId/);
  assert.match(sidebar, /aria-describedby=\{tooltipVisible \? tooltipId : undefined\}/);
  assert.match(sidebar, /event\.relatedTarget instanceof Node/);
  assert.match(sidebar, /event\.key === "Escape"/);
  assert.match(sidebar, /onScroll=\{\(\) => setTooltip\(null\)\}/);
  assert.match(sidebar, /aria-controls="primary-navigation"/);
});

test("global header and module fallback have named, focusable semantics", async () => {
  const [header, boundary] = await Promise.all([
    source("../app/shell/global-header.tsx"),
    source("../app/shell/module-error-boundary.tsx"),
  ]);
  assert.match(header, /aria-labelledby="global-page-title"/);
  assert.match(header, /aria-describedby="global-page-description"/);
  assert.match(header, /aria-haspopup="dialog"/);
  assert.match(header, /role="group" aria-label="页面工具"/);
  assert.match(boundary, /this\.scheduleFocus\(\(\) => this\.fallbackRef\.current\)/);
  assert.match(boundary, /aria-labelledby="module-error-title"/);
  assert.match(boundary, /aria-describedby="module-error-description"/);
  assert.match(boundary, /tabIndex=\{-1\}/);
  assert.match(boundary, /document\.getElementById\("global-page-title"\)/);
  assert.match(boundary, /componentWillUnmount/);
});

test("reduced motion remains a global application contract", async () => {
  const styles = await source("../app/globals.css");
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(styles, /scroll-behavior: auto!important/);
  assert.match(styles, /transition-duration: \.01ms!important/);
  assert.match(styles, /animation-duration: \.01ms!important/);
});
