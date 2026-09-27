import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  readDjangoNetshopConsumer,
  type MarketNetshopProjectionRow,
} from "@/lib/django/netshop-consumer-reader";
import {
  MARKET_COMMANDS_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { PublicApiError } from "@/lib/http/api-error";

const PAGE_SIZE = 1_000;
const MAX_PAGES_PER_RUN = 4;
const MAX_PROJECTION_ROWS = 300_000;
const REVISION_RE = /^\d+:[a-f0-9]{12}$/;

const INTERNAL_PROJECTION_PRINCIPAL: AppPrincipal = {
  email: "market-netshop-projection@teruisi.internal",
  displayName: "市场网店投影同步器",
  role: "admin",
  scope: null,
};

type ProjectionState = {
  status: "active" | "syncing";
  activeRevision: string;
  activeTotal: number;
  syncingRevision: string;
  syncingTotal: number;
  syncingOffset: number;
};

function unavailable(message = "Django 市场网店投影同步失败。") {
  return new PublicApiError(503, "service_unavailable", message);
}

function state(value: unknown): ProjectionState {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw unavailable();
  const row = value as Record<string, unknown>;
  if ((row.status !== "active" && row.status !== "syncing")
    || typeof row.activeRevision !== "string"
    || !Number.isSafeInteger(row.activeTotal) || Number(row.activeTotal) < 0
    || typeof row.syncingRevision !== "string"
    || !Number.isSafeInteger(row.syncingTotal) || Number(row.syncingTotal) < 0
    || !Number.isSafeInteger(row.syncingOffset) || Number(row.syncingOffset) < 0) {
    throw unavailable();
  }
  return row as unknown as ProjectionState;
}

async function command(commandValue: Record<string, unknown>) {
  const result = await requestDjangoMarketService<{
    ok: boolean;
    result: Record<string, unknown>;
  }>(INTERNAL_PROJECTION_PRINCIPAL, {
    path: MARKET_COMMANDS_PATH,
    service: "writer",
    payload: {
      contractVersion: "market-command-v1",
      domain: "projection",
      command: commandValue,
    },
  });
  if (result.data.ok !== true) throw unavailable();
  return state(result.data.result);
}

async function page(offset: number, expectedRevision: string | null) {
  return readDjangoNetshopConsumer(INTERNAL_PROJECTION_PRINCIPAL, {
    operation: "market_projection_page",
    offset,
    limit: PAGE_SIZE,
    expectedRevision,
  });
}

export async function runDjangoMarketNetshopProjectionSync() {
  const first = await page(0, null);
  const sourceRevision = first.revision;
  const total = first.data.total;
  if (!REVISION_RE.test(sourceRevision)
    || !Number.isSafeInteger(total) || total < 0 || total > MAX_PROJECTION_ROWS) {
    throw unavailable("Django 网店投影源版本或总数无效。");
  }
  let current = await command({ action: "begin_sync", sourceRevision, total });
  if (current.status === "active") {
    return { idle: true, status: "active", sourceRevision, total, processedCount: 0 };
  }
  if (current.syncingRevision !== sourceRevision || current.syncingTotal !== total) {
    throw unavailable();
  }
  if (total === 0) {
    current = await command({ action: "activate_sync", sourceRevision });
    return { idle: false, status: current.status, sourceRevision, total, processedCount: 0 };
  }
  let processedCount = 0;
  for (let pageIndex = 0; pageIndex < MAX_PAGES_PER_RUN && current.syncingOffset < total; pageIndex += 1) {
    const offset = current.syncingOffset;
    const source = offset === 0 ? first : await page(offset, sourceRevision);
    if (source.revision !== sourceRevision || source.data.total !== total) throw unavailable();
    const rows = source.data.rows as MarketNetshopProjectionRow[];
    current = await command({
      action: "stage_page",
      sourceRevision,
      offset,
      rows,
    });
    if (current.syncingOffset !== offset + rows.length) throw unavailable();
    processedCount += rows.length;
  }
  if (current.syncingOffset === total) {
    current = await command({ action: "activate_sync", sourceRevision });
  }
  return {
    idle: false,
    status: current.status,
    sourceRevision,
    total,
    syncingOffset: current.syncingOffset,
    processedCount,
  };
}
