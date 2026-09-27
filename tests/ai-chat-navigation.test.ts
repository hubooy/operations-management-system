import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import SidebarNavigation from "../app/shell/sidebar-navigation";
import type { ModuleViewKey } from "../app/shell/navigation-catalog";

test("standalone chat precedes operations and only one AI destination is current", () => {
  for (const view of ["assistant", "agents", "configuration", "scheduled"] as ModuleViewKey[]) {
    const html = renderToStaticMarkup(createElement(SidebarNavigation, {
      active: "ai", activeView: view, collapsed: false,
      hrefForModule: (module, requestedView) => `/?module=${module}${requestedView ? `&view=${requestedView}` : ""}`,
      onNavigate: () => {}, onToggleCollapsed: () => {},
    }));
    const links = [...html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)];
    assert.equal(links.length, 13);
    assert.match(links[0][1], /module=ai&amp;view=assistant/);
    assert.match(links[1][1], /module=workflow/);
    const active = links.filter(match => match[1].includes('aria-current="page"'));
    assert.equal(active.length, 1);
    assert.match(active[0][1], view === "assistant" ? /view=assistant/ : /view=agents/);
    assert.match(active[0][2], view === "assistant" ? /AI 对话/ : /AI 助理/);
  }
});
