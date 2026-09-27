import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoProductsService,
  PRODUCTS_INVENTORY_PROJECTION_PATH,
  type DjangoProductsServiceOptions,
} from "@/lib/django/products-service";
import {
  createDjangoInventoryConsumerReader,
  type InventoryConsumerReader,
} from "@/lib/django/inventory-consumer-reader";
import { PublicApiError } from "@/lib/http/api-error";

const MAX_ROWS = 20_000;
const PAGE_SIZE = 1_000;

type ProjectionRow = {
  productCode: string;
  brand: string;
  availableQuantity: number;
  knownStockValueCents: number;
  pricedAvailableQuantity: number;
};

type ProductsProjectionWriter = ReturnType<typeof createDjangoProductsService>;

function unavailable(message: string) {
  return new PublicApiError(503, "service_unavailable", message);
}

function checkedInteger(value: unknown, label: string) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0) {
    throw unavailable(`库存投影 ${label} 超出安全数值范围`);
  }
  return number;
}

async function sha256(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function readProjectionRows(
  principal: AppPrincipal,
  reader: InventoryConsumerReader,
  signal?: AbortSignal,
) {
  const rows: ProjectionRow[] = [];
  let expectedRevision = "";
  let batchId: string | null = null;
  let snapshotDate: string | null = null;
  let total = -1;
  for (let offset = 0; total < 0 || offset < total; offset += PAGE_SIZE) {
    const result = await reader.read(principal, {
      operation: "stock_projection",
      offset,
      limit: PAGE_SIZE,
    }, { signal });
    if (!expectedRevision) expectedRevision = result.revision;
    if (result.revision !== expectedRevision
      || (batchId !== null && result.data.batchId !== batchId)
      || (snapshotDate !== null && result.data.snapshotDate !== snapshotDate)
      || (total >= 0 && result.data.total !== total)
      || result.data.offset !== offset) {
      throw unavailable("库存投影分页读取期间版本发生变化");
    }
    batchId = result.data.batchId;
    snapshotDate = result.data.snapshotDate;
    total = checkedInteger(result.data.total, "规格总数");
    if (total > MAX_ROWS || result.data.rows.length > PAGE_SIZE
      || result.data.rows.length !== Math.min(PAGE_SIZE, Math.max(0, total - offset))) {
      throw unavailable("库存投影规格数量超过商品域安全上限");
    }
    for (const row of result.data.rows) {
      const availableQuantity = checkedInteger(row.availableQuantity, "可用数量");
      const pricedAvailableQuantity = checkedInteger(row.pricedAvailableQuantity, "已知成本数量");
      if (!row.productCode.trim() || row.productCode.length > 512
        || row.brand.length > 500 || pricedAvailableQuantity > availableQuantity) {
        throw unavailable("库存投影行结构无效");
      }
      rows.push({
        productCode: row.productCode.trim(),
        brand: row.brand.trim(),
        availableQuantity,
        knownStockValueCents: checkedInteger(row.knownStockValueCents, "已知库存金额"),
        pricedAvailableQuantity,
      });
    }
    if (total === 0) break;
  }
  if (!batchId || !snapshotDate || rows.length !== total) {
    throw unavailable("没有可同步到商品域的已完成库存快照");
  }
  return { batchId, snapshotDate, rows };
}

export async function syncLatestInventoryProjection(
  principal: AppPrincipal,
  options: Omit<DjangoProductsServiceOptions, "config"> & {
    inventoryReader?: InventoryConsumerReader;
    writer?: ProductsProjectionWriter;
  } = {},
) {
  const {
    inventoryReader = createDjangoInventoryConsumerReader(),
    writer = createDjangoProductsService(),
    ...requestOptions
  } = options;
  const projection = await readProjectionRows(principal, inventoryReader, requestOptions.signal);
  const { rows, batchId, snapshotDate } = projection;
  const projectionRevision = await sha256(
    `product-inventory-projection-v1\n${batchId}\n${snapshotDate}\n${JSON.stringify(rows)}`,
  );
  const ownerToken = await sha256(
    `product-inventory-projection-owner-v1\n${principal.email.trim().toLowerCase()}\n${projectionRevision}`,
  );
  const begin = await writer.requestJson<{
    status: "active" | "syncing";
    ownerToken?: string;
    control: { syncingOffset: number };
  }>(principal, {
    method: "POST",
    path: PRODUCTS_INVENTORY_PROJECTION_PATH,
    service: "writer",
    payload: {
      action: "begin_sync",
      projectionRevision,
      sourceBatchId: batchId,
      snapshotDate,
      totalRows: rows.length,
      ownerToken,
    },
  }, requestOptions);
  if (begin.data.status === "active") {
    return { status: "active" as const, projectionRevision, rowCount: rows.length, sourceBatchId: batchId };
  }
  let offset = Number(begin.data.control.syncingOffset ?? 0);
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > rows.length) {
    throw unavailable("商品域返回的库存投影进度无效");
  }
  while (offset < rows.length) {
    const page = rows.slice(offset, offset + PAGE_SIZE);
    const staged = await writer.requestJson<{ control: { syncingOffset: number } }>(principal, {
      method: "POST",
      path: PRODUCTS_INVENTORY_PROJECTION_PATH,
      service: "writer",
      payload: {
        action: "stage_page",
        projectionRevision,
        ownerToken,
        offset,
        rows: page,
      },
    }, requestOptions);
    const next = Number(staged.data.control.syncingOffset);
    if (!Number.isSafeInteger(next) || next !== offset + page.length) {
      throw unavailable("商品域库存投影分页回读不一致");
    }
    offset = next;
  }
  const activated = await writer.requestJson<{ status: string; control: { activeRevision: string; activeTotal: number } }>(
    principal,
    {
      method: "POST",
      path: PRODUCTS_INVENTORY_PROJECTION_PATH,
      service: "writer",
      payload: { action: "activate_sync", projectionRevision, ownerToken },
    },
    requestOptions,
  );
  if (activated.data.status !== "active"
    || activated.data.control.activeRevision !== projectionRevision
    || activated.data.control.activeTotal !== rows.length) {
    throw unavailable("商品域库存投影激活回查不一致");
  }
  return { status: "active" as const, projectionRevision, rowCount: rows.length, sourceBatchId: batchId };
}
