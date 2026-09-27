import { readFile, readdir, stat } from "node:fs/promises";
import { resolve, dirname, relative } from "node:path";
import ts from "typescript";
import { n8nMetadataReaderPath, isApprovedN8nMetadataReader, metadataReaderImportRoots } from "./n8n-metadata-read-boundary.mjs";

const root = resolve(import.meta.dirname, "..");
async function files(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  return (await Promise.all(entries.map((entry) => entry.isDirectory()
    ? files(resolve(directory, entry.name)) : [resolve(directory, entry.name)]))).flat();
}
async function localModule(from, specifier) {
  if (!specifier.startsWith(".") && !specifier.startsWith("@/")) return null;
  const base = specifier.startsWith("@/") ? resolve(root, specifier.slice(2)) : resolve(dirname(from), specifier);
  for (const path of [base, `${base}.ts`, `${base}.tsx`, `${base}.mjs`, resolve(base, "index.ts")]) {
    if (await stat(path).then((info) => info.isFile()).catch(() => false)) return path;
  }
  throw new Error(`Unresolved production import: ${relative(root, from)} -> ${specifier}`);
}
export async function auditProductionBoundary() {
  const packageJson = JSON.parse(await readFile(resolve(root, "package.json"), "utf8"));
  const operationalEntrypoints = [...new Set([
    "tools/tmall-sycm-cookie-pipeline.ts",
    ...Object.values(packageJson.scripts).flatMap((command) => {
      const match = /^node\s+(?:--import\s+tsx\s+)?(tools\/[A-Za-z0-9._/-]+\.(?:ts|mjs))(?:\s|$)/.exec(command);
      return match && !["check:backend-boundary"].some((name) => command === packageJson.scripts[name]) ? [match[1]] : [];
    }),
  ])];
  const queue = (await Promise.all([files(resolve(root, "app")), files(resolve(root, "worker"))])).flat()
    .filter((path) => /\.[cm]?[jt]sx?$/.test(path));
  queue.push(...operationalEntrypoints.map((entry) => resolve(root, entry)));
  const entrypoints = [...queue], edges = new Map(), metadataCandidates = [];
  const parents = new Map(queue.map((path) => [path, null]));
  const violations = [];
  const chainFor = path => {
    const chain = [];
    for (let current = path; current; current = parents.get(current)) chain.unshift(relative(root, current).replaceAll("\\", "/"));
    return chain.join(" -> ");
  };
  for (let cursor = 0; cursor < queue.length; cursor++) {
    const path = queue[cursor];
    const source = await readFile(path, "utf8");
    // Inspect emitted JavaScript so type-only references to migration contracts
    // do not make offline tools part of the production execution graph.
    const emitted = ts.transpileModule(source, {
      fileName: path,
      compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve },
    }).outputText;
    const ast = ts.createSourceFile(path, emitted, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    const imports = [];
    let bindingAccess = false;
    function visit(node) {
      if ((ts.isPropertyAccessExpression(node) && node.name.text === "DB")
        || (ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression) && node.argumentExpression.text === "DB")
        || (ts.isBindingElement(node) && (node.propertyName?.getText(ast) ?? node.name.getText(ast)) === "DB")) bindingAccess = true;
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) imports.push(node.moduleSpecifier.text);
      if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword
        || (ts.isIdentifier(node.expression) && node.expression.text === "require"))) {
        if (!node.arguments[0] || !ts.isStringLiteral(node.arguments[0])) throw new Error(`Unbounded runtime import in ${path}`);
        imports.push(node.arguments[0].text);
      }
      ts.forEachChild(node, visit);
    }
    visit(ast);
    const relativePath = relative(root, path).replaceAll("\\", "/");
    if (relativePath === n8nMetadataReaderPath) metadataCandidates.push({ path, source, relativePath });
    else if (bindingAccess || /\b(?:getD1Database|getMarketDatabase|getFinanceDatabase|getInventoryDatabase|getNetshopDatabase|getErpReferenceDatabase)\s*\(|\b(?:env|environment)\s*(?:\.DB\b|\[\s*["']DB["']\s*\])|\bsqlite_master\b|\b(?:CREATE TABLE|INSERT INTO|DELETE FROM)\b/.test(emitted)
      || imports.some((specifier) => /^(?:drizzle-orm|@\/db\/|node:sqlite$|better-sqlite3$)/.test(specifier))) {
      violations.push(chainFor(path));
    }
    for (const specifier of imports) {
      const imported = await localModule(path, specifier);
      if (imported) edges.set(path, [...(edges.get(path) ?? []), imported]);
      if (imported && /\.[cm]?[jt]sx?$/.test(imported) && !parents.has(imported)) {
        parents.set(imported, path);
        queue.push(imported);
      }
    }
  }
  const approvedMetadataReaders = [];
  for (const candidate of metadataCandidates) {
    const roots = metadataReaderImportRoots(entrypoints, edges, candidate.path).map(path => relative(root, path).replaceAll("\\", "/"));
    if (!isApprovedN8nMetadataReader(candidate.relativePath, candidate.source, roots)) violations.push(chainFor(candidate.path));
    else approvedMetadataReaders.push({ path: candidate.relativePath, roots });
  }
  return { checkedModules: queue.length, operationalEntrypoints, violations, approvedMetadataReaders };
}
if (process.argv[1] && resolve(process.argv[1]) === resolve(import.meta.filename)) {
  const result = await auditProductionBoundary();
  console.log(JSON.stringify(result, null, 2));
  if (result.violations.length) process.exitCode = 1;
}
