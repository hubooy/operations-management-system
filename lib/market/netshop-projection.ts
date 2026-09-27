import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoNetshopConsumerReader,
  type MarketNetshopProjectionRow,
  type NetshopConsumerReader,
  type NetshopConsumerResponseMap,
} from "@/lib/django/netshop-consumer-reader";
import { PublicApiError } from "@/lib/http/api-error";
import {
  marketNetshopProjectionStatements,
  type MarketSchemaDatabase,
} from "@/lib/market/schema-core";
import {
  ensureMarketMonthlySummaryInvalidationTriggers,
  type MonthlySummaryCacheDatabase,
} from "@/lib/market/monthly-summary-cache";

const PAGE_SIZE = 1_000;
const INSERT_BATCH_SIZE = 50;
const MAX_PROJECTION_ROWS = 300_000;
const REVISION_RE = /^\d+:[a-f0-9]{12}$/;
const HASHED_KEY_RE = /^(?:identity|brand):[a-f0-9]{64}$/;
const METRIC_KEY_RE = /^metric:[^\u0000-\u001f\u007f]{1,1024}$/;

type ProjectionControl = {
  active_revision: string;
  active_total: number;
};

function unavailable(message = "Django 网店市场投影暂时不可用，请稍后重试。") {
  return new PublicApiError(503, "service_unavailable", message);
}

function validText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.length <= maximum
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(value);
}

function validProjectionRow(value: unknown): value is MarketNetshopProjectionRow {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  const kind = row.kind;
  const keyValid = typeof row.projectionKey === "string"
    && (kind === "metric" ? METRIC_KEY_RE.test(row.projectionKey) : HASHED_KEY_RE.test(row.projectionKey));
  return (kind === "metric" || kind === "identity" || kind === "brand")
    && keyValid
    && validText(row.source, 64)
    && validText(row.dataset, 64)
    && validText(row.platform, 100)
    && validText(row.shopName, 100)
    && validText(row.businessDate, 10)
    && (row.businessDate === "" || /^\d{4}-\d{2}-\d{2}$/.test(row.businessDate))
    && validText(row.skuId, 512)
    && validText(row.spuId, 512)
    && validText(row.productCode, 512)
    && Number.isSafeInteger(row.transactionAmountCents)
    && validText(row.brand, 120);
}

function validatePage(
  revision: string,
  data: NetshopConsumerResponseMap["market_projection_page"],
  offset: number,
) {
  if (!REVISION_RE.test(revision)
    || !data || !Array.isArray(data.rows)
    || !Number.isSafeInteger(data.total) || data.total < 0 || data.total > MAX_PROJECTION_ROWS
    || data.rows.length !== Math.min(PAGE_SIZE, Math.max(0, data.total - offset))
    || typeof data.truncated !== "boolean"
    || data.truncated !== (offset + data.rows.length < data.total)
    || !data.rows.every(validProjectionRow)) {
    throw unavailable("Django 网店市场投影响应不符合有界契约。");
  }
}

async function page(
  reader: NetshopConsumerReader,
  principal: AppPrincipal,
  offset: number,
  expectedRevision: string | null,
  signal?: AbortSignal,
) {
  const result = await reader.read(principal, {
    operation: "market_projection_page",
    offset,
    limit: PAGE_SIZE,
    expectedRevision,
  }, { signal });
  validatePage(result.revision, result.data, offset);
  if (expectedRevision !== null && result.revision !== expectedRevision) throw unavailable();
  return result;
}

async function insertRows(
  db: MarketSchemaDatabase,
  revision: string,
  rows: readonly MarketNetshopProjectionRow[],
) {
  for (let offset = 0; offset < rows.length; offset += INSERT_BATCH_SIZE) {
    const chunk = rows.slice(offset, offset + INSERT_BATCH_SIZE);
    await db.batch(chunk.map((row) => db.prepare(`INSERT INTO market_netshop_projection (
        projection_revision,projection_key,kind,source,dataset,platform,shop_name,
        business_date,sku_id,spu_id,product_code,transaction_amount_cents,brand
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(projection_revision,projection_key) DO UPDATE SET
        kind=excluded.kind,source=excluded.source,dataset=excluded.dataset,
        platform=excluded.platform,shop_name=excluded.shop_name,
        business_date=excluded.business_date,sku_id=excluded.sku_id,
        spu_id=excluded.spu_id,product_code=excluded.product_code,
        transaction_amount_cents=excluded.transaction_amount_cents,brand=excluded.brand`)
      .bind(
        revision, row.projectionKey, row.kind, row.source, row.dataset, row.platform,
        row.shopName, row.businessDate, row.skuId, row.spuId, row.productCode,
        row.transactionAmountCents, row.brand,
      )));
  }
}

async function syncProjection(
  db: MarketSchemaDatabase,
  principal: AppPrincipal,
  reader: NetshopConsumerReader,
  signal?: AbortSignal,
) {
  for (const statement of marketNetshopProjectionStatements) await db.prepare(statement).run();
  // MarketSchemaDatabase intentionally accepts the smaller statement facade
  // used by schema-repair tests. Runtime D1 statements return themselves from
  // bind(), which is the stronger cache contract required here.
  await ensureMarketMonthlySummaryInvalidationTriggers(db as unknown as MonthlySummaryCacheDatabase);
  const first = await page(reader, principal, 0, null, signal);
  const revision = first.revision;
  const total = first.data.total;
  const control = await db.prepare(`SELECT active_revision,active_total
    FROM market_netshop_projection_control WHERE id=1`).first<ProjectionControl>();
  if (control?.active_revision === revision && Number(control.active_total) === total) return revision;

  const ownerToken = crypto.randomUUID();
  const claim = await db.prepare(`UPDATE market_netshop_projection_control
    SET syncing_revision=?,owner_token=?,lease_expires_at=datetime('now','+5 minutes'),updated_at=CURRENT_TIMESTAMP
    WHERE id=1 AND (active_revision<>? OR active_total<>?)
      AND (owner_token='' OR lease_expires_at IS NULL OR lease_expires_at<=CURRENT_TIMESTAMP)`)
    .bind(revision, ownerToken, revision, total).run() as { meta?: { changes?: number } };
  if (Number(claim.meta?.changes ?? 0) !== 1) {
    const current = await db.prepare(`SELECT active_revision,active_total
      FROM market_netshop_projection_control WHERE id=1`).first<ProjectionControl>();
    if (current?.active_revision === revision && Number(current.active_total) === total) return revision;
    throw unavailable("网店市场投影正在由另一请求更新，请稍后重试。");
  }

  try {
    await db.prepare("DELETE FROM market_netshop_projection WHERE projection_revision=?")
      .bind(revision).run();
    let offset = 0;
    let current = first;
    while (offset < total) {
      if (offset > 0) current = await page(reader, principal, offset, revision, signal);
      if (current.revision !== revision || current.data.total !== total) throw unavailable();
      await insertRows(db, revision, current.data.rows);
      offset += current.data.rows.length;
      if (!current.data.rows.length && offset < total) throw unavailable();
      const renewed = await db.prepare(`UPDATE market_netshop_projection_control
        SET lease_expires_at=datetime('now','+5 minutes'),updated_at=CURRENT_TIMESTAMP
        WHERE id=1 AND owner_token=? AND syncing_revision=?`)
        .bind(ownerToken, revision).run() as { meta?: { changes?: number } };
      if (Number(renewed.meta?.changes ?? 0) !== 1) throw unavailable("网店市场投影同步所有权已失效。");
    }
    const staged = await db.prepare(`SELECT COUNT(*) count FROM market_netshop_projection
      WHERE projection_revision=?`).bind(revision).first<{ count: number }>();
    if (Number(staged?.count ?? -1) !== total) throw unavailable("网店市场投影落库行数回查不一致。");
    const activation = await db.batch([
      db.prepare(`UPDATE market_netshop_projection_control SET
        active_revision=?,active_total=?,syncing_revision='',owner_token='',lease_expires_at=NULL,
        updated_at=CURRENT_TIMESTAMP
        WHERE id=1 AND owner_token=? AND syncing_revision=?`)
        .bind(revision, total, ownerToken, revision),
      // The effective-metrics trigger may not have been installed by an admin
      // surface yet; this idempotent delete closes that first-activation gap.
      db.prepare(`DELETE FROM market_effective_metrics_cache_state WHERE id=1
        AND EXISTS (
          SELECT 1 FROM market_netshop_projection_control
          WHERE id=1 AND active_revision=? AND active_total=? AND owner_token=''
        )`).bind(revision, total),
      db.prepare(`UPDATE market_system_kpi_cache_state
        SET source_revision=source_revision+1,updated_at=CURRENT_TIMESTAMP
        WHERE id=1 AND EXISTS (
          SELECT 1 FROM market_netshop_projection_control
          WHERE id=1 AND active_revision=? AND active_total=? AND owner_token=''
        )`).bind(revision, total),
      db.prepare(`DELETE FROM market_overview_response_cache WHERE EXISTS (
        SELECT 1 FROM market_netshop_projection_control
        WHERE id=1 AND active_revision=? AND active_total=? AND owner_token=''
      )`).bind(revision, total),
    ]) as Array<{ meta?: { changes?: number } }>;
    if (Number(activation[0]?.meta?.changes ?? 0) !== 1) {
      // Miniflare/D1 may commit the guarded update while omitting the first
      // batch item's change count.  Treat the durable control row as the
      // authority before reporting a lost owner; otherwise every successful
      // first activation can surface a false 503 to the market page.
      const activated = await db.prepare(`SELECT active_revision,active_total
        FROM market_netshop_projection_control WHERE id=1`).first<ProjectionControl>();
      if (activated?.active_revision !== revision || Number(activated.active_total) !== total) {
        throw unavailable("网店市场投影激活所有权已失效。");
      }
    }
    await db.prepare("DELETE FROM market_netshop_projection WHERE projection_revision<>?")
      .bind(revision).run().catch(() => undefined);
    return revision;
  } catch (error) {
    await db.prepare(`UPDATE market_netshop_projection_control
      SET syncing_revision='',owner_token='',lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP
      WHERE id=1 AND owner_token=?`).bind(ownerToken).run().catch(() => undefined);
    throw error;
  }
}

const syncByDatabase = new WeakMap<object, Promise<string>>();

export function ensureMarketNetshopProjection(
  db: MarketSchemaDatabase,
  principal: AppPrincipal,
  options: { reader?: NetshopConsumerReader; signal?: AbortSignal } = {},
) {
  const key = db as object;
  const current = syncByDatabase.get(key);
  if (current) return current;
  const running = syncProjection(
    db,
    principal,
    options.reader ?? createDjangoNetshopConsumerReader(),
    options.signal,
  ).finally(() => syncByDatabase.delete(key));
  syncByDatabase.set(key, running);
  return running;
}
