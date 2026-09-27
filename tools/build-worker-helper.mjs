import { createHash } from "node:crypto";
import { readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const version = "teruisi-worker-helper-build-v1";
const entryRelativePath = "tools/tmall-sycm-cookie-pipeline.ts";
const pathResolvedCliGuard = 'path.resolve(process.argv[1] ?? "") === path.resolve(fileURLToPath(import.meta.url))';
const fileUrlCliGuard = "process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href";
const entryCliGuard = `if (${fileUrlCliGuard}) {`;
const directCliGuards = new Map([
  ["tools/jackyun-automation-runner.ts", pathResolvedCliGuard],
  ["tools/jackyun-browser-controller.ts", pathResolvedCliGuard],
  ["tools/jackyun-daily-runner.ts", pathResolvedCliGuard],
  ["tools/jackyun-download-runner.ts", pathResolvedCliGuard],
  ["tools/jd-multi-store-runner.ts", fileUrlCliGuard],
  ["tools/jd-promotion-export.ts", fileUrlCliGuard],
  ["tools/sales-import-runner.ts", pathResolvedCliGuard],
  ["tools/tmall-download-receipt.ts", fileUrlCliGuard],
  ["tools/tmall-multi-store-import-runner.ts", fileUrlCliGuard],
  ["tools/tmall-product-master-export.ts", fileUrlCliGuard],
  ["tools/tmall-promotion-export.ts", fileUrlCliGuard],
]);
const immutableResourceUrlDeclarations = new Map([
  ["tools/jd-secure-credential.ts", 'const credentialScript = fileURLToPath(new URL("./jd-credential-vault.ps1", import.meta.url));'],
  ["tools/tmall-secure-credential.ts", 'const credentialScript = fileURLToPath(new URL("./tmall-credential-vault.ps1", import.meta.url));'],
]);
const mutableRootDeclarations = new Map([
  ["tools/jackyun-api-export.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["lib/jackyun/run-lock.ts", "const projectRoot = resolveJackyunRunLockProjectRoot({ moduleUrl: import.meta.url });"],
  ["lib/jd/chromium-run-lock.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");'],
  ["tools/jackyun-automation-runner.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jackyun-browser-controller.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jackyun-daily-runner.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jackyun-download-runner.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jackyun-n8n-pipeline.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jd-market-ranking-daily.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jd-multi-store-runner.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jd-n8n-pipeline.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jd-promotion-export.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/jd-promotion-n8n-pipeline.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/sales-import-runner.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/tmall-multi-store-import-runner.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/tmall-pagewise-product-master-export.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/tmall-product-master-cadence.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/tmall-product-master-export.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  ["tools/tmall-promotion-export.ts", 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
  [entryRelativePath, 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");'],
]);
const immutableResourceUrlPaths = new Set(immutableResourceUrlDeclarations.keys());
const immutablePowerShellResources = Object.freeze([
  { sourceRelativePath: "tools/jd-credential-vault.ps1", outputRelativePath: "jd-credential-vault.ps1" },
  { sourceRelativePath: "tools/tmall-credential-vault.ps1", outputRelativePath: "tmall-credential-vault.ps1" },
]);

function fail(message) {
  throw new Error(message);
}

function ordinalCompare(left, right) {
  return left === right ? 0 : left < right ? -1 : 1;
}

function canonicalJson(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const keys = Object.keys(value).sort(ordinalCompare);
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  fail("helper builder canonical JSON value unsupported");
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function countExact(source, value) {
  return source.split(value).length - 1;
}

function countImportMeta(source) {
  return source.match(/\bimport\.meta\b/g)?.length ?? 0;
}

function parseArguments(argv) {
  if (argv.length !== 4 || argv[0] !== "--source-root" || argv[2] !== "--output") {
    fail("helper builder only accepts --source-root <absolute> --output <absolute>");
  }
  const sourceRoot = path.resolve(argv[1]);
  const output = path.resolve(argv[3]);
  if (!path.win32.isAbsolute(argv[1]) || !path.win32.isAbsolute(argv[3])) fail("helper builder paths must be absolute");
  if (output === sourceRoot || output.startsWith(`${sourceRoot}${path.sep}`)) fail("helper bundle output must be outside isolated source root");
  return { sourceRoot, output };
}

const mutableRootExpression = `(() => {
  const configuredMutableRoot = process.env.TERUISI_HELPER_MUTABLE_ROOT;
  if (!configuredMutableRoot || !path.isAbsolute(configuredMutableRoot)) {
    throw new Error("TERUISI_HELPER_MUTABLE_ROOT must be an absolute protected path");
  }
  return path.resolve(configuredMutableRoot);
})()`;

export function rewriteMutableProjectRoot(source, relativePath) {
  const declaration = mutableRootDeclarations.get(relativePath);
  if (!declaration) return { source, count: 0 };
  if (countExact(source, declaration) !== 1) {
    fail(`helper mutable-root declaration must be unique and exact: ${relativePath}`);
  }
  return {
    source: source.replace(declaration, `const projectRoot = ${mutableRootExpression};`),
    count: 1,
  };
}

export function rewriteControlledImportMeta(source, relativePath) {
  const uses = countImportMeta(source);
  if (relativePath === entryRelativePath) {
    if (uses !== 1 || countExact(source, entryCliGuard) !== 1) {
      fail(`helper entrypoint must retain one exact CLI import.meta guard: ${relativePath}`);
    }
    return { source, neutralized: false };
  }
  const resourceDeclaration = immutableResourceUrlDeclarations.get(relativePath);
  if (resourceDeclaration) {
    if (uses !== 1 || countExact(source, resourceDeclaration) !== 1) {
      fail(`immutable helper resource URL must be one exact sibling declaration: ${relativePath}`);
    }
    return { source, neutralized: false };
  }
  const cliGuard = directCliGuards.get(relativePath);
  if (cliGuard) {
    if (uses !== 1 || countExact(source, cliGuard) !== 1) {
      fail(`helper direct CLI guard must be unique and exact: ${relativePath}`);
    }
    return { source: source.replace(cliGuard, "false"), neutralized: true };
  }
  if (uses !== 0) fail(`helper input has an unreviewed import.meta use: ${relativePath}`);
  return { source, neutralized: false };
}

function rewritePowerShellMutableRoot(source, relativePath) {
  const preamble = `$projectRoot = [Environment]::GetEnvironmentVariable("TERUISI_HELPER_MUTABLE_ROOT")
if ([string]::IsNullOrWhiteSpace($projectRoot) -or -not [System.IO.Path]::IsPathRooted($projectRoot)) {
  throw "TERUISI_HELPER_MUTABLE_ROOT must be an absolute protected path"
}
$projectRoot = [System.IO.Path]::GetFullPath($projectRoot)`;
  if (relativePath === "tools/jd-credential-vault.ps1") {
    const expected = "$projectRoot = Split-Path -Parent $PSScriptRoot";
    if (!source.includes(expected)) fail(`immutable helper resource root contract missing: ${relativePath}`);
    return source.replace(expected, preamble);
  }
  if (relativePath === "tools/tmall-credential-vault.ps1") {
    const vault = '$vaultRoot = Join-Path (Split-Path -Parent $PSScriptRoot) ".runtime\\tmall-credentials"';
    const registry = '$registryFile = Join-Path (Split-Path -Parent $PSScriptRoot) "config\\tmall-store-accounts.json"';
    if (!source.includes(vault) || !source.includes(registry)) {
      fail(`immutable helper resource root contract missing: ${relativePath}`);
    }
    return source
      .replace(vault, `${preamble}\n$vaultRoot = Join-Path $projectRoot ".runtime\\tmall-credentials"`)
      .replace(registry, '$registryFile = Join-Path $projectRoot "config\\tmall-store-accounts.json"');
  }
  fail(`unexpected immutable PowerShell resource: ${relativePath}`);
}

async function main() {
  const { sourceRoot, output } = parseArguments(process.argv.slice(2));
  const entryPath = path.join(sourceRoot, ...entryRelativePath.split("/"));
  const mutableRootRewritePaths = new Set();
  const importMetaNeutralizedPaths = new Set();
  const mutableConfigPaths = new Set();
  const loadedInputPaths = new Set();
  const sourceByPath = new Map();

  const helperPlugin = {
    name: "teruisi-immutable-helper-contract",
    setup(context) {
      context.onResolve({ filter: /^@\// }, async (args) => {
        const base = path.join(sourceRoot, args.path.slice(2));
        for (const suffix of ["", ".ts", ".tsx", ".mjs", ".js", "/index.ts", "/index.tsx", "/index.mjs", "/index.js"]) {
          const candidate = `${base}${suffix}`;
          const info = await stat(candidate).catch(() => null);
          if (info?.isFile()) {
            const relativePath = path.relative(sourceRoot, candidate).replaceAll("\\", "/");
            if (/^config\/.+\.json$/.test(relativePath)) {
              mutableConfigPaths.add(relativePath);
              return { path: relativePath, namespace: "teruisi-mutable-config" };
            }
            return { path: candidate };
          }
        }
        fail(`helper bundle cannot resolve source alias: ${args.path}`);
      });
      context.onLoad({ filter: /.*/, namespace: "teruisi-mutable-config" }, async (args) => ({
        contents: `import { readFileSync } from "node:fs";
import path from "node:path";
const mutableRoot = process.env.TERUISI_HELPER_MUTABLE_ROOT;
if (!mutableRoot || !path.isAbsolute(mutableRoot)) throw new Error("TERUISI_HELPER_MUTABLE_ROOT must be an absolute protected path");
const value = JSON.parse(readFileSync(path.join(mutableRoot, ${JSON.stringify(args.path)}), "utf8"));
export default value;`,
        loader: "js",
      }));
      context.onLoad({ filter: /\.(?:[cm]?[jt]s|tsx|json)$/ }, async (args) => {
        const absolute = path.resolve(args.path);
        if (absolute !== sourceRoot && !absolute.startsWith(`${sourceRoot}${path.sep}`)) return null;
        const relativePath = path.relative(sourceRoot, absolute).replaceAll("\\", "/");
        const raw = await readFile(absolute);
        let source = raw.toString("utf8");
        const rewrite = rewriteMutableProjectRoot(source, relativePath);
        source = rewrite.source;
        if (rewrite.count > 0) mutableRootRewritePaths.add(relativePath);
        const importMetaRewrite = rewriteControlledImportMeta(source, relativePath);
        source = importMetaRewrite.source;
        if (importMetaRewrite.neutralized) importMetaNeutralizedPaths.add(relativePath);
        loadedInputPaths.add(relativePath);
        sourceByPath.set(relativePath, raw);
        const extension = path.extname(relativePath).toLowerCase();
        return {
          contents: source,
          loader: extension === ".tsx" ? "tsx" : extension === ".ts" ? "ts" : extension === ".json" ? "json" : "js",
        };
      });
    },
  };

  const result = await build({
    absWorkingDir: sourceRoot,
    entryPoints: [entryPath],
    outfile: output,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node24",
    packages: "external",
    legalComments: "none",
    metafile: true,
    logLevel: "silent",
    plugins: [helperPlugin],
  });
  const metafileKeys = Object.keys(result.metafile.inputs);
  const metafileConfigPaths = metafileKeys
    .filter((value) => value.startsWith("teruisi-mutable-config:"))
    .map((value) => value.slice("teruisi-mutable-config:".length))
    .sort(ordinalCompare);
  const inputPaths = metafileKeys
    .filter((value) => !value.startsWith("teruisi-mutable-config:"))
    .map((value) => path.relative(sourceRoot, path.resolve(sourceRoot, value)).replaceAll("\\", "/"))
    .sort(ordinalCompare);
  const uncontrolledInputs = inputPaths.filter((value) => !loadedInputPaths.has(value));
  if (!inputPaths.includes(entryRelativePath) || uncontrolledInputs.length > 0) {
    fail(`helper bundle metafile input closure does not match controlled source loader: ${uncontrolledInputs.join(",")}`);
  }
  const mutableConfigs = [...mutableConfigPaths].sort(ordinalCompare);
  if (canonicalJson(metafileConfigPaths) !== canonicalJson(mutableConfigs)) {
    fail("helper bundle mutable config closure is not exact");
  }
  if (!mutableRootRewritePaths.has(entryRelativePath)) fail("helper entrypoint mutable project root was not rewritten");
  const inputFiles = inputPaths.map((relativePath) => ({
    relativePath,
    sha256: sha256(sourceByPath.get(relativePath)),
  }));
  const outputRaw = await readFile(output);
  const resourceInputFiles = [];
  const resourceOutputFiles = [];
  for (const resource of immutablePowerShellResources) {
    const sourcePath = path.join(sourceRoot, ...resource.sourceRelativePath.split("/"));
    const raw = await readFile(sourcePath);
    const rewritten = rewritePowerShellMutableRoot(raw.toString("utf8"), resource.sourceRelativePath);
    const outputPath = path.join(path.dirname(output), resource.outputRelativePath);
    await writeFile(outputPath, rewritten, { encoding: "utf8", flag: "wx" });
    resourceInputFiles.push({ relativePath: resource.sourceRelativePath, sha256: sha256(raw) });
    resourceOutputFiles.push({ relativePath: resource.outputRelativePath, sha256: sha256(await readFile(outputPath)) });
  }
  const receipt = {
    version,
    entryRelativePath,
    inputFiles,
    mutableRootRewritePaths: [...mutableRootRewritePaths].sort(ordinalCompare),
    importMetaNeutralizedPaths: [...importMetaNeutralizedPaths].sort(ordinalCompare),
    immutableResourceUrlPaths: [...immutableResourceUrlPaths].sort(ordinalCompare),
    mutableConfigPaths: mutableConfigs,
    resourceInputFiles,
    resourceOutputFiles,
    outputSha256: sha256(outputRaw),
  };
  process.stdout.write(`${canonicalJson(receipt)}\n`);
}

const modulePath = fileURLToPath(import.meta.url);
if (process.argv[1] && path.resolve(process.argv[1]) === modulePath) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
