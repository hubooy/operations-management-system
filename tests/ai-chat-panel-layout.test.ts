import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("module AI chat fills the drawer and keeps a compact composer", async () => {
  const [chatStyles, globalStyles] = await Promise.all([
    readFile(new URL("../app/ai-chat-workbench.css", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(globalStyles, /\.ai-module-drawer\s*\{[^}]*overflow:hidden;/);
  assert.match(globalStyles, /\.ai-module-drawer\s*>\s*\.ai-chat-compact\s*\{[^}]*flex:1;[^}]*min-height:0;/);
  assert.match(globalStyles, /\.ai-chat-compact\s*\{[^}]*display:flex;[^}]*min-height:0;[^}]*flex-direction:column;/);
  assert.match(chatStyles, /\.ai-workbench-compact\s*\{[^}]*display:flex;[^}]*flex:1;[^}]*min-height:0;/);
  assert.match(chatStyles, /\.ai-workbench-compact\s+\.ai-workbench-layout\s*\{[^}]*height:auto;[^}]*min-height:0;[^}]*flex:1;/);
  assert.doesNotMatch(chatStyles, /\.ai-workbench-compact\s+\.ai-workbench-layout\s*\{[^}]*height:65vh/);
  assert.match(chatStyles, /\.ai-workbench-compact\s+\.ai-workbench-composer\s+textarea\s*\{[^}]*min-height:36px;[^}]*max-height:96px;/);
});
