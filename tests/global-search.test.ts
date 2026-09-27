import { GLOBAL_SEARCH_SCHEMA_TABLE_AUDIT } from "./global-search-schema-audit";
import type { MarketConsumerReader, MarketConsumerResponseMap, MarketSearchItem } from "../lib/django/market-consumer-reader";
import { handleSearchAllSystemDataTool } from "../lib/search/global-search-tool";
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import type { AppPrincipal } from "../lib/auth/authorization";
import type { SalesConsumerReader } from "../lib/django/sales-consumer-reader";
import type { FinanceConsumerReader } from "../lib/django/finance-consumer-reader";
import type { NetshopConsumerReader } from "../lib/django/netshop-consumer-reader";
import type { ProductsConsumerReader } from "../lib/django/products-consumer-reader";
import type { InventoryConsumerReader } from "../lib/django/inventory-consumer-reader";
import type { WorkflowConsumerReader } from "../lib/django/workflow-consumer-reader";
import type { CustomerServiceConsumerReader } from "../lib/django/customer-service-consumer-reader";
import type { ErpReferenceConsumerReader } from "../lib/django/erp-reference-consumer-reader";
import { globalSearchErrorResponse } from "../lib/search/api-response";

import {
  GlobalSearchRequestError,
  normalizeGlobalSearchRequest,
  searchAllBusinessData,
  type GlobalSearchExecutionOptions,
} from "../lib/search/global-search";
import {
  getGlobalSearchNavigationTarget,
  globalSearchDefaultTargets,
  globalSearchGroupKeys,
  isGlobalSearchNavigationTargetForGroup,
} from "../lib/search/target-contract";

const admin: AppPrincipal = { email: "admin@example.com", displayName: "Admin", role: "admin", scope: null };
const viewer: AppPrincipal = { email: "viewer@example.com", displayName: "Viewer", role: "viewer", scope: null };

type Call = { principal: AppPrincipal; request: Record<string, unknown> };
function fakeMarketReader(input: { items?: MarketSearchItem[]; imports?: MarketConsumerResponseMap["import_batch_search"]["items"]; calls?: Call[] } = {}): MarketConsumerReader {
  return { read: (async (principal, request) => {
    input.calls?.push({ principal, request });
    const all = request.operation === "import_batch_search" ? input.imports ?? [] : input.items ?? [];
    return { revision: "5:abcdef123456", data: {
      items: all.slice(request.offset, request.offset + request.limit), total: all.length,
      truncated: request.offset + request.limit < all.length,
    } };
  }) as MarketConsumerReader["read"] };
}
function readers(calls?: Call[]): GlobalSearchExecutionOptions {
  return {
    salesReader: fakeSalesSearchReader({ calls }), financeReader: fakeFinanceSearchReader({ calls }),
    netshopReader: fakeNetshopSearchReader({ calls }), productsReader: fakeProductsSearchReader({ calls }),
    inventoryReader: fakeInventorySearchReader({ calls }), workflowReader: fakeWorkflowSearchReader({ calls }),
    customerServiceReader: fakeCustomerServiceSearchReader({ calls }), erpReferenceReader: fakeErpReferenceSearchReader({ calls }),
    marketReader: fakeMarketReader({ calls }),
  };
}
function request(group?: string, extra = "") {
  return normalizeGlobalSearchRequest(new URLSearchParams(`q=净水机${group ? `&group=${group}` : ""}${extra}`));
}
const item: MarketSearchItem = { id: "sku-1", title: "净水机", subtitle: "SKU · 志高", detail: "厨房", updatedAt: "2026-09-05", amountCents: 12345 };
const batch = (id: string) => ({ id, source: "导入", fileName: `${id}.xlsx`, status: "completed", rowCount: 2, createdAt: "2026-09-05", completedAt: null });

test("all fourteen groups work with Django readers and no database binding", async () => {
  const calls: Call[] = [];
  const result = await searchAllBusinessData(request(), admin, readers(calls));
  assert.equal(result.groups.length, 14);
  assert.deepEqual(result.unavailableDomains, []);
  assert.ok(result.groups.every((group) => group.available && group.totalExact && group.total === 0));
  assert.ok(calls.every((call) => call.principal === admin));
});

for (const group of ["market_skus", "market_annotations"] as const) {
  test(`${group} uses exact bounded Django pages and literal query text`, async () => {
    const calls: Call[] = [];
    const query = "A%_\\B' OR 1=1";
    const result = await searchAllBusinessData(normalizeGlobalSearchRequest(new URLSearchParams({ q: query, group, page: "2", limit: "1" })), admin,
      { ...readers(), marketReader: fakeMarketReader({ items: [item, { ...item, id: "sku-2" }], calls }) });
    assert.deepEqual(calls[0], { principal: admin, request: { operation: group === "market_skus" ? "sku_search" : "annotation_search", query, offset: 1, limit: 1 } });
    assert.equal(calls.length, 1);
    assert.equal(result.groups[0].total, 2);
    assert.equal(result.groups[0].totalExact, true);
    assert.equal(result.groups[0].hasMore, false);
    assert.equal(result.groups[0].items[0].amountCents, 12345);
    assert.deepEqual(result.groups[0].items[0].target, globalSearchDefaultTargets[group]);
    const empty = await searchAllBusinessData(request(group, "&page=3&limit=1"), admin,
      { ...readers(), marketReader: fakeMarketReader({ items: [item, item] }) });
    assert.equal(empty.returned, 0);
    assert.equal(empty.groups[0].total, 2);
  });
}

test("a broken market service cannot take down another search domain", async () => {
  const result = await searchAllBusinessData(request(), admin, { ...readers(),
    marketReader: { read: async () => { throw new Error("private backend details"); } },
    salesReader: fakeSalesSearchReader({ orders: [{ ...item, amountCents: 10 }] }),
  });
  assert.equal(result.groups.find((group) => group.key === "orders")?.items.length, 1);
  assert.equal(result.groups.find((group) => group.key === "market_skus")?.available, false);
  assert.doesNotMatch(JSON.stringify(result), /private backend/);
});

for (const malformed of [
  { revision: "", data: { items: [item], total: 1, truncated: false } },
  { revision: "5:abcdef123456", data: { items: [item], total: 2, truncated: false } },
  { revision: "5:abcdef123456", data: { items: [{ ...item, amountCents: 0.1 }], total: 1, truncated: false } },
  { revision: "5:abcdef123456", data: { items: [], total: -1, truncated: false } },
]) {
  test(`invalid market response fails closed: ${JSON.stringify(malformed)}`, async () => {
    const result = await searchAllBusinessData(request("market_skus"), admin,
      { marketReader: { read: async () => malformed } as MarketConsumerReader });
    assert.equal(result.groups[0].available, false);
    assert.equal(result.groups[0].totalExact, false);
    assert.equal(result.returned, 0);
  });
}

test("restricted scope reaches the owning readers and excludes unscoped domains", async () => {
  const calls: Call[] = [];
  const principal: AppPrincipal = { ...admin, scope: { warehouses: ["京东仓"], channels: ["设备店"], platforms: ["京东"] } };
  const result = await searchAllBusinessData(request(), principal, readers(calls));
  assert.ok(calls.every((call) => call.principal === principal));
  assert.ok(!result.groups.some((group) => ["market_skus", "market_annotations", "customer_service", "imports"].includes(group.key)));
  assert.deepEqual(result.filtersApplied.dataScope.warehouses, ["京东仓"]);
});

test("viewer cannot search finance, import receipts or customer message bodies", async () => {
  const calls: Call[] = [];
  const result = await searchAllBusinessData(request(), viewer, readers(calls));
  for (const denied of ["finance", "targets", "customer_service", "imports", "replenishment"]) {
    assert.ok(!result.groups.some((group) => group.key === denied));
  }
  assert.ok(!calls.some((call) => ["line_search", "target_search", "search", "import_batch_search"].includes(String(call.request.operation))));
});

test("only explicit customer-service searches enable message matching", async () => {
  const calls: Call[] = [];
  await searchAllBusinessData(request(), admin, readers(calls));
  assert.equal(calls.find((call) => call.request.operation === "search")?.request.includeMessages, false);
  calls.length = 0;
  await searchAllBusinessData(request("customer_service"), admin, readers(calls));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].request.includeMessages, true);
});

test("all eight import sources retain exact cross-source pagination including market", async () => {
  const options = { ...readers(),
    salesReader: fakeSalesSearchReader({ imports: [batch("sales-1"), batch("sales-2")] }),
    financeReader: fakeFinanceSearchReader({ imports: [batch("finance")] }),
    netshopReader: fakeNetshopSearchReader({ imports: [{ ...batch("netshop"), dataset: "sku", platform: "京东", shopName: "设备店" }] }),
    productsReader: fakeProductsSearchReader({ imports: [batch("products")] }),
    inventoryReader: fakeInventorySearchReader({ imports: [{ ...batch("inventory"), dataset: "stock" }] }),
    customerServiceReader: fakeCustomerServiceSearchReader({ imports: [batch("customer")] }),
    erpReferenceReader: fakeErpReferenceSearchReader({ imports: [batch("erp")] }),
    marketReader: fakeMarketReader({ imports: [{ ...batch("market"), sourceType: "jd-ranking", periodStart: "2026-09-01", periodEnd: "2026-09-05" }] }),
  };
  const ids: string[] = [];
  for (let page = 1; page <= 6; page++) {
    const result = await searchAllBusinessData(request("imports", `&page=${page}&limit=2`), admin, options);
    assert.equal(result.groups[0].total, 9);
    assert.equal(result.groups[0].totalExact, true);
    assert.equal(result.groups[0].hasMore, page < 5);
    ids.push(...result.groups[0].items.map((entry) => entry.id));
  }
  assert.deepEqual(ids, ["sales-1", "sales-2", "finance", "netshop", "products", "inventory", "customer", "erp", "market"]);
});

test("market revision change between import count and page invalidates the combined page", async () => {
  let calls = 0;
  const inner = fakeMarketReader({ imports: [{ ...batch("market"), sourceType: "jd", periodStart: "", periodEnd: "" }] });
  const marketReader: MarketConsumerReader = { read: async (...args) => {
    const result = await inner.read(...args);
    return { ...result, revision: ++calls === 1 ? "1:abcdef123456" : "2:abcdef123456" };
  } };
  const result = await searchAllBusinessData(request("imports"), admin, { ...readers(), marketReader });
  assert.equal(result.returned, 0);
  assert.equal(result.groups[0].available, false);
  assert.equal(result.groups[0].totalExact, false);
});

test("workflow results preserve the exact launch, review and inspection navigation", async () => {
  const result = await searchAllBusinessData(request("workflow"), admin, { workflowReader: fakeWorkflowSearchReader({
    items: ["launch", "review", "inspection"].map((hint) => ({ ...item, resultId: hint, targetHint: hint as "launch" | "review" | "inspection" })),
  }) });
  assert.deepEqual(result.groups[0].items.map((entry) => entry.target.view), ["launch", "reviews", "inspection"]);
});

test("search bounds projected text and the response total", async () => {
  const huge = { ...item, title: "净".repeat(5000), detail: "水".repeat(5000) };
  const result = await searchAllBusinessData(request(undefined, "&totalLimit=1"), admin, {
    ...readers(), salesReader: fakeSalesSearchReader({ orders: [{ ...huge, amountCents: 10 }] }),
    marketReader: fakeMarketReader({ items: [huge] }),
  });
  assert.equal(result.returned, 1);
  assert.equal(result.truncated, true);
  const entry = result.groups.flatMap((group) => group.items)[0];
  assert.ok(entry.title.length <= 200 && entry.detail.length <= 400);
});

test("AI search uses the same Django core with real principal and bounded arguments", async () => {
  const calls: Call[] = [];
  const result = await handleSearchAllSystemDataTool({ query: "净水机", domain: "market_skus", perGroupLimit: 1 }, admin,
    { ...readers(), marketReader: fakeMarketReader({ items: [item], calls }) });
  assert.equal(result.monetaryUnit, "cents");
  assert.equal(calls[0].principal, admin);
  assert.equal(result.returned, 1);
  await assert.rejects(handleSearchAllSystemDataTool({ query: "净水机", role: "admin" }, viewer), /不支持的参数/);
});

test("group concurrency stays bounded and a deadline prevents queued calls", async () => {
  const options = readers();
  let active = 0, maximum = 0, calls = 0;
  for (const reader of Object.values(options)) {
    if (!reader || typeof reader !== "object" || !("read" in reader)) continue;
    const wrapped = reader as { read: (...args: unknown[]) => Promise<unknown> };
    const original = wrapped.read.bind(wrapped);
    wrapped.read = async (...args: unknown[]) => {
      calls++; active++; maximum = Math.max(maximum, active);
      await wait(30); active--;
      return original(...args);
    };
  }
  const result = await searchAllBusinessData(request(), viewer, { ...options, deadlineMs: 5 });
  assert.equal(result.deadlineExceeded, true);
  assert.ok(result.timedOutDomains.length > 0);
  assert.equal(calls, 3);
  await wait(45);
  assert.equal(calls, 3);
  assert.equal(maximum, 3);
});

type ErpSearchItem = {
  resultId: string; title: string; subtitle: string; detail: string;
  updatedAt: string; amountCents: null;
};
type ErpImportItem = {
  id: string; source: string; fileName: string; status: string;
  rowCount: number; createdAt: string; completedAt: string | null;
};

function fakeErpReferenceSearchReader(input: {
  products?: ErpSearchItem[];
  combos?: ErpSearchItem[];
  imports?: ErpImportItem[];
  calls?: Array<{ principal: AppPrincipal; request: Record<string, unknown> }>;
} = {}): ErpReferenceConsumerReader {
  return {
    read: (async (principal: AppPrincipal, request: Record<string, unknown>) => {
      input.calls?.push({ principal, request });
      const allItems = request.operation === "product_search"
        ? input.products ?? []
        : request.operation === "combo_search"
          ? input.combos ?? []
          : request.operation === "import_batch_search"
            ? input.imports ?? []
            : (() => { throw new Error(`unexpected operation ${String(request.operation)}`); })();
      const offset = Number(request.offset);
      const limit = Number(request.limit);
      return {
        revision: "erp:7:abcdef123456",
        data: {
          items: allItems.slice(offset, offset + limit),
          total: allItems.length,
          truncated: offset + limit < allItems.length,
        },
      };
    }) as ErpReferenceConsumerReader["read"],
  };
}

function wait(milliseconds: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, milliseconds));
}

function fakeSalesSearchReader(input: {
  orders?: Array<{ id: string; title: string; subtitle: string; detail: string; updatedAt: string; amountCents: number }>;
  orderTotal?: number;
  imports?: Array<{ id: string; source: string; fileName: string; status: string; rowCount: number; createdAt: string; completedAt: string | null }>;
  importTotal?: number;
  calls?: Array<{ principal: AppPrincipal; request: Record<string, unknown> }>;
} = {}): SalesConsumerReader {
  return {
    read: (async (principal: AppPrincipal, request: Record<string, unknown>) => {
      input.calls?.push({ principal, request });
      if (request.operation === "order_search") {
        const allItems = input.orders ?? [];
        const page = Number(request.page);
        const pageSize = Number(request.pageSize);
        const total = input.orderTotal ?? allItems.length;
        return {
          revision: "9:1",
          data: { items: allItems.slice((page - 1) * pageSize, page * pageSize), total, truncated: page * pageSize < total },
        };
      }
      if (request.operation === "import_batch_search") {
        const allItems = input.imports ?? [];
        const page = Number(request.page);
        const pageSize = Number(request.pageSize);
        const total = input.importTotal ?? allItems.length;
        return {
          revision: "9:1",
          data: { items: allItems.slice((page - 1) * pageSize, page * pageSize), total, truncated: page * pageSize < total },
        };
      }
      throw new Error(`unexpected operation ${String(request.operation)}`);
    }) as SalesConsumerReader["read"],
  };
}

function fakeFinanceSearchReader(input: {
  lines?: Array<{ id: string; title: string; subtitle: string; detail: string; updatedAt: string; amountCents: number | null }>;
  targets?: Array<{ id: string; title: string; subtitle: string; detail: string; updatedAt: string; amountCents: number | null }>;
  imports?: Array<{ id: string; source: string; fileName: string; status: string; rowCount: number; createdAt: string; completedAt: string | null }>;
  calls?: Array<{ principal: AppPrincipal; request: Record<string, unknown> }>;
} = {}): FinanceConsumerReader {
  return {
    read: (async (principal: AppPrincipal, request: Record<string, unknown>) => {
      input.calls?.push({ principal, request });
      const allItems = request.operation === "line_search"
        ? input.lines ?? []
        : request.operation === "target_search"
          ? input.targets ?? []
          : input.imports ?? [];
      const offset = Number(request.offset);
      const limit = Number(request.limit);
      return {
        revision: "3:abcdef123456",
        data: {
          items: allItems.slice(offset, offset + limit),
          total: allItems.length,
          truncated: offset + limit < allItems.length,
        },
      };
    }) as FinanceConsumerReader["read"],
  };
}

function fakeNetshopSearchReader(input: {
  rows?: Array<{ id: string; title: string; subtitle: string; detail: string; updatedAt: string; amountCents: null }>;
  imports?: Array<{ id: string; source: string; dataset: string; platform: string; shopName: string; fileName: string; status: string; rowCount: number; createdAt: string; completedAt: string | null }>;
  calls?: Array<{ principal: AppPrincipal; request: Record<string, unknown> }>;
} = {}): NetshopConsumerReader {
  return {
    read: (async (principal: AppPrincipal, request: Record<string, unknown>) => {
      input.calls?.push({ principal, request });
      const allItems = request.operation === "row_search" ? input.rows ?? [] : input.imports ?? [];
      const offset = Number(request.offset);
      const limit = Number(request.limit);
      return {
        revision: "4:abcdef123456",
        data: {
          items: allItems.slice(offset, offset + limit),
          total: allItems.length,
          truncated: offset + limit < allItems.length,
        },
      };
    }) as NetshopConsumerReader["read"],
  };
}

function fakeProductsSearchReader(input: {
  imports?: Array<{ id: string; source: string; fileName: string; status: string; rowCount: number; createdAt: string; completedAt: string | null }>;
  calls?: Array<{ principal: AppPrincipal; request: Record<string, unknown> }>;
} = {}): ProductsConsumerReader {
  return {
    read: (async (principal: AppPrincipal, request: Record<string, unknown>) => {
      input.calls?.push({ principal, request });
      if (request.operation !== "import_batch_search") {
        throw new Error(`unexpected operation ${String(request.operation)}`);
      }
      const allItems = input.imports ?? [];
      const offset = Number(request.offset);
      const limit = Number(request.limit);
      return {
        revision: "2:abcdef123456",
        data: {
          items: allItems.slice(offset, offset + limit),
          total: allItems.length,
          truncated: offset + limit < allItems.length,
        },
      };
    }) as ProductsConsumerReader["read"],
  };
}

type InventorySearchItem = {
  id: string;
  title: string;
  subtitle: string;
  detail: string;
  updatedAt: string;
  amountCents: number | null;
};

type InventoryImportItem = {
  id: string;
  source: string;
  dataset: string;
  fileName: string;
  status: string;
  rowCount: number;
  createdAt: string;
  completedAt: string | null;
};

function fakeInventorySearchReader(input: {
  inventory?: InventorySearchItem[];
  age?: InventorySearchItem[];
  replenishment?: InventorySearchItem[];
  imports?: InventoryImportItem[];
  calls?: Array<{ principal: AppPrincipal; request: Record<string, unknown> }>;
} = {}): InventoryConsumerReader {
  return {
    read: (async (principal: AppPrincipal, request: Record<string, unknown>) => {
      input.calls?.push({ principal, request });
      const allItems = request.operation === "inventory_search"
        ? input.inventory ?? []
        : request.operation === "age_search"
          ? input.age ?? []
          : request.operation === "replenishment_search"
            ? input.replenishment ?? []
            : request.operation === "import_batch_search"
              ? input.imports ?? []
              : (() => { throw new Error(`unexpected operation ${String(request.operation)}`); })();
      const offset = Number(request.offset);
      const limit = Number(request.limit);
      return {
        revision: "inventory:2:abcdef123456",
        data: {
          items: allItems.slice(offset, offset + limit),
          total: allItems.length,
          truncated: offset + limit < allItems.length,
        },
      };
    }) as InventoryConsumerReader["read"],
  };
}

function fakeWorkflowSearchReader(input: {
  items?: Array<{
    resultId: string;
    targetHint: "task" | "inspection" | "review" | "launch";
    title: string;
    subtitle: string;
    detail: string;
    updatedAt: string;
    amountCents: number | null;
  }>;
  total?: number;
  calls?: Array<{ principal: AppPrincipal; request: Record<string, unknown> }>;
} = {}): WorkflowConsumerReader {
  return {
    async read(principal, request) {
      input.calls?.push({ principal, request });
      assert.equal(request.operation, "workflow_search");
      const items = input.items ?? [];
      return {
        revision: "3:abcdef123456",
        data: {
          items: items.slice(request.offset, request.offset + request.limit),
          total: input.total ?? items.length,
          truncated: request.offset + request.limit < (input.total ?? items.length),
        },
      };
    },
  };
}

function fakeCustomerServiceSearchReader(input: {
  conversations?: Array<{ resultId: string; title: string; subtitle: string; detail: string; updatedAt: string; amountCents: null }>;
  imports?: Array<{ id: string; source: string; fileName: string; status: string; rowCount: number; createdAt: string; completedAt: string | null }>;
  calls?: Array<{ principal: AppPrincipal; request: Record<string, unknown> }>;
} = {}): CustomerServiceConsumerReader {
  return {
    read: (async (principal: AppPrincipal, request: Record<string, unknown>) => {
      input.calls?.push({ principal, request });
      const allItems = request.operation === "search" ? input.conversations ?? [] : input.imports ?? [];
      const offset = Number(request.offset);
      const limit = Number(request.limit);
      return {
        revision: "customer-service:1:abcdef123456",
        data: {
          items: allItems.slice(offset, offset + limit),
          total: allItems.length,
          truncated: offset + limit < allItems.length,
        },
      };
    }) as CustomerServiceConsumerReader["read"],
  };
}

test("全局搜索校验关键词、分组和严格分页上限", () => {
  assert.throws(() => normalizeGlobalSearchRequest(new URLSearchParams("q=一")), GlobalSearchRequestError);
  assert.throws(() => normalizeGlobalSearchRequest(new URLSearchParams("q=净水机&group=sqlite_master")), /允许清单/);
  assert.throws(() => normalizeGlobalSearchRequest(new URLSearchParams("q=净水机&group=")), /允许清单/);
  assert.throws(() => normalizeGlobalSearchRequest(new URLSearchParams("q=净水机&limit=9")), /1 到 8/);
  assert.throws(() => normalizeGlobalSearchRequest(new URLSearchParams("q=净水机&totalLimit=51")), /1 到 50/);
  const parsed = normalizeGlobalSearchRequest(new URLSearchParams("q= 净水机 &group=products&page=2&limit=3&totalLimit=9"));
  assert.deepEqual(parsed, { query: "净水机", group: "products", page: 2, groupLimit: 3, totalLimit: 9 });
  assert.equal(normalizeGlobalSearchRequest(new URLSearchParams("q=净水机&pageSize=3")).groupLimit, 3);

  for (const query of [
    "q=净水机&page=1e2",
    "q=净水机&page=1.5",
    "q=净水机&page=%2B1",
    "q=净水机&page=%201",
    "q=净水机&page=01",
    "q=净水机&pageSize=1e2",
    "q=净水机&pageSize=1.5",
    "q=净水机&limit=1e2",
    "q=净水机&limit=1.5",
  ]) {
    assert.throws(() => normalizeGlobalSearchRequest(new URLSearchParams(query)), /十进制正整数/);
  }
  for (const query of [
    "q=净水机&q=净水器",
    "q=净水机&page=1&page=2",
    "q=净水机&pageSize=2&pageSize=3",
    "q=净水机&limit=2&limit=3",
    "q=净水机&group=products&group=orders",
  ]) {
    assert.throws(() => normalizeGlobalSearchRequest(new URLSearchParams(query)), /不能重复/);
  }
  assert.throws(
    () => normalizeGlobalSearchRequest(new URLSearchParams("q=净水机&pageSize=2&limit=2")),
    /不能同时提供/,
  );
  assert.throws(() => normalizeGlobalSearchRequest(new URLSearchParams("q=净水机&unknown=1")), /不支持的查询参数/);
});

test("每个搜索分组都有精确 module+view target 且不伪造实体", () => {
  assert.deepEqual(globalSearchDefaultTargets, {
    products: { module: "product", view: "overview" },
    orders: { module: "sales", view: "overview" },
    jd_products: { module: "shop", view: "products" },
    inventory: { module: "inventory", view: "overview" },
    inventory_age: { module: "inventory", view: "age" },
    combos: { module: "product", view: "overview" },
    replenishment: { module: "inventory", view: "plan" },
    market_skus: { module: "market", view: "ranking" },
    market_annotations: { module: "market", view: "settings" },
    customer_service: { module: "customer_service", view: "conversations" },
    finance: { module: "sales", view: "finance" },
    targets: { module: "sales", view: "targets" },
    workflow: { module: "workflow", view: "plan" },
    imports: { module: "import", view: "history" },
  });
  assert.deepEqual(Object.keys(globalSearchDefaultTargets), [...globalSearchGroupKeys]);
  for (const group of globalSearchGroupKeys) {
    const target = getGlobalSearchNavigationTarget(group);
    assert.equal(isGlobalSearchNavigationTargetForGroup(group, target), true);
    assert.equal(Object.hasOwn(target, "entity"), false);
  }
  assert.deepEqual(getGlobalSearchNavigationTarget("workflow", "inspection"), { module: "workflow", view: "inspection" });
  assert.deepEqual(getGlobalSearchNavigationTarget("workflow", "review"), { module: "workflow", view: "reviews" });
  assert.deepEqual(getGlobalSearchNavigationTarget("workflow", "launch"), { module: "workflow", view: "launch" });
  assert.deepEqual(getGlobalSearchNavigationTarget("workflow", "unknown"), { module: "workflow", view: "plan" });
});

test("搜索 API 对受控输入返回400，对未知异常固定脱敏且全部no-store", async () => {
  const invalid = globalSearchErrorResponse(new GlobalSearchRequestError("page 必须为十进制正整数。"));
  assert.equal(invalid.status, 400);
  assert.equal(invalid.headers.get("cache-control"), "no-store");
  assert.deepEqual(await invalid.json(), {
    error: "page 必须为十进制正整数。",
    code: "invalid_request",
  });

  const unknown = globalSearchErrorResponse(new Error("SQLITE_SECRET_INTERNAL_DETAIL"));
  assert.equal(unknown.status, 500);
  assert.equal(unknown.headers.get("cache-control"), "no-store");
  const body = await unknown.json() as { error: string; code: string };
  assert.deepEqual(body, { error: "搜索系统数据失败", code: "internal_error" });
  assert.doesNotMatch(JSON.stringify(body), /SQLITE_SECRET_INTERNAL_DETAIL/);
});

test("API、分组 UI 和 AI 注册入口复用同一搜索核心", async () => {
  const [route, page, dialog, tool, guide] = await Promise.all([
    readFile(new URL("../app/api/search/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/global-search-dialog.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/search/ai-tool.ts", import.meta.url), "utf8"),
    readFile(new URL("../docs/GLOBAL_SEARCH.md", import.meta.url), "utf8"),
  ]);
  assert.match(route, /searchAllBusinessData/);
  assert.match(route, /requireAppPrincipal/);
  assert.match(route, /globalSearchErrorResponse/);
  assert.doesNotMatch(route, /error instanceof Error \? error\.message/);
  assert.match(page, /const GlobalSearchDialog = lazy/);
  assert.match(dialog, /result\.groups/);
  assert.doesNotMatch(page, /className="global-search"/);
  assert.match(page, /event\.metaKey \|\| event\.ctrlKey/);
  assert.doesNotMatch(tool, /ToolDefinition/);
  assert.match(tool, /searchSystemDataForAi/);
  assert.match(guide, /字段白名单/);
  assert.match(guide, /客服会话/);
  assert.match(guide, /导入批次/);
});

test("every durable schema table is explicitly searchable, projected, or security-excluded", async () => {
  const schemaSource = await readFile(new URL("../db/schema.ts", import.meta.url), "utf8");
  const schemaTables = [...schemaSource.matchAll(/sqliteTable\(\s*"([^"]+)"/g)].map((match) => match[1]).sort();
  const classified = [
    ...GLOBAL_SEARCH_SCHEMA_TABLE_AUDIT.searchable,
    ...GLOBAL_SEARCH_SCHEMA_TABLE_AUDIT.coveredByProjection,
    ...GLOBAL_SEARCH_SCHEMA_TABLE_AUDIT.excludedSensitiveOrInternal,
  ].sort();
  assert.deepEqual(classified, schemaTables);
  assert.equal(new Set(classified).size, classified.length);
  for (const sensitive of ["ai_models", "ai_tool_audit_logs", "market_annotation_local_agents"]) {
    assert.equal(GLOBAL_SEARCH_SCHEMA_TABLE_AUDIT.excludedSensitiveOrInternal.includes(sensitive as never), true);
    assert.equal(GLOBAL_SEARCH_SCHEMA_TABLE_AUDIT.searchable.includes(sensitive as never), false);
  }
});
