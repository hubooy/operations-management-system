import type { D1Database } from "@/lib/database/d1";

const taskSchemaStatements = [
  `CREATE TABLE IF NOT EXISTS workflow_tasks (
    id TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, work_content TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '工作计划', owner TEXT NOT NULL DEFAULT '', shop_name TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, priority TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE INDEX IF NOT EXISTS workflow_tasks_status_created_idx ON workflow_tasks (status, created_at DESC)`,
  `CREATE TABLE IF NOT EXISTS workflow_task_bootstrap (
    key TEXT PRIMARY KEY NOT NULL, seeded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE TABLE IF NOT EXISTS workflow_task_states (
    task_id TEXT PRIMARY KEY NOT NULL, version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    mutation_token TEXT NOT NULL DEFAULT '', deleted_at TEXT, deleted_by TEXT,
    FOREIGN KEY (task_id) REFERENCES workflow_tasks(id) ON DELETE CASCADE)`,
  `CREATE INDEX IF NOT EXISTS workflow_task_states_deleted_idx ON workflow_task_states (deleted_at, task_id)`,
] as const;

const collaborationSchemaStatements = [
  `CREATE TABLE IF NOT EXISTS workflow_task_comments (
    id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL, content TEXT NOT NULL,
    created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES workflow_tasks(id) ON DELETE CASCADE)`,
  `CREATE INDEX IF NOT EXISTS workflow_task_comments_task_created_idx ON workflow_task_comments (task_id, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS workflow_task_activity_logs (
    id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL, action TEXT NOT NULL,
    summary TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', actor_email TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES workflow_tasks(id) ON DELETE CASCADE)`,
  `CREATE INDEX IF NOT EXISTS workflow_task_activity_task_created_idx ON workflow_task_activity_logs (task_id, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS workflow_task_reminders (
    id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL, remind_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','dismissed','sent')),
    created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES workflow_tasks(id) ON DELETE CASCADE)`,
  `CREATE INDEX IF NOT EXISTS workflow_task_reminders_task_status_time_idx ON workflow_task_reminders (task_id, status, remind_at)`,
  `CREATE TABLE IF NOT EXISTS workflow_task_templates (
    id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
    work_content TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '工作计划', owner TEXT NOT NULL DEFAULT '', shop_name TEXT NOT NULL DEFAULT '',
    start_offset_days INTEGER NOT NULL DEFAULT 0, due_offset_days INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high','normal','low')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)), created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE INDEX IF NOT EXISTS workflow_task_templates_active_updated_idx ON workflow_task_templates (active, updated_at, id)`,
  `CREATE TABLE IF NOT EXISTS workflow_task_template_states (
    template_id TEXT PRIMARY KEY NOT NULL, version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    mutation_token TEXT NOT NULL DEFAULT '', FOREIGN KEY (template_id) REFERENCES workflow_task_templates(id) ON DELETE CASCADE)`,
  `CREATE TABLE IF NOT EXISTS workflow_task_entity_links (
    id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('shop','product','campaign','order','report','url')),
    entity_id TEXT NOT NULL, label TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (task_id) REFERENCES workflow_tasks(id) ON DELETE CASCADE)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS workflow_task_entity_links_task_entity_uq ON workflow_task_entity_links (task_id, entity_type, entity_id)`,
  `CREATE INDEX IF NOT EXISTS workflow_task_entity_links_task_created_idx ON workflow_task_entity_links (task_id, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS workflow_task_attachments (
    id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL, file_name TEXT NOT NULL, mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, object_key TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (task_id) REFERENCES workflow_tasks(id) ON DELETE CASCADE)`,
  `CREATE INDEX IF NOT EXISTS workflow_task_attachments_task_created_idx ON workflow_task_attachments (task_id, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS workflow_attachment_cleanup_queue (
    object_key TEXT PRIMARY KEY NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
    enqueued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE INDEX IF NOT EXISTS workflow_attachment_cleanup_queue_updated_idx ON workflow_attachment_cleanup_queue (updated_at, object_key)`,
] as const;

const taskSchemaReadyByDatabase = new WeakMap<object, Promise<void>>();
const collaborationSchemaReadyByDatabase = new WeakMap<object, Promise<void>>();

async function workflowDatabase(db?: D1Database) {
  if (db) return db;
  const { getD1Database } = await import("@/lib/database/d1");
  return getD1Database();
}

export async function ensureWorkflowTaskSchema(db?: D1Database) {
  const database = await workflowDatabase(db);
  const key = database as unknown as object;
  const existing = taskSchemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = database.batch(taskSchemaStatements.map((statement) => database.prepare(statement)))
    .then(() => database.prepare("INSERT OR IGNORE INTO workflow_task_states (task_id) SELECT id FROM workflow_tasks").run())
    .then(() => undefined)
    .catch((error: unknown) => {
      taskSchemaReadyByDatabase.delete(key);
      throw error;
    });
  taskSchemaReadyByDatabase.set(key, setup);
  return setup;
}

export async function ensureWorkflowCollaborationSchema(db?: D1Database) {
  const database = await workflowDatabase(db);
  const key = database as unknown as object;
  const existing = collaborationSchemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = ensureWorkflowTaskSchema(database)
    .then(() => database.batch(collaborationSchemaStatements.map((statement) => database.prepare(statement))))
    .then(() => database.prepare(`INSERT OR IGNORE INTO workflow_task_template_states (template_id)
      SELECT id FROM workflow_task_templates`).run())
    .then(() => undefined)
    .catch((error: unknown) => {
      collaborationSchemaReadyByDatabase.delete(key);
      throw error;
    });
  collaborationSchemaReadyByDatabase.set(key, setup);
  return setup;
}
