import {
  getD1Database,
  type D1Database,
} from "@/lib/database/d1";

export type OperatingSettings = {
  targetDays: number;
  criticalDays: number;
  slowDays: number;
  stagnantDays: number;
  autoReplenishment: boolean;
  inventoryAlert: boolean;
  allowNegativeInventory: boolean;
};

export type StoredOperatingSettings = OperatingSettings & {
  updatedAt: string | null;
  updatedBy: string | null;
};

const SETTINGS_KEY = "operating";
const defaults: OperatingSettings = {
  targetDays: 30,
  criticalDays: 7,
  slowDays: 45,
  stagnantDays: 90,
  autoReplenishment: true,
  inventoryAlert: true,
  allowNegativeInventory: false,
};

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

function numberInRange(value: unknown, fallback: number, min: number, max: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(max, Math.max(min, Math.round(parsed))) : fallback;
}

function normalize(input: Partial<OperatingSettings> | null | undefined): OperatingSettings {
  return {
    targetDays: numberInRange(input?.targetDays, defaults.targetDays, 1, 365),
    criticalDays: numberInRange(input?.criticalDays, defaults.criticalDays, 1, 120),
    slowDays: numberInRange(input?.slowDays, defaults.slowDays, 1, 730),
    stagnantDays: numberInRange(input?.stagnantDays, defaults.stagnantDays, 1, 1_460),
    autoReplenishment: input?.autoReplenishment ?? defaults.autoReplenishment,
    inventoryAlert: input?.inventoryAlert ?? defaults.inventoryAlert,
    allowNegativeInventory: input?.allowNegativeInventory ?? defaults.allowNegativeInventory,
  };
}

export async function ensureSettingsSchema(db: D1Database = getD1Database()) {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db.batch(schemaStatements.map((statement) => db.prepare(statement)))
    .then(() => undefined)
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}

export async function readOperatingSettings(db: D1Database = getD1Database()): Promise<StoredOperatingSettings> {
  await ensureSettingsSchema(db);
  const row = await db.prepare(
    `SELECT value_json, updated_by, updated_at FROM system_settings WHERE key = ? LIMIT 1`,
  ).bind(SETTINGS_KEY).first<{ value_json: string; updated_by: string; updated_at: string }>();
  if (!row) return { ...defaults, updatedAt: null, updatedBy: null };
  try {
    return { ...normalize(JSON.parse(row.value_json) as Partial<OperatingSettings>), updatedAt: row.updated_at, updatedBy: row.updated_by || null };
  } catch {
    return { ...defaults, updatedAt: row.updated_at, updatedBy: row.updated_by || null };
  }
}

export async function saveOperatingSettings(input: Partial<OperatingSettings>, updatedBy: string, db: D1Database = getD1Database()) {
  await ensureSettingsSchema(db);
  const settings = normalize(input);
  await db.prepare(
    `INSERT INTO system_settings (key, value_json, updated_by, updated_at)
     VALUES (?, ?, ?, CURRENT_TIMESTAMP)
     ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP`,
  ).bind(SETTINGS_KEY, JSON.stringify(settings), updatedBy).run();
  return readOperatingSettings(db);
}
