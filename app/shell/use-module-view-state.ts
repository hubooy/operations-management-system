"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getDefaultModuleView,
  type ModuleKey,
  type ModuleViewKey,
} from "./navigation-catalog";
import {
  normalizeShellLocation,
  parseShellLocation,
  updateModuleViewLocation,
} from "./navigation-contract";
import { normalizeModuleView } from "./module-view-contract";

export type ModuleViewSelection = {
  [M in ModuleKey]: { module: M; view: ModuleViewKey<M> };
}[ModuleKey];

const defaultSelection: ModuleViewSelection = {
  module: "dashboard",
  view: getDefaultModuleView("dashboard"),
};

function selectionsEqual(left: ModuleViewSelection, right: ModuleViewSelection): boolean {
  return left.module === right.module && left.view === right.view;
}

function createSelection<M extends ModuleKey>(module: M, view: unknown): ModuleViewSelection {
  return { module, view: normalizeModuleView(module, view) } as ModuleViewSelection;
}

function currentRelativeUrl(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

export function readModuleViewSelection(input: string | URL): ModuleViewSelection {
  const state = parseShellLocation(input);
  return createSelection(state.module, state.view);
}

/**
 * Owns the browser-history side of the controlled module view. Domain views
 * receive `selection.view` from the shell and call `pushView`; they never read
 * or mutate `window.location` independently.
 */
export function useModuleViewState() {
  const [selection, setSelectionState] = useState<ModuleViewSelection>(() => (
    typeof window === "undefined" ? defaultSelection : readModuleViewSelection(window.location.href)
  ));

  const setSelection = useCallback(<M extends ModuleKey>(module: M, view: ModuleViewKey<M>) => {
    const next = createSelection(module, view);
    setSelectionState((current) => selectionsEqual(current, next) ? current : next);
    return next;
  }, []);

  const syncFromLocation = useCallback((input?: string | URL) => {
    const hasBrowserLocation = typeof window !== "undefined";
    const source = input ?? (hasBrowserLocation ? window.location.href : "/");
    const next = readModuleViewSelection(source);
    setSelectionState((current) => selectionsEqual(current, next) ? current : next);

    if (input === undefined && hasBrowserLocation) {
      const canonical = normalizeShellLocation(window.location.href);
      if (canonical !== currentRelativeUrl()) window.history.replaceState(null, "", canonical);
    }
    return next;
  }, []);

  const pushView = useCallback(<M extends ModuleKey>(module: M, view: ModuleViewKey<M>) => {
    const normalized = normalizeModuleView(module, view);
    const current = typeof window === "undefined" ? "/" : window.location.href;
    const nextUrl = updateModuleViewLocation(current, module, normalized);
    if (typeof window !== "undefined" && nextUrl !== currentRelativeUrl()) {
      window.history.pushState(null, "", nextUrl);
    }
    setSelectionState((existing) => {
      const next = createSelection(module, normalized);
      return selectionsEqual(existing, next) ? existing : next;
    });
    return nextUrl;
  }, []);

  useEffect(() => {
    syncFromLocation();
    const onPopState = () => { syncFromLocation(); };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [syncFromLocation]);

  return { selection, syncFromLocation, setSelection, pushView } as const;
}
