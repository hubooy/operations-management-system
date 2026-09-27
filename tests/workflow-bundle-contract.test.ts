import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const workflowSourcePaths = [
  path.join(repositoryRoot, "lib", "workflow", "tasks.ts"),
  path.join(repositoryRoot, "lib", "workflow", "collaboration.ts"),
  path.join(repositoryRoot, "lib", "workflow", "schema.ts"),
];

async function listJavaScriptFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return listJavaScriptFiles(entryPath);
    return entry.isFile() && entry.name.endsWith(".js") ? [entryPath] : [];
  }));
  return nested.flat();
}

test("workflow task mutations use cycle-free static dependencies", async () => {
  const [tasks, collaboration, schema] = await Promise.all(
    workflowSourcePaths.map((sourcePath) => readFile(sourcePath, "utf8")),
  );

  assert.doesNotMatch(tasks, /await import\(["']@\/lib\/workflow\/collaboration["']\)/);
  assert.match(tasks, /import \{ deleteWorkflowTaskWithCollaboration \} from "@\/lib\/workflow\/collaboration"/);
  assert.match(tasks, /import \{ ensureWorkflowCollaborationSchema, ensureWorkflowTaskSchema \} from "@\/lib\/workflow\/schema"/);
  assert.doesNotMatch(collaboration, /from ["']@\/lib\/workflow\/tasks["']/);
  assert.match(collaboration, /from "@\/lib\/workflow\/schema"/);
  assert.match(schema, /import type \{ D1Database \} from "@\/lib\/database\/d1"/);
  assert.match(schema, /export async function ensureWorkflowTaskSchema/);
  assert.match(schema, /export async function ensureWorkflowCollaborationSchema/);
  assert.doesNotMatch(schema, /@\/lib\/sales\/database/);
});

test("fresh server bundle does not lazy-import workflow mutations from its default-only entry", async (context) => {
  const serverDirectory = path.join(repositoryRoot, "dist", "server");
  const entryPath = path.join(serverDirectory, "index.js");
  let entryStat;
  try {
    entryStat = await stat(entryPath);
  } catch {
    context.skip("server bundle is absent; the full test command builds it before running this gate");
    return;
  }

  const sourceStats = await Promise.all(workflowSourcePaths.map((sourcePath) => stat(sourcePath)));
  const newestSourceTime = Math.max(...sourceStats.map((sourceStat) => sourceStat.mtimeMs));
  if (entryStat.mtimeMs < newestSourceTime) {
    context.skip("server bundle predates the workflow sources; run the full build test to verify the artifact");
    return;
  }

  const files = await listJavaScriptFiles(serverDirectory);
  const sources = await Promise.all(files.map((filePath) => readFile(filePath, "utf8")));
  const serverBundle = sources.join("\n");
  assert.doesNotMatch(
    serverBundle,
    /\{\s*ensureWorkflowCollaborationSchema\s*\}\s*=\s*await import\(["']\.\.\/index\.js["']\)/,
    "workflow create/update must not import a missing named export from the Worker entry",
  );
  assert.doesNotMatch(
    serverBundle,
    /\{\s*deleteWorkflowTaskWithCollaboration\s*\}\s*=\s*await import\(["']\.\.\/index\.js["']\)/,
    "workflow delete must not import a missing named export from the Worker entry",
  );
});
