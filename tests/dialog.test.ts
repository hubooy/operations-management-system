import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { closeDialogLayer, isTopDialogLayer, openDialogLayer } from "@/app/ui/dialog";

test("dialog layer transitions keep the background locked until the final dialog closes", () => {
  const first = Symbol("first");
  const second = Symbol("second");
  let layers: symbol[] = [];

  const openedFirst = openDialogLayer(layers, first);
  layers = openedFirst.layers;
  assert.equal(openedFirst.becameFirst, true);
  assert.equal(isTopDialogLayer(layers, first), true);

  const duplicate = openDialogLayer(layers, first);
  layers = duplicate.layers;
  assert.equal(duplicate.becameFirst, false);
  assert.equal(layers.length, 1);

  const openedSecond = openDialogLayer(layers, second);
  layers = openedSecond.layers;
  assert.equal(openedSecond.becameFirst, false);
  assert.equal(isTopDialogLayer(layers, first), false);
  assert.equal(isTopDialogLayer(layers, second), true);

  const closedUnderlay = closeDialogLayer(layers, first);
  layers = closedUnderlay.layers;
  assert.deepEqual(
    {
      removed: closedUnderlay.removed,
      wasTop: closedUnderlay.wasTop,
      becameEmpty: closedUnderlay.becameEmpty,
    },
    { removed: true, wasTop: false, becameEmpty: false },
  );
  assert.equal(isTopDialogLayer(layers, second), true);

  const closedFinal = closeDialogLayer(layers, second);
  assert.deepEqual(
    {
      removed: closedFinal.removed,
      wasTop: closedFinal.wasTop,
      becameEmpty: closedFinal.becameEmpty,
    },
    { removed: true, wasTop: true, becameEmpty: true },
  );
  assert.equal(closedFinal.layers.length, 0);
});

test("dialog is SSR-safe and portals outside the inert application shell", async () => {
  const source = await readFile(new URL("../app/ui/dialog.tsx", import.meta.url), "utf8");

  assert.match(source, /import \{ createPortal \} from "react-dom"/);
  assert.match(source, /setPortalTarget\(document\.body\)/);
  assert.match(source, /if \(!open \|\| !portalTarget\) return null/);
  assert.match(source, /return createPortal\([\s\S]*?portalTarget/);
  assert.match(source, /document\.body\.style\.overflow = "hidden"/);
  assert.match(source, /if \(transition\.becameEmpty\)/);
  assert.match(source, /backgroundShell\?\.setAttribute\("inert", ""\)/);
  assert.match(source, /event\.key === "Escape"/);
  assert.match(source, /event\.key !== "Tab"/);
  assert.match(source, /event\.target === event\.currentTarget/);
  assert.match(source, /focusTarget\?\.isConnected/);
  assert.match(source, /returnFocusRef\?: RefObject<HTMLElement \| null>/);
  assert.match(source, /explicitReturnFocus\?\.isConnected/);
});
