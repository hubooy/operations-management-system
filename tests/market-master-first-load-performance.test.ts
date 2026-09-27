import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseMarketMasterView } from "../lib/market/admin-query-contract";
import { PublicApiError } from "../lib/http/api-error";

function between(source: string, start: string, end: string) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.ok(startIndex >= 0, `missing start marker: ${start}`);
  assert.ok(endIndex > startIndex, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("market master database first paint excludes deferred price, image, prompt, and model work", async () => {
  const [panel, route, service, backendAdmin] = await Promise.all([
    readFile(new URL("../app/market-master-admin-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/master/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/market/admin.py", import.meta.url), "utf8"),
  ]);

  const primaryClientLoad = between(panel, "  const load = useCallback", "  const loadDatabaseFilters = useCallback");
  assert.match(primaryClientLoad, /mode === "database" \? "database_primary" : "workspace"/);
  assert.equal((primaryClientLoad.match(/fetch\(/g) ?? []).length, 1);
  assert.doesNotMatch(primaryClientLoad, /pendingPricePage|pendingPriceSource|\/api\/ai\/models|database_secondary/);
  assert.match(primaryClientLoad, /signal: controller\.signal/);
  assert.match(primaryClientLoad, /primaryLoadControllerRef\.current\?\.abort\(\)/);
  assert.match(primaryClientLoad, /generation !== primaryLoadGenerationRef\.current/);

  const primaryService = between(service, "export async function getMarketMasterDatabasePrimary", "export async function getMarketMasterDatabaseFilters");
  assert.match(primaryService, /listMarketMasterData/);
  assert.doesNotMatch(primaryService, /market_ranking_entries GROUP BY category/);
  assert.doesNotMatch(primaryService, /market_subcategory_taxonomy/);
  assert.equal((primaryService.match(/db\.prepare\(/g) ?? []).length, 0, "primary view owns only the paginated list query");
  assert.doesNotMatch(primaryService, /listPendingMarketPrices|market_image_cache|market_annotation_prompt_versions|market_price_snapshots/);

  const filterService = between(service, "export async function getMarketMasterDatabaseFilters", "export async function getMarketMasterDatabaseSecondary");
  assert.match(filterService, /getCachedMarketMasterDatabaseFilters/);
  assert.match(filterService, /validateMarketMasterDatabaseFiltersCachePayload/);
  assert.match(filterService, /market_ranking_entries GROUP BY category/);
  assert.match(filterService, /market_subcategory_taxonomy/);
  assert.equal((filterService.match(/db\.prepare\(/g) ?? []).length, 2, "deferred filter view owns the two exact aggregates");

  assert.match(route, /const view = parseMarketMasterView\(params\)/);
  assert.match(route, /payload: \{ operation: "master", view, params: queryParams\(params\) \}/);
  assert.match(backendAdmin, /if view == "database_primary":[\s\S]+return \{"masterData": list_master\(params\)\}/);
  assert.match(backendAdmin, /if view == "database_filters":/);
  assert.match(backendAdmin, /if view == "database_secondary":/);
  assert.ok(route.indexOf("requireUnrestrictedDataScope") < route.indexOf("requestDjangoMarketService<JsonRecord>"));
});

test("market master filter aggregates start only after the paginated first paint and remain cancellable", async () => {
  const panel = await readFile(new URL("../app/market-master-admin-panel.tsx", import.meta.url), "utf8");
  const primaryClientLoad = between(panel, "  const load = useCallback", "  const loadDatabaseFilters = useCallback");
  const filterClientLoad = between(panel, "  const loadDatabaseFilters = useCallback", "  const loadDatabaseSecondary = useCallback");

  assert.match(primaryClientLoad, /setData\([\s\S]+setDatabaseFiltersScope/);
  assert.equal((filterClientLoad.match(/fetch\(/g) ?? []).length, 1);
  assert.match(filterClientLoad, /view: "database_filters"/);
  assert.match(filterClientLoad, /databaseFiltersScope !== scopeKey/);
  assert.match(filterClientLoad, /signal: controller\.signal/);
  assert.match(filterClientLoad, /databaseFiltersControllerRef\.current\?\.abort\(\)/);
  assert.match(filterClientLoad, /generation !== databaseFiltersGenerationRef\.current/);
  assert.match(panel, /if \(databaseFiltersScope !== scopeKey\) return;[\s\S]+window\.setTimeout\([\s\S]+loadDatabaseFilters/);
});

test("deferred database details are explicit, cancellable, generation-fenced, and models load once", async () => {
  const [panel, service] = await Promise.all([
    readFile(new URL("../app/market-master-admin-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8"),
  ]);

  const secondaryClientLoad = between(panel, "  const loadDatabaseSecondary = useCallback", "  const reloadLatest = useCallback");
  assert.equal((secondaryClientLoad.match(/fetch\(/g) ?? []).length, 1);
  assert.match(secondaryClientLoad, /view: "database_secondary"/);
  assert.match(secondaryClientLoad, /pendingPriceSources\.forEach/);
  assert.match(secondaryClientLoad, /signal: controller\.signal/);
  assert.match(secondaryClientLoad, /secondaryLoadControllerRef\.current\?\.abort\(\)/);
  assert.match(secondaryClientLoad, /generation !== secondaryLoadGenerationRef\.current/);

  assert.match(panel, /databaseSecondaryRequested && \(databaseSecondaryLoaded \?/);
  assert.match(panel, /className="panel data-refresh-region" aria-busy=\{databaseSecondaryLoading\}/);
  assert.match(panel, /onClick=\{\(\) => \{ setDatabaseSecondaryLoading\(true\); setDatabaseSecondaryRequested\(true\); \}\}/);
  assert.equal((panel.match(/\/api\/ai\/models/g) ?? []).length, 1);
  assert.match(panel, /if \(!shouldLoad \|\| aiModelsRequestedRef\.current\) return;/);
  assert.match(panel, /aiModelsRequestedRef\.current = true;[\s\S]+fetch\("\/api\/ai\/models"/);
  assert.match(panel, /queueMicrotask\(\(\) => \{[\s\S]+generation !== lifecycleGenerationRef\.current[\s\S]+aiModelsControllerRef\.current\?\.abort\(\)/);

  const secondaryService = between(service, "export async function getMarketMasterDatabaseSecondary", "export async function getMarketSettingsStatus");
  assert.match(secondaryService, /listPendingMarketPrices/);
  assert.match(secondaryService, /FROM market_image_cache/);
  assert.match(secondaryService, /market_annotation_prompt_versions/);
  assert.match(secondaryService, /FROM market_price_snapshots/);
  assert.equal((secondaryService.match(/db\.prepare\(/g) ?? []).length, 3, "secondary view owns exactly the three deferred aggregate statements");
});

test("parallel market master channels cannot clear each other's errors or expose stale secondary rows", async () => {
  const panel = await readFile(new URL("../app/market-master-admin-panel.tsx", import.meta.url), "utf8");
  const primaryClientLoad = between(panel, "  const load = useCallback", "  const loadDatabaseFilters = useCallback");
  const filterClientLoad = between(panel, "  const loadDatabaseFilters = useCallback", "  const loadDatabaseSecondary = useCallback");
  const secondaryClientLoad = between(panel, "  const loadDatabaseSecondary = useCallback", "  const reloadLatest = useCallback");
  const modelClientLoad = between(panel, "    const shouldLoad = isAdmin", "  useEffect(() => {\n    const generation = lifecycleGenerationRef");

  assert.match(primaryClientLoad, /setPrimaryError\(""\)/);
  assert.doesNotMatch(primaryClientLoad, /setError\(/);
  assert.match(filterClientLoad, /setDatabaseFiltersError\(""\)/);
  assert.doesNotMatch(filterClientLoad, /setError\(/);
  assert.match(secondaryClientLoad, /setSecondaryError\(""\)/);
  assert.doesNotMatch(secondaryClientLoad, /setDatabaseSecondaryLoaded\(false\)/);
  assert.match(secondaryClientLoad, /setDatabaseSecondaryLoaded\(true\)/);
  assert.doesNotMatch(secondaryClientLoad, /setError\(/);
  assert.match(modelClientLoad, /setAiModelsError\(""\)/);
  assert.match(modelClientLoad, /setAiModelsError\(reason instanceof Error/);
  assert.doesNotMatch(modelClientLoad, /setError\(/);

  assert.match(panel, /Promise\.all\(\[[\s\S]+load\(\)\.catch\(\(reason\) => setPrimaryError/);
  assert.match(panel, /loadDatabaseSecondary\(\)\.catch\(\(reason\) => setSecondaryError/);
  assert.doesNotMatch(panel, /Promise\.allSettled\(\[load\(\), loadDatabaseSecondary\(\)\]\)/);
  assert.match(panel, /if \(!databaseSecondaryRequested \|\| mode !== "database"\) return;[\s\S]+setDatabaseSecondaryLoading\(true\);/);
  assert.doesNotMatch(panel, /setDatabaseSecondaryLoaded\(false\)/);
  assert.match(panel, /databaseSecondaryRequested && \(databaseSecondaryLoaded \?/);
  assert.match(panel, /const fatalError = primaryError;/);
  assert.match(panel, /const visibleError = error \|\| fatalError \|\| relevantDatabaseFiltersError \|\| relevantSecondaryError \|\| relevantAiModelsError;/);
  assert.match(panel, /if \(!data && fatalError\)/);
  assert.match(panel, /mode === "database" && databaseSecondaryRequested \? secondaryError : ""/);
  assert.match(panel, /mode === "brand" \|\| \(mode === "database" && databaseSecondaryRequested\) \? aiModelsError : ""/);
});

test("market settings loads a lightweight truthful status instead of reusing ranking overview", async () => {
  const [view, route, service, backendAdmin] = await Promise.all([
    readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/master/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/market/admin.py", import.meta.url), "utf8"),
  ]);
  const settingsEffect = between(view, "  useEffect(() => {\n    if (activeSection !== \"settings\")", "  const loadMore = useCallback");
  assert.match(settingsEffect, /view=settings_status/);
  assert.match(settingsEffect, /controller\.signal/);
  assert.match(settingsEffect, /generation !== settingsStatusGenerationRef\.current/);
  assert.match(settingsEffect, /setSettingsStatus\(null\)/);
  assert.match(settingsEffect, /setSettingsStatusRequestKey\(requestKey\)/);
  assert.match(settingsEffect, /setSettingsStatusScopeKey\(requestKey\)/);
  assert.match(view, /settingsStatusScopeKey === settingsStatusRequestKey/);
  assert.match(view, /settingsStatusReloadScope === reloadKey/);
  assert.doesNotMatch(view, /settingsStatus \?\? data/);
  assert.match(view, /activeSection === "settings" && settingsStatusLoading && !settingsData/);
  assert.match(view, /shanghaiToday\(\)/);
  assert.doesNotMatch(view, /data\?\.dataRange\.startDate \?\? new Date\(\)\.toISOString/);
  assert.match(route, /parseMarketMasterView\(params\)/);
  assert.match(backendAdmin, /if view == "settings_status":[\s\S]+return settings_status\(\)/);
  const statusService = between(service, "export async function getMarketSettingsStatus", "export async function confirmMarketPrice");
  assert.match(statusService, /MIN\(period_start\)[\s\S]*MAX\(period_end\)/);
  assert.match(statusService, /SELECT \$\{marketBatchColumns\}[\s\S]*FROM market_import_batches ORDER BY created_at DESC LIMIT 8/);
  assert.match(statusService, /batches\.results \?\? \[\]\)\.map\(\(row\) => mapMarketBatch\(row\)\)/);
  assert.match(statusService, /FROM market_image_cache/);
  assert.doesNotMatch(statusService, /getMarketOverview|market_effective|buildMarketOverview/);
});

test("market master view projection preserves the legacy default but rejects duplicates and unknown values", () => {
  assert.equal(parseMarketMasterView(new URLSearchParams()), "workspace");
  assert.equal(parseMarketMasterView(new URLSearchParams("view=system_kpis")), "system_kpis");
  assert.equal(parseMarketMasterView(new URLSearchParams("view=database_filters")), "database_filters");
  for (const query of ["view=unknown", "view=workspace&view=workspace"]) {
    assert.throws(
      () => parseMarketMasterView(new URLSearchParams(query)),
      (error) => error instanceof PublicApiError && error.status === 400 && error.code === "invalid_request",
    );
  }
});

test("market master pagination applies the unique id tie-breaker inside and outside both query paths", async () => {
  const service = await readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8");
  const listService = between(service, "export async function listMarketMasterData", "export async function listPendingMarketPrices");

  assert.equal((listService.match(/CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank, id ASC/g) ?? []).length, 2,
    "historical and current page selection must use the same unique id tie-breaker");
  assert.match(listService, /CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank, paged\.id ASC/,
    "historical detail ordering must preserve the selected page order");
  assert.match(listService, /CASE WHEN page_ids\.rank IS NULL THEN 1 ELSE 0 END, page_ids\.rank, page_ids\.id ASC/,
    "current detail ordering must preserve the selected page order");
});
