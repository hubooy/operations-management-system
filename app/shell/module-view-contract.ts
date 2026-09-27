import {
  getDefaultModuleView,
  isModuleViewKey,
  type ModuleKey,
  type ModuleViewKey,
} from "./navigation-catalog";

export const moduleViewQueryKey = "view" as const;
export const moduleViewOwnedQueryKeys = [moduleViewQueryKey] as const;

const LOCAL_URL_ORIGIN = "https://teruisi-module-view.invalid";

function toUrl(input: string | URL): URL {
  return input instanceof URL ? new URL(input.toString()) : new URL(input, LOCAL_URL_ORIGIN);
}

function toRelativeUrl(url: URL): string {
  return `${url.pathname}${url.search}${url.hash}`;
}

function searchParamsFrom(input: string | URL | URLSearchParams): URLSearchParams {
  if (input instanceof URLSearchParams) return new URLSearchParams(input);
  return toUrl(input).searchParams;
}

/** Runtime-safe normalization for values received from URLs or browser history. */
export function normalizeModuleView<M extends ModuleKey>(module: M, value: unknown): ModuleViewKey<M> {
  return typeof value === "string" && isModuleViewKey(module, value)
    ? value
    : getDefaultModuleView(module);
}

/** Duplicate, missing, cross-module, and unknown values all resolve to the module default. */
export function parseModuleView<M extends ModuleKey>(
  module: M,
  input: string | URL | URLSearchParams,
): ModuleViewKey<M> {
  const values = searchParamsFrom(input).getAll(moduleViewQueryKey);
  return values.length === 1
    ? normalizeModuleView(module, values[0])
    : getDefaultModuleView(module);
}

/**
 * Pure view-only URL update. It preserves pathname, unrelated query fields,
 * and hash; default views are canonicalized by omitting the `view` parameter.
 */
export function serializeModuleViewLocation<M extends ModuleKey>(
  module: M,
  view: ModuleViewKey<M>,
  current: string | URL = "/",
): string {
  const url = toUrl(current);
  const normalized = normalizeModuleView(module, view);
  url.searchParams.delete(moduleViewQueryKey);
  if (normalized !== getDefaultModuleView(module)) url.searchParams.append(moduleViewQueryKey, normalized);
  return toRelativeUrl(url);
}
