import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import path from "node:path";

import type { AppPrincipal } from "../lib/auth/authorization";
import { getFinanceAnalysis, type FinanceAnalysisOptions } from "../lib/finance/analysis";
import { requestDjangoFinanceService } from "../lib/django/finance-service";

type Arguments = {
  source: string;
  readerUrl: string;
  writerUrl: string;
};

function parseArguments(argv: string[]): Arguments {
  const values = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || !value || value.startsWith("--")) {
      throw new Error("参数必须使用 --source/--reader-url/--writer-url <value>。 ");
    }
    if (values.has(key)) throw new Error(`参数 ${key} 重复。`);
    values.set(key, value);
  }
  const allowed = new Set(["--source", "--reader-url", "--writer-url"]);
  if ([...values.keys()].some((key) => !allowed.has(key))) throw new Error("包含未知参数。");
  const source = path.resolve(values.get("--source") ?? "");
  const readerUrl = values.get("--reader-url") ?? "";
  const writerUrl = values.get("--writer-url") ?? "";
  if (!source || !existsSync(source) || path.extname(source).toLowerCase() !== ".sqlite") {
    throw new Error("--source 必须指向存在的财务演练 .sqlite 副本。");
  }
  if (!readerUrl || !writerUrl) throw new Error("必须提供独立 reader/writer URL。");
  return { source, readerUrl, writerUrl };
}

function sqliteAdapter(sqlite: DatabaseSync) {
  return {
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...next: unknown[]) {
          values = next as SQLInputValue[];
          return this;
        },
        async first<T>(column?: string) {
          const row = sqlite.prepare(sql).get(...values) as Record<string, unknown> | undefined;
          return (column ? row?.[column] ?? null : row ?? null) as T | null;
        },
        async all<T>() {
          return { results: sqlite.prepare(sql).all(...values) as T[] };
        },
        async run(): Promise<never> {
          throw new Error("财务对比源只允许读取。");
        },
      };
    },
    async batch(): Promise<never> {
      throw new Error("财务对比源只允许读取。");
    },
  };
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(Object.keys(record).sort().map((key) => [key, canonical(record[key])]));
  }
  return value;
}

function digest(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(canonical(value))).digest("hex");
}

function firstDifference(left: unknown, right: unknown, current = "$"): string | null {
  // JSON has one numeric zero, while Object.is distinguishes 0 and -0.
  if (left === right) return null;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right)) return current;
    if (left.length !== right.length) return `${current}.length`;
    for (let index = 0; index < left.length; index += 1) {
      const difference = firstDifference(left[index], right[index], `${current}[${index}]`);
      if (difference) return difference;
    }
    return null;
  }
  if (left && right && typeof left === "object" && typeof right === "object") {
    const leftRecord = left as Record<string, unknown>;
    const rightRecord = right as Record<string, unknown>;
    const leftKeys = Object.keys(leftRecord).sort();
    const rightKeys = Object.keys(rightRecord).sort();
    if (JSON.stringify(leftKeys) !== JSON.stringify(rightKeys)) return `${current}.__keys`;
    for (const key of leftKeys) {
      const difference = firstDifference(leftRecord[key], rightRecord[key], `${current}.${key}`);
      if (difference) return difference;
    }
    return null;
  }
  return current;
}

function selectableShop(name: string): boolean {
  const compact = name.replace(/[\s　]+/g, "");
  return !/^分销[-—]/.test(compact)
    && !/^(?:[1-9]|1[0-2])月(?:项目费率)?$/.test(compact);
}

function queryFor(options: FinanceAnalysisOptions): URLSearchParams {
  const query = new URLSearchParams();
  if (options.allMonths) query.append("month", "*");
  else for (const month of options.requestedMonths ?? []) query.append("month", month);
  if (options.fallbackToLatestCompletedMonth) {
    query.append("initialMonthFallback", "latest_completed");
  }
  for (const platform of options.platformNames ?? []) query.append("platform", platform);
  for (const shop of options.shopKeys ?? []) query.append("shop", shop);
  return query;
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const internalSecret = process.env.TERUISI_DJANGO_INTERNAL_SECRET ?? "";
  if (Buffer.byteLength(internalSecret, "utf8") < 32) {
    throw new Error("TERUISI_DJANGO_INTERNAL_SECRET 未配置或不足 32 字节。");
  }
  const sqlite = new DatabaseSync(args.source, { readOnly: true });
  try {
    const authority = sqlite.prepare(
      "SELECT owner FROM finance_write_authority WHERE id=1",
    ).get() as { owner?: string } | undefined;
    if (!authority || !["d1", "pending", "postgresql"].includes(String(authority.owner))) {
      throw new Error("财务对比副本缺少有效 authority。");
    }
    const months = sqlite.prepare(
      "SELECT month FROM finance_months WHERE status='completed' ORDER BY month",
    ).all().map((row) => String(row.month));
    if (months.length === 0) throw new Error("财务对比副本没有完成月份。");
    const latest = months.at(-1)!;
    const platforms = sqlite.prepare(
      "SELECT DISTINCT COALESCE(NULLIF(group_name,''),'未分组') AS platform "
      + "FROM finance_lines WHERE month=? AND scope_type='shop' ORDER BY platform",
    ).all(latest).map((row) => String(row.platform));
    const shops = sqlite.prepare(
      "SELECT DISTINCT COALESCE(NULLIF(group_name,''),'未分组') AS platform, scope_name AS name "
      + "FROM finance_lines WHERE month=? AND scope_type='shop' AND scope_name<>'' "
      + "ORDER BY platform,name LIMIT 100",
    ).all(latest)
      .filter((row) => selectableShop(String(row.name)))
      .slice(0, 8)
      .map((row) => JSON.stringify([String(row.platform), String(row.name)]));
    const scenarios: Array<{ label: string; options: FinanceAnalysisOptions }> = [
      { label: "default", options: {} },
      { label: "all", options: { allMonths: true } },
      { label: "last-two", options: { requestedMonths: months.slice(-2) } },
      ...months.map((month) => ({ label: `month:${month}`, options: { requestedMonths: [month] } })),
      ...platforms.map((platform) => ({
        label: `platform:${digest(platform).slice(0, 12)}`,
        options: { requestedMonths: [latest], platformNames: [platform] },
      })),
      ...shops.map((shop) => ({
        label: `shop:${digest(shop).slice(0, 12)}`,
        options: { requestedMonths: [latest], shopKeys: [shop] },
      })),
    ];
    const principal: AppPrincipal = {
      email: "finance-parity@example.invalid",
      displayName: "Finance parity",
      role: "admin",
      scope: null,
    };
    const database = sqliteAdapter(sqlite) as never;
    let sequence = 0;
    const mismatches: Array<Record<string, unknown>> = [];
    for (const scenario of scenarios) {
      try {
        const [legacy, candidate] = await Promise.all([
          getFinanceAnalysis(database, scenario.options),
          requestDjangoFinanceService<Record<string, unknown>>(
            principal,
            {
              method: "GET",
              path: "/api/finance/analysis",
              query: queryFor(scenario.options),
              service: "reader",
            },
            {
              config: {
                readerBaseUrl: args.readerUrl,
                writerBaseUrl: args.writerUrl,
                internalSecret,
                timeoutMs: 30_000,
                maxRequestBytes: 16 * 1024 * 1024,
                maxResponseBytes: 16 * 1024 * 1024,
              },
              requestId: () => `finance-parity-${++sequence}`,
            },
          ).then((result) => result.data),
        ]);
        const legacyDigest = digest(legacy);
        const djangoDigest = digest(candidate);
        if (legacyDigest !== djangoDigest) {
          mismatches.push({
            scenario: scenario.label,
            path: firstDifference(legacy, candidate),
            legacySha256: legacyDigest,
            djangoSha256: djangoDigest,
          });
        }
      } catch (error) {
        mismatches.push({
          scenario: scenario.label,
          errorType: error instanceof Error ? error.name : "unknown",
        });
      }
    }
    const result = {
      status: mismatches.length ? "mismatch" : "matched",
      scenarios: scenarios.length,
      months: months.length,
      platforms: platforms.length,
      shops: shops.length,
      mismatches,
    };
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (mismatches.length) process.exitCode = 2;
  } finally {
    sqlite.close();
  }
}

main().catch((error: unknown) => {
  process.stderr.write(`${JSON.stringify({
    status: "failed",
    errorType: error instanceof Error ? error.name : "unknown",
  })}\n`);
  process.exitCode = 1;
});
