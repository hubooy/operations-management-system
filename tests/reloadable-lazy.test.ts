import assert from "node:assert/strict";
import test from "node:test";
import type { ComponentType, LazyExoticComponent } from "react";

import {
  createReloadableLazy,
  resetReloadableLazyScope,
} from "../app/shell/reloadable-lazy";
import ModuleErrorBoundary from "../app/shell/module-error-boundary";

type LazyInternals<Props extends object> = LazyExoticComponent<ComponentType<Props>> & {
  _init: (payload: unknown) => ComponentType<Props>;
  _payload: unknown;
};

function initializeLazy<Props extends object>(component: LazyExoticComponent<ComponentType<Props>>) {
  const internals = component as LazyInternals<Props>;
  return internals._init(internals._payload);
}

async function waitForLazyAttempt<Props extends object>(component: LazyExoticComponent<ComponentType<Props>>) {
  try {
    return initializeLazy(component);
  } catch (reason) {
    if (!reason || typeof (reason as PromiseLike<unknown>).then !== "function") throw reason;
    await Promise.resolve(reason).catch(() => undefined);
    return null;
  }
}

test("a scoped market lazy importer can reject once and resolve after an explicit retry", async () => {
  const scope = "market-retry-test";
  let attempts = 0;
  const reloadable = createReloadableLazy<{ label: string }>(scope, async () => {
    attempts += 1;
    if (attempts === 1) throw new Error("transient chunk failure");
    return { default: () => null };
  });

  const rejectedComponent = reloadable.controller.current;
  assert.equal(await waitForLazyAttempt(rejectedComponent), null);
  assert.throws(() => initializeLazy(rejectedComponent), /transient chunk failure/);
  assert.equal(attempts, 1);

  assert.equal(resetReloadableLazyScope(scope), 1);
  const retriedComponent = reloadable.controller.current;
  assert.notEqual(retriedComponent, rejectedComponent);
  assert.equal(await waitForLazyAttempt(retriedComponent), null);
  assert.equal(typeof initializeLazy(retriedComponent), "function");
  assert.equal(attempts, 2);
});

test("the module error boundary rebuilds its lazy scope before clearing the fallback", () => {
  const events: string[] = [];
  const boundary = new ModuleErrorBoundary({
    children: null,
    resetKey: "ai:chat",
    onRetry: () => { events.push("reset"); },
    onOpenDashboard: () => undefined,
  }) as unknown as {
    state: { failed: boolean };
    setState: (state: { failed: boolean }, callback: () => void) => void;
    retryCurrentModule: () => void;
  };
  boundary.state = { failed: true };
  boundary.setState = (state, callback) => {
    events.push("clear");
    boundary.state = state;
    callback();
  };

  boundary.retryCurrentModule();

  assert.deepEqual(events, ["reset", "clear"]);
  assert.equal(boundary.state.failed, false);
});
