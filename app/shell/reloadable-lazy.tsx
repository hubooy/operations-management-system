"use client";

import {
  createElement,
  lazy,
  type ComponentType,
  type LazyExoticComponent,
} from "react";

type LazyImporter<Props extends object> = () => Promise<{
  default: ComponentType<Props>;
}>;

export type ReloadableLazyController<Props extends object> = {
  readonly current: LazyExoticComponent<ComponentType<Props>>;
  reset: () => LazyExoticComponent<ComponentType<Props>>;
};

const scopeResets = new Map<string, Set<() => void>>();

export function createReloadableLazyController<Props extends object>(
  importer: LazyImporter<Props>,
): ReloadableLazyController<Props> {
  let current = lazy(importer);
  return {
    get current() {
      return current;
    },
    reset() {
      current = lazy(importer);
      return current;
    },
  };
}

export function createReloadableLazy<Props extends object>(
  scope: string,
  importer: LazyImporter<Props>,
) {
  const controller = createReloadableLazyController(importer);
  const reset = () => {
    controller.reset();
  };
  const resets = scopeResets.get(scope) ?? new Set<() => void>();
  resets.add(reset);
  scopeResets.set(scope, resets);

  function ReloadableLazyComponent(props: Props) {
    return createElement(controller.current, props);
  }
  ReloadableLazyComponent.displayName = `ReloadableLazy(${scope})`;

  return {
    Component: ReloadableLazyComponent,
    controller,
    reset,
  };
}

export function resetReloadableLazyScope(scope: string): number {
  const resets = scopeResets.get(scope);
  if (!resets) return 0;
  for (const reset of resets) reset();
  return resets.size;
}
