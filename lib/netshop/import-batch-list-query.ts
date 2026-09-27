export type NetshopImportBatchListFilters = {
  page?: number;
  pageSize?: number;
  /** Backward-compatible alias for pageSize. */
  limit?: number;
  ids?: string[];
  sources?: string[];
  platforms?: string[];
  shops?: string[];
};

/** Normalizes bounded filters and keeps every user value as a SQL binding. */
export function buildNetshopImportBatchListQuery(input: NetshopImportBatchListFilters = {}) {
  const boundedInteger = (value: number | undefined, fallback: number, maximum: number) => (
    Number.isSafeInteger(value) ? Math.max(1, Math.min(maximum, value as number)) : fallback
  );
  const page = boundedInteger(input.page, 1, 10_000);
  const pageSize = boundedInteger(input.pageSize ?? input.limit, 20, 100);
  const bounded = (values: string[] | undefined, maximum: number) => [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))].slice(0, maximum);
  const ids = bounded(input.ids, 20);
  const sources = bounded(input.sources, 20);
  const platforms = bounded(input.platforms, 20);
  const shops = bounded(input.shops, 50);
  const where: string[] = [];
  const bindings: string[] = [];
  const list = (column: string, values: string[]) => {
    if (!values.length) return;
    where.push(`${column} IN (SELECT CAST(value AS TEXT) FROM json_each(?))`);
    bindings.push(JSON.stringify(values));
  };
  list("id", ids);
  list("source", sources);
  list("platform", platforms);
  list("shop_name", shops);
  return { page, pageSize, offset: (page - 1) * pageSize, whereSql: where.join(" AND "), bindings };
}
