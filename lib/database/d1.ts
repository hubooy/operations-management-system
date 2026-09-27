import { env } from "cloudflare:workers";

/**
 * The shared Cloudflare D1 binding used by domains that have not migrated to
 * PostgreSQL yet. Domain modules should depend on this neutral boundary rather
 * than importing another domain's database module.
 */
export type D1Database = NonNullable<typeof env.DB>;

export function getD1Database(): D1Database {
  if (!env.DB) {
    throw new Error(
      "Cloudflare D1 binding `DB` is unavailable. Configure `.openai/hosting.json` with `\"d1\": \"DB\"`.",
    );
  }

  return env.DB;
}
