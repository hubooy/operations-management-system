import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import { dirname, extname, isAbsolute, relative, resolve, sep } from "node:path";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const PAGE_SOURCE_MAX_BYTES = 80_000;
const PAGE_ENTRY_MAX_BYTES = 180_000;
const AI_ASSISTANT_CHUNK_MAX_BYTES = 180_000;
const CUSTOMER_SERVICE_CHUNK_MAX_BYTES = 150_000;
const AI_RUNTIME_MARKER = "/api/ai/conversations?";
const CUSTOMER_RUNTIME_MARKER = "/api/customer-service/analyze";
const PROJECT_ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const LOCAL_MODULE_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".css"] as const;
const PARSED_SOURCE_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);
const RUNNING_IN_CI = Boolean(process.env.CI && !/^(?:0|false)$/i.test(process.env.CI));

type ClientManifestEntry = {
  file?: string;
  imports?: string[];
  dynamicImports?: string[];
};

const businessModuleEntries = ["dashboard", "shop", "sales", "inventory", "product", "import"] as const;
const splitClientSources = [
  ...businessModuleEntries.map((name) => `app/${name}-module-view.tsx`),
  "app/market-view.tsx",
  "app/n8n-workflow-view.tsx",
  "app/operations-view.tsx",
  "app/settings-view.tsx",
  "app/customer-service-view.tsx",
  "app/ai-module-view.tsx",
  "app/ai-assistant-view.tsx",
  "app/ai-space-view.tsx",
  "app/ai-space-management-view.tsx",
  "app/global-search-dialog.tsx",
] as const;

function isProjectPath(filePath: string) {
  const projectRelative = relative(PROJECT_ROOT, filePath);
  return projectRelative === "" || (!projectRelative.startsWith(`..${sep}`) && projectRelative !== ".." && !isAbsolute(projectRelative));
}

function localImportBase(importerPath: string, specifier: string) {
  const cleanSpecifier = specifier.split(/[?#]/, 1)[0] ?? "";
  if (cleanSpecifier.startsWith("@/")) return resolve(PROJECT_ROOT, cleanSpecifier.slice(2));
  if (cleanSpecifier.startsWith("./") || cleanSpecifier.startsWith("../")) {
    return resolve(dirname(importerPath), cleanSpecifier);
  }
  return null;
}

async function existingSourceFile(candidates: readonly string[]) {
  for (const candidate of candidates) {
    try {
      if ((await stat(candidate)).isFile()) return candidate;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
  return null;
}

async function resolveLocalSource(importerPath: string, specifier: string) {
  const base = localImportBase(importerPath, specifier);
  if (!base) return null;
  assert.equal(isProjectPath(base), true, `local import escapes the project: ${specifier} from ${relative(PROJECT_ROOT, importerPath)}`);
  const extension = extname(base);
  const candidates = extension
    ? [base]
    : [
        ...LOCAL_MODULE_EXTENSIONS.map((candidateExtension) => `${base}${candidateExtension}`),
        ...LOCAL_MODULE_EXTENSIONS.map((candidateExtension) => resolve(base, `index${candidateExtension}`)),
      ];
  const resolved = await existingSourceFile(candidates);
  assert.ok(resolved, `cannot resolve local import ${specifier} from ${relative(PROJECT_ROOT, importerPath)}`);
  return resolved;
}

function localModuleSpecifiers(filePath: string, source: string) {
  const scriptKind = [".tsx", ".jsx"].includes(extname(filePath)) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(filePath, source, ts.ScriptTarget.Latest, true, scriptKind);
  const specifiers = new Set<string>();
  const visit = (node: ts.Node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
      && node.moduleSpecifier && ts.isStringLiteralLike(node.moduleSpecifier)) {
      specifiers.add(node.moduleSpecifier.text);
    }
    if (ts.isCallExpression(node)
      && node.expression.kind === ts.SyntaxKind.ImportKeyword
      && node.arguments.length === 1
      && ts.isStringLiteralLike(node.arguments[0])) {
      specifiers.add(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return [...specifiers];
}

async function collectPageClientSourceGraph() {
  const pending = [resolve(PROJECT_ROOT, "app/page.tsx")];
  const visited = new Set<string>();
  while (pending.length > 0) {
    const filePath = pending.pop();
    if (!filePath || visited.has(filePath)) continue;
    visited.add(filePath);
    if (!PARSED_SOURCE_EXTENSIONS.has(extname(filePath))) continue;
    const source = await readFile(filePath, "utf8");
    for (const specifier of localModuleSpecifiers(filePath, source)) {
      const dependency = await resolveLocalSource(filePath, specifier);
      if (dependency && !visited.has(dependency)) pending.push(dependency);
    }
  }
  return [...visited].sort();
}

function failInCiOrSkipLocally(context: TestContext, reason: string) {
  if (RUNNING_IN_CI) assert.fail(`${reason}; CI 必须先生成与当前源码一致的 production client artifacts`);
  context.skip(reason);
}

test("page source delegates every large business module to a direct lazy chunk", async () => {
  const [page, ...modules] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    ...businessModuleEntries.map((name) => readFile(new URL(`../app/${name}-module-view.tsx`, import.meta.url), "utf8")),
  ]);
  assert.ok(Buffer.byteLength(page, "utf8") <= PAGE_SOURCE_MAX_BYTES, `page source is ${Buffer.byteLength(page, "utf8")} bytes; budget is ${PAGE_SOURCE_MAX_BYTES}`);
  for (const [index, name] of businessModuleEntries.entries()) {
    assert.match(page, new RegExp(`createReloadableLazy\\("${name}", \\(\\) => import\\("\\./${name}-module-view"\\)\\)`));
    assert.doesNotMatch(page, new RegExp(`^import (?!type )[^\\n]+from "\\./${name}-module-view"`, "m"));
    assert.match(modules[index] ?? "", /export default function (DashboardView|ShopView|SalesView|InventoryView|ProductView|ImportView)\(/);
    assert.doesNotMatch(modules[index] ?? "", /from "\.\/page"|import\("\.\/page"\)/);
  }
  assert.doesNotMatch(page, /function (DashboardView|ShopView|SalesView|InventoryView|ProductView|ImportView)\(/);
  assert.match(modules[businessModuleEntries.indexOf("shop")], /promotionItemsSnapshotToken[\s\S]+params\.set\("snapshotToken", promotionItemsSnapshotToken\)/);
  assert.match(modules[businessModuleEntries.indexOf("product")], /productSummarySnapshotTokenRef[\s\S]+params\.set\("snapshotToken", expectedSnapshotToken\)/);
});

function collectStaticClientFiles(
  manifest: Record<string, ClientManifestEntry>,
  entryKey: string,
) {
  const pending = [entryKey];
  const visited = new Set<string>();
  const files = new Set<string>();
  while (pending.length > 0) {
    const key = pending.pop();
    if (!key || visited.has(key)) continue;
    visited.add(key);
    const entry = manifest[key];
    if (!entry) continue;
    if (entry.file) files.add(entry.file);
    for (const importedKey of entry.imports ?? []) pending.push(importedKey);
  }
  return files;
}

test("page keeps the AI workspace and customer service behind direct lazy boundaries", async () => {
  const [page, aiModule, aiAssistant, aiAgents, aiMemory, aiScheduled, aiSpace, aiSpaceManagement, customerService] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/ai-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/ai-assistant-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/ai-agent-workflow-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/ai-memory-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/ai-dingtalk-schedules-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/ai-space-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/ai-space-management-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/customer-service-view.tsx", import.meta.url), "utf8"),
  ]);

  for (const [scope, modulePath] of [["ai", "ai-module-view"], ["customer_service", "customer-service-view"]]) {
    assert.match(page, new RegExp(`createReloadableLazy\\("${scope}", \\(\\) => import\\("\\./${modulePath}"\\)\\)`));
    assert.doesNotMatch(page, new RegExp(`^import (?!type )[^\\n]+from "\\./${modulePath}"`, "m"));
  }
  assert.match(page, /onRetry=\{\(\) => \{ resetReloadableLazyScope\(active\); \}\}/);
  assert.doesNotMatch(page, /function AiAssistantView\(|function CustomerServiceView\(/);
  assert.doesNotMatch(page, /conversationGenerationRef|listGenerationRef|newAiModelDraft|customerProblemTypes/);
  assert.match(page, /customer_service: \([^\n]+<CustomerServiceView[^\n]+currentUser=\{currentUser\}[^\n]+onNavigate=\{onNavigate\}/);
  assert.match(page, /ai: \([^\n]+<AiModuleView currentUser=\{currentUser\}/);
  assert.match(aiModule, /import \{ lazy, Suspense, type KeyboardEvent \} from "react"/);
  for (const [component, modulePath] of [
    ["AiAssistantView", "ai-assistant-view"],
    ["AiAgentWorkflowView", "ai-agent-workflow-view"],
    ["AiMemoryView", "ai-memory-view"],
    ["AiDingTalkSchedulesView", "ai-dingtalk-schedules-view"],
    ["AiSpaceView", "ai-space-view"],
    ["AiSpaceManagementView", "ai-space-management-view"],
  ]) {
    assert.match(aiModule, new RegExp(`const ${component} = lazy\\(\\(\\) => import\\("\\./${modulePath}"\\)\\)`));
    assert.doesNotMatch(aiModule, new RegExp(`^import (?!type )[^\\n]+from "\\./${modulePath}"`, "m"));
  }
  assert.ok((aiModule.match(/<Suspense fallback=/g) ?? []).length >= 6);
  assert.match(aiModule, /<AiAssistantView[^>]+workspace="chat"/);
  assert.match(aiModule, /<AiAgentWorkflowView[\s\S]*?currentUser=\{currentUser\}/);
  assert.match(aiModule, /<AiMemoryView currentUser={currentUser}/);
  assert.doesNotMatch(aiModule, /AiSandboxView|ai-sandbox-view/);
  assert.match(aiModule, /<AiDingTalkSchedulesView/);
  assert.match(aiModule, /<AiSpaceView/);
  assert.match(aiModule, /<AiSpaceManagementView/);

  assert.match(aiAssistant, /conversationControllerRef\.current\?\.abort\(\)/);
  assert.match(aiAssistant, /generation !== conversationGenerationRef\.current/);
  assert.match(aiAssistant, /messageGenerationRef/);
  assert.match(aiAssistant, /deleteConversation/);
  const aiWorkbench = await readFile(new URL("../app/ai-chat-workbench.tsx", import.meta.url), "utf8");
  assert.match(aiAssistant, /<AiChatWorkbench/);
  assert.match(aiWorkbench, /加载更多对话/);
  assert.match(aiWorkbench, /加载更早消息/);

  assert.match(customerService, /listControllerRef\.current\?\.abort\(\)/);
  assert.match(customerService, /listGenerationRef\.current === generation/);
  assert.match(customerService, /listRequestKeyRef\.current === requestKey/);
  assert.match(customerService, /response\.status === 409/);
  assert.match(customerService, /dialogId="customer-service-conversation-detail"/);
  assert.match(customerService, /messageTotalCount|messagesTruncated/);

  for (const lazyView of [aiModule, aiAssistant, aiAgents, aiMemory, aiScheduled, aiSpace, aiSpaceManagement, customerService]) {
    assert.doesNotMatch(lazyView, /from "\.\/page"|import\("\.\/page"\)/);
  }
});

test("fresh production artifacts keep the page entry and lazy view chunks within budget", async (context) => {
  const sourceFiles = await collectPageClientSourceGraph();
  const sourceFilesByProjectPath = new Set(sourceFiles.map((filePath) => relative(PROJECT_ROOT, filePath).replaceAll("\\", "/")));
  assert.equal(sourceFilesByProjectPath.has("app/page.tsx"), true);
  assert.equal(sourceFilesByProjectPath.has("app/module-view-shared.tsx"), true);
  assert.equal(sourceFilesByProjectPath.has("app/module-view-business-ui.tsx"), true);
  for (const expectedSource of splitClientSources) {
    assert.equal(sourceFilesByProjectPath.has(expectedSource), true, `source graph must include ${expectedSource}`);
  }

  const manifestUrl = new URL("../dist/client/.vite/manifest.json", import.meta.url);
  let manifestStat;
  try {
    manifestStat = await stat(manifestUrl);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    failInCiOrSkipLocally(context, "production client artifacts are not present");
    return;
  }

  const sourceStats = await Promise.all(sourceFiles.map(async (filePath) => ({
    filePath,
    stats: await stat(filePath),
  })));
  const newestSource = sourceStats.reduce((latest, candidate) => (
    candidate.stats.mtimeMs > latest.stats.mtimeMs ? candidate : latest
  ));
  if (manifestStat.mtimeMs < newestSource.stats.mtimeMs) {
    failInCiOrSkipLocally(
      context,
      `production client artifacts predate ${relative(PROJECT_ROOT, newestSource.filePath).replaceAll("\\", "/")} in the ${sourceFiles.length}-file page client graph`,
    );
    return;
  }

  const manifest = JSON.parse(await readFile(manifestUrl, "utf8")) as Record<string, ClientManifestEntry>;
  const pageEntry = manifest["app/page.tsx"]?.file;
  assert.ok(pageEntry, "client manifest must expose app/page.tsx");
  const pageStaticFiles = collectStaticClientFiles(manifest, "app/page.tsx");
  const pageDynamicImports = new Set(manifest["app/page.tsx"]?.dynamicImports ?? []);
  const pageEntryUrl = new URL(`../dist/client/${pageEntry}`, import.meta.url);
  const [pageEntrySource, pageEntryStat] = await Promise.all([readFile(pageEntryUrl, "utf8"), stat(pageEntryUrl)]);
  assert.ok(
    pageEntryStat.size <= PAGE_ENTRY_MAX_BYTES,
    `page entry is ${pageEntryStat.size} bytes; budget is ${PAGE_ENTRY_MAX_BYTES}`,
  );
  assert.doesNotMatch(pageEntrySource, new RegExp(AI_RUNTIME_MARKER));
  assert.doesNotMatch(pageEntrySource, new RegExp(CUSTOMER_RUNTIME_MARKER));
  for (const name of businessModuleEntries) {
    const entryKey = `app/${name}-module-view.tsx`;
    const chunkFile = manifest[entryKey]?.file;
    assert.ok(chunkFile, `client manifest must expose ${entryKey}`);
    assert.notEqual(chunkFile, pageEntry);
    assert.equal(pageStaticFiles.has(chunkFile), false, `${name} must not enter the page static dependency graph`);
    assert.equal(pageDynamicImports.has(entryKey), true, `${name} must be a direct dynamic import of app/page.tsx`);
  }

  const assetsUrl = new URL("../dist/client/assets/", import.meta.url);
  const javascriptAssets = (await readdir(assetsUrl)).filter((name) => name.endsWith(".js"));
  const markedAssets = new Map<string, Array<{ name: string; bytes: number }>>([
    [AI_RUNTIME_MARKER, []],
    [CUSTOMER_RUNTIME_MARKER, []],
  ]);
  for (const name of javascriptAssets) {
    const assetUrl = new URL(name, assetsUrl);
    const source = await readFile(assetUrl, "utf8");
    for (const marker of markedAssets.keys()) {
      if (source.includes(marker)) markedAssets.get(marker)?.push({ name, bytes: (await stat(assetUrl)).size });
    }
  }

  const aiChunks = markedAssets.get(AI_RUNTIME_MARKER) ?? [];
  const customerChunks = markedAssets.get(CUSTOMER_RUNTIME_MARKER) ?? [];
  assert.equal(aiChunks.length, 1, "one lazy client chunk must own the AI assistant runtime");
  assert.equal(customerChunks.length, 1, "one lazy client chunk must own the customer-service runtime");
  assert.notEqual(`assets/${aiChunks[0]?.name}`, pageEntry);
  assert.notEqual(`assets/${customerChunks[0]?.name}`, pageEntry);
  assert.equal(
    pageStaticFiles.has(`assets/${aiChunks[0]?.name}`),
    false,
    "the AI runtime must not be reachable through the page entry's static import graph",
  );
  assert.equal(
    pageStaticFiles.has(`assets/${customerChunks[0]?.name}`),
    false,
    "the customer-service runtime must not be reachable through the page entry's static import graph",
  );
  assert.ok(
    (aiChunks[0]?.bytes ?? Number.POSITIVE_INFINITY) <= AI_ASSISTANT_CHUNK_MAX_BYTES,
    `AI assistant chunk is ${aiChunks[0]?.bytes} bytes; budget is ${AI_ASSISTANT_CHUNK_MAX_BYTES}`,
  );
  assert.ok(
    (customerChunks[0]?.bytes ?? Number.POSITIVE_INFINITY) <= CUSTOMER_SERVICE_CHUNK_MAX_BYTES,
    `customer-service chunk is ${customerChunks[0]?.bytes} bytes; budget is ${CUSTOMER_SERVICE_CHUNK_MAX_BYTES}`,
  );
});
