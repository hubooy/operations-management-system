import {
  getDefaultModuleView,
  isImportSourceKey,
  isModuleKey,
  type ImportSourceKey,
  type ModuleKey,
  type ModuleViewKey,
} from "./navigation-catalog";
import { normalizeModuleView, parseModuleView } from "./module-view-contract";

export const shellPeriodKeys = [
  "today",
  "yesterday",
  "last7",
  "last15",
  "last30",
  "previous_year",
  "current_month",
  "calendar_month",
  "custom",
] as const;

export type ShellPeriodKey = (typeof shellPeriodKeys)[number];

export type ShellPeriodState =
  | { kind: "today" | "yesterday" | "last7" | "last15" | "last30" | "current_month" }
  | { kind: "calendar_month"; month: string }
  | { kind: "custom" | "previous_year"; from: string; to: string };

export type ShellLocationState<M extends ModuleKey = ModuleKey> = {
  module: M;
  view: ModuleViewKey<M>;
  source?: ImportSourceKey;
  period: ShellPeriodState;
};

export type ShellLocationInput<M extends ModuleKey = ModuleKey> =
  Omit<ShellLocationState<M>, "view"> & { view?: ModuleViewKey<M> };

export const shellOwnedQueryKeys = ["module", "view", "salesTab", "source", "period", "month", "from", "to"] as const;

const relativeOrCurrentPeriodKeys: ReadonlySet<string> = new Set([
  "today",
  "yesterday",
  "last7",
  "last15",
  "last30",
  "current_month",
]);

const LOCAL_URL_ORIGIN = "https://teruisi-shell.invalid";

function toUrl(input: string | URL): URL {
  return input instanceof URL ? new URL(input.toString()) : new URL(input, LOCAL_URL_ORIGIN);
}

function toRelativeUrl(url: URL): string {
  return `${url.pathname}${url.search}${url.hash}`;
}

function singleQueryValue(params: URLSearchParams, key: string): string | null {
  const values = params.getAll(key);
  return values.length === 1 ? values[0] : null;
}

function isIsoMonth(value: string): boolean {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(value);
}

function isIsoDate(value: string): boolean {
  if (!/^\d{4}-(0[1-9]|1[0-2])-([0-2]\d|3[01])$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day;
}

function parsePeriod(params: URLSearchParams): ShellPeriodState {
  const period = singleQueryValue(params, "period");
  if (period === null || period === "current_month") return { kind: "current_month" };
  if (relativeOrCurrentPeriodKeys.has(period)) {
    return { kind: period as "today" | "yesterday" | "last7" | "last15" | "last30" };
  }
  if (period === "calendar_month") {
    const month = singleQueryValue(params, "month");
    return month !== null && isIsoMonth(month)
      ? { kind: "calendar_month", month }
      : { kind: "current_month" };
  }
  if (period === "custom" || period === "previous_year") {
    const from = singleQueryValue(params, "from");
    const to = singleQueryValue(params, "to");
    return from !== null && to !== null && isIsoDate(from) && isIsoDate(to) && from <= to
      ? { kind: period, from, to }
      : { kind: "current_month" };
  }
  return { kind: "current_month" };
}

function parseShellView<M extends ModuleKey>(module: M, params: URLSearchParams): ModuleViewKey<M> {
  if (params.has("view") || module !== "sales") return parseModuleView(module, params);
  const legacySalesView = singleQueryValue(params, "salesTab");
  return normalizeModuleView(module, legacySalesView);
}

export function parseShellLocation(input: string | URL): ShellLocationState {
  const url = toUrl(input);
  const moduleValue = singleQueryValue(url.searchParams, "module");
  const activeModule = moduleValue !== null && isModuleKey(moduleValue) ? moduleValue : "dashboard";
  const sourceValue = singleQueryValue(url.searchParams, "source");
  const source = activeModule === "import" && sourceValue !== null && isImportSourceKey(sourceValue)
    ? sourceValue
    : undefined;
  return {
    module: activeModule,
    view: parseShellView(activeModule, url.searchParams),
    ...(source ? { source } : {}),
    period: parsePeriod(url.searchParams),
  };
}

function writeShellState<M extends ModuleKey>(url: URL, state: ShellLocationInput<M>): void {
  const currentModuleValue = singleQueryValue(url.searchParams, "module");
  const currentModule = currentModuleValue !== null && isModuleKey(currentModuleValue)
    ? currentModuleValue
    : "dashboard";
  const requestedView = state.view ?? (currentModule === state.module
    ? parseShellView(state.module, url.searchParams)
    : undefined);

  for (const key of shellOwnedQueryKeys) url.searchParams.delete(key);

  if (state.module !== "dashboard") url.searchParams.append("module", state.module);
  const view = normalizeModuleView(state.module, requestedView);
  if (view !== getDefaultModuleView(state.module)) url.searchParams.append("view", view);
  if (state.module === "import" && state.source && isImportSourceKey(state.source)) {
    url.searchParams.append("source", state.source);
  }

  if (state.period.kind === "current_month") return;
  url.searchParams.append("period", state.period.kind);
  if (state.period.kind === "calendar_month") url.searchParams.append("month", state.period.month);
  if (state.period.kind === "custom" || state.period.kind === "previous_year") {
    url.searchParams.append("from", state.period.from);
    url.searchParams.append("to", state.period.to);
  }
}

export function serializeShellLocation<M extends ModuleKey>(
  state: ShellLocationInput<M>,
  current: string | URL = "/",
): string {
  const url = toUrl(current);
  writeShellState(url, state);
  return toRelativeUrl(url);
}

export function normalizeShellLocation(input: string | URL): string {
  const url = toUrl(input);
  writeShellState(url, parseShellLocation(url));
  return toRelativeUrl(url);
}

/**
 * Pure shell URL transition used by tabs and history navigation. The current
 * period and a valid import source survive a view change; unrelated query
 * fields and the hash are preserved by the serializer.
 */
export function updateModuleViewLocation<M extends ModuleKey>(
  input: string | URL,
  module: M,
  view: ModuleViewKey<M>,
): string {
  const current = parseShellLocation(input);
  return serializeShellLocation({
    module,
    view: normalizeModuleView(module, view),
    ...(module === "import" && current.module === "import" && current.source
      ? { source: current.source }
      : {}),
    period: current.period,
  }, input);
}
