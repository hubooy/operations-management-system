import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { copyFile, link, lstat, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { pathToFileURL } from "node:url";

import {
  authorityPayload,
  assertAuthorityTargetsCurrent,
  assertSupervisorPrelaunchProcessState,
  assertOrdinaryDeployAllowed,
  assertTrustedVerifierMatches,
  assertIsolatedSourceClosure,
  assertNoWranglerSourceConflict,
  assertVinextScratchAbsent,
  canonicalJson,
  canonicalWindowsPath,
  copyWorkerReleaseRuntimeArtifacts,
  consumeSupervisorPrelaunchVerificationReceipt,
  consumeVinextBuildScratch,
  deploymentKeyFiles,
  hashTree,
  hashRelativeFiles,
  treeHashMetadataConcurrency,
  treeHashReadBatchBytes,
  treeHashReadConcurrency,
  helperPathContainsMutableState,
  isAcceptedExactWorkerProcessStatus,
  ordinalCompare,
  publishSalesAuthorityPayload,
  publishFirstCurrentPointer,
  probeAnyLocalPort,
  salesRetirementMigrationSha256,
  sha256Bytes,
  sha256Canonical,
  trustedHelperBuilderSha256,
  withPayloadSha256,
  verifyPublishedAuthorityForCurrent,
  validateGuardReceipt,
  validateHelperBuilderEvidence,
  verifyWorkerReleaseProcessState,
  windowsPathSha256,
  workerReleaseBundledSourcePaths,
  workerReleaseKeyFilePaths,
  workerGuardCheckNames,
  workerGuardEntrypointPaths,
  workerGuardForbiddenScans,
  assertWorkerManifestProvenance,
  workerLegacySourceRoot,
  workerSourceRoot,
  assertBundledNpmToolchainProvenance,
  bundledNpmCliRelativePath,
  bundledNpmPackageRootRelativePath,
  bundledNpmPackageJsonRelativePath,
  npmCiArguments,
  resolveBundledNpmToolchain,
  runProcess,
  supervisorPrelaunchReceiptRetryDelayMs,
  supervisorPrelaunchReceiptWaitBudgetMs,
  supervisorPrelaunchVerificationReceiptMaxAgeMs,
  supervisorPrelaunchVerificationReceiptRelativePath,
  supervisorPrelaunchVerificationReceiptVersion,
} from "../tools/worker-local-release.mjs";
import {
  immutableMiniflareCacheBinding,
  immutableWorkerEnvironment,
  miniflareCacheRelativePath,
  prepareImmutableMiniflareCacheDirectory,
  resolveImmutableMiniflareCacheDirectory,
} from "../tools/worker-local-runtime-supervisor.mjs";
import {
  rewriteControlledImportMeta,
  rewriteMutableProjectRoot,
} from "../tools/build-worker-helper.mjs";
import {
  assertLegacyWorkerLaunchAllowed,
} from "../tools/worker-authority-guard.mjs";
import {
  d1ContainsRetirementTombstone,
  inspectD1RetirementState,
} from "../tools/legacy-d1-retirement-inspector.mjs";

async function availableLoopbackPort() {
  const server = createServer();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("ephemeral loopback port was unavailable");
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

async function waitForHelperHealth(port: number) {
  const expiresAt = Date.now() + 15_000;
  let lastError: unknown = null;
  while (Date.now() < expiresAt) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`);
      const body = await response.json() as { ok?: unknown };
      if (response.ok && body.ok === true) return;
      lastError = new Error(`health returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw lastError instanceof Error ? lastError : new Error("helper health timed out");
}

const retiredNames = [
  "sales_import_upload_chunks", "sales_import_uploads", "sales_order_lines", "sales_import_batches",
  "sales_overview_response_cache", "sales_overview_cache_state", "sales_projection_outbox",
  "sales_projection_source_state", "sales_write_authority",
];

test("release canonical JSON and Windows path identities are deterministic", () => {
  assert.equal(canonicalJson({ z: 1, a: [true, { y: null, x: "ok" }] }), '{"a":[true,{"x":"ok","y":null}],"z":1}');
  assert.equal(canonicalWindowsPath("d:/Teruisi/runtime/"), "D:\\TERUISI\\RUNTIME");
  assert.equal(windowsPathSha256("d:/Teruisi/runtime"), windowsPathSha256("D:\\TERUISI\\RUNTIME\\"));
  assert.match(sha256Canonical({ b: 2, a: 1 }), /^[0-9a-f]{64}$/);
  assert.deepEqual(["\uE000", "\u{10000}"].sort(ordinalCompare), ["\u{10000}", "\uE000"]);
  assert.equal(canonicalJson({ "\uE000": 1, "\u{10000}": 2 }), '{"𐀀":2,"":1}');
});

test("real release artifact packer carries every guarded entrypoint and binds each one as a key file", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-real-candidate-"));
  const sourceRoot = path.join(root, "source");
  const candidateRoot = path.join(root, "candidate");
  const expectedGuardEntrypoints = [
    "package.json",
    "运行项目.bat",
    "tools/operations-system-control.ps1",
    "tools/start-local-worker.mjs",
    "tools/worker-authority-guard.mjs",
    "tools/d1-retirement-proof.mjs",
    "tools/collect-d1-retirement-proof.mjs",
    "tools/worker-local-release.mjs",
    "tools/worker-local-release-rotation.mjs",
    "tools/worker-local-service.ps1",
    ".runtime/worker-release-activation-fence.json",
  ];
  try {
    assert.deepEqual([...workerGuardEntrypointPaths], expectedGuardEntrypoints);
    for (const relativePath of expectedGuardEntrypoints) {
      assert.ok(workerReleaseBundledSourcePaths.includes(relativePath), `${relativePath} must be bundled`);
      assert.ok(workerReleaseKeyFilePaths.includes(relativePath), `${relativePath} must be key-hashed`);
    }
    for (const relativePath of workerReleaseBundledSourcePaths) {
      const target = path.join(sourceRoot, ...relativePath.split("/"));
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, `immutable:${relativePath}`, "utf8");
    }
    await copyWorkerReleaseRuntimeArtifacts(sourceRoot, candidateRoot);
    for (const relativePath of expectedGuardEntrypoints) {
      assert.deepEqual(
        await readFile(path.join(candidateRoot, ...relativePath.split("/"))),
        await readFile(path.join(sourceRoot, ...relativePath.split("/"))),
      );
    }
    for (const relativePath of ["dist/server/index.js", "dist/server/wrangler.json", "helper/tmall-workflow-helper.mjs"]) {
      const target = path.join(candidateRoot, ...relativePath.split("/"));
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, `immutable:${relativePath}`, "utf8");
    }
    const keyFiles = await deploymentKeyFiles(candidateRoot);
    assert.deepEqual(
      keyFiles.filter((item) => expectedGuardEntrypoints.includes(item.relativePath)).map((item) => item.relativePath),
      expectedGuardEntrypoints,
    );
    for (const item of keyFiles) {
      assert.equal(item.sha256, sha256Bytes(await readFile(path.join(candidateRoot, ...item.relativePath.split("/")))));
    }
    const sourceFingerprint = "f".repeat(64);
    const protectedSourceRoot = path.join(root, "protected");
    const persistRoot = path.join(root, "persist");
    const sourceD1Path = path.join(root, "source.sqlite");
    const guardReceipt = withPayloadSha256({
      version: "teruisi-legacy-worker-guard-receipt-v1",
      generatedAt: "2026-08-30T00:00:00.000Z",
      sourceFingerprint,
      status: "passed",
      bindings: {
        protectedSourceRoot,
        protectedSourceRootPathSha256: windowsPathSha256(protectedSourceRoot),
        persistRoot,
        persistRootPathSha256: windowsPathSha256(persistRoot),
        sourceD1Path,
        sourceD1PathSha256: windowsPathSha256(sourceD1Path),
        authorityRelativePath: "state/sales-postgresql-authority.json",
        authoritySidecarRelativePath: "state/sales-postgresql-authority.json.sha256",
      },
      checks: Object.fromEntries(workerGuardCheckNames.map((name) => [name, true])),
      entrypoints: expectedGuardEntrypoints.map((relativePath) => ({
        relativePath,
        sha256: keyFiles.find((item) => item.relativePath === relativePath)!.sha256,
      })),
      forbiddenLegacyDirectCommands: workerGuardForbiddenScans.map((item) => ({ ...item, matches: [] })),
    }, "receiptPayloadSha256");
    const guardReceiptPath = path.join(candidateRoot, "audit", "legacy-worker-guard-receipt.json");
    await mkdir(path.dirname(guardReceiptPath), { recursive: true });
    const guardReceiptRaw = Buffer.from(`${canonicalJson(guardReceipt)}\n`, "utf8");
    await writeFile(guardReceiptPath, guardReceiptRaw);
    const manifest = {
      source: { sourceFingerprint },
      runtime: {
        protectedSourceRootPathSha256: windowsPathSha256(protectedSourceRoot),
        persistRootPathSha256: windowsPathSha256(persistRoot),
        sourceD1PathSha256: windowsPathSha256(sourceD1Path),
      },
      artifacts: {
        keyFiles,
        guardReceipt: {
          version: "teruisi-legacy-worker-guard-receipt-v1",
          relativePath: "audit/legacy-worker-guard-receipt.json",
          sha256: sha256Bytes(guardReceiptRaw),
        },
      },
    };
    await assert.doesNotReject(validateGuardReceipt(manifest, candidateRoot));
    await assert.rejects(validateGuardReceipt({
      ...manifest,
      artifacts: { ...manifest.artifacts, keyFiles: keyFiles.filter((item) => item.relativePath !== "运行项目.bat") },
    }, candidateRoot), /运行项目\.bat/);
    const authorityGuardSource = await readFile(path.resolve("tools/worker-authority-guard.mjs"), "utf8");
    assert.match(authorityGuardSource, /workerGuardEntrypointPaths/);
    assert.doesNotMatch(authorityGuardSource, /const\s+guardEntrypointPaths\s*=/);
    await rm(path.join(candidateRoot, "运行项目.bat"));
    await assert.rejects(deploymentKeyFiles(candidateRoot), /运行项目\.bat/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("production manifest provenance accepts only the legacy predecessor or isolated successor source", () => {
  const nodeRoot = path.dirname(process.execPath);
  const manifest = {
    source: { rootPathSha256: windowsPathSha256(workerLegacySourceRoot) },
    build: {
      nodeVersion: process.version,
      nodeExecutablePathSha256: windowsPathSha256(process.execPath),
      npmVersion: "11.6.0",
      npmPackageRootRelativePath: bundledNpmPackageRootRelativePath,
      npmPackageRootPathSha256: windowsPathSha256(path.join(nodeRoot, ...bundledNpmPackageRootRelativePath.split("/"))),
      npmCliRelativePath: bundledNpmCliRelativePath,
      npmCliPathSha256: windowsPathSha256(path.join(nodeRoot, ...bundledNpmCliRelativePath.split("/"))),
      npmCliSha256: "a".repeat(64),
      npmPackageJsonRelativePath: bundledNpmPackageJsonRelativePath,
      npmPackageJsonPathSha256: windowsPathSha256(path.join(nodeRoot, ...bundledNpmPackageJsonRelativePath.split("/"))),
      npmPackageJsonSha256: "b".repeat(64),
      npmPackageTree: { algorithm: "sha256-ordinal-path-length-content-v1", fileCount: 1, sha256: "c".repeat(64) },
      npmCiArguments: [...npmCiArguments],
    },
    runtime: {
      protectedSourceRoot: "D:\\运营管理系统",
      devVars: { sourcePath: "D:\\运营管理系统\\.dev.vars" },
    },
  };
  assert.doesNotThrow(() => assertWorkerManifestProvenance(manifest));
  assert.doesNotThrow(() => assertWorkerManifestProvenance({
    ...manifest,
    source: { rootPathSha256: windowsPathSha256(workerSourceRoot) },
  }));
  assert.throws(() => assertWorkerManifestProvenance({
    ...manifest,
    source: { rootPathSha256: windowsPathSha256("D:\\wrong-source") },
  }), /fixed legacy\/successor\/main|固定 legacy\/successor\/main/);
  assert.throws(() => assertWorkerManifestProvenance({
    ...manifest,
    runtime: { ...manifest.runtime, devVars: { sourcePath: "D:\\other\\.dev.vars" } },
  }), /protected source root|legacy\/successor\/main/);
  assert.throws(() => assertWorkerManifestProvenance({
    ...manifest,
    build: { ...manifest.build, npmCiArguments: ["ci", "--no-audit"] },
  }, { allowTestRuntimeRoot: true }), /npm ci provenance/);
  assert.throws(() => assertWorkerManifestProvenance({
    ...manifest,
    build: { ...manifest.build, npmVersion: "latest" },
  }, { allowTestRuntimeRoot: true }), /npm ci provenance/);
  assert.throws(() => assertWorkerManifestProvenance({
    ...manifest,
    build: { ...manifest.build, npmCliRelativePath: "../outside/npm-cli.js" },
  }, { allowTestRuntimeRoot: true }), /npm ci provenance/);
  assert.throws(() => assertWorkerManifestProvenance({
    ...manifest,
    build: { ...manifest.build, npmCliPathSha256: windowsPathSha256("D:\\outside\\npm-cli.js") },
  }, { allowTestRuntimeRoot: true }), /npm ci provenance/);
  assert.throws(() => assertWorkerManifestProvenance({
    ...manifest,
    build: { ...manifest.build, npmCiArguments: ["ci", "--no-audit", "--no-audit"] },
  }, { allowTestRuntimeRoot: true }), /npm ci provenance/);
  assert.throws(() => assertWorkerManifestProvenance({
    ...manifest,
    build: { ...manifest.build, npmPackageTree: { ...manifest.build.npmPackageTree, sha256: "tampered" } },
  }, { allowTestRuntimeRoot: true }), /npm ci provenance/);
});

test("Windows Node 24 runs its bundled npm CLI directly without cmd or shell", async () => {
  assert.equal(process.platform, "win32");
  assert.match(process.version, /^v24\./);
  assert.match(process.execPath, /Program Files/i);
  const toolchain = await resolveBundledNpmToolchain();
  assert.equal(toolchain.provenance.nodeExecutablePathSha256, windowsPathSha256(process.execPath));
  assert.equal(toolchain.provenance.npmCliPathSha256, windowsPathSha256(toolchain.npmCliPath));
  assert.match(toolchain.provenance.npmCliSha256, /^[0-9a-f]{64}$/);
  assert.match(toolchain.provenance.npmPackageJsonSha256, /^[0-9a-f]{64}$/);
  assert.equal(toolchain.provenance.npmPackageRootPathSha256, windowsPathSha256(toolchain.npmPackageRoot));
  assert.ok(toolchain.provenance.npmPackageTree.fileCount > 1_000);
  assert.match(toolchain.provenance.npmPackageTree.sha256, /^[0-9a-f]{64}$/);
  await assert.rejects(
    assertBundledNpmToolchainProvenance({
      ...toolchain.provenance,
      npmPackageTree: { ...toolchain.provenance.npmPackageTree, sha256: "d".repeat(64) },
    }),
    /toolchain 与 manifest provenance 不一致/,
  );
  assert.match(toolchain.provenance.npmVersion, /^\d+\.\d+\.\d+/);
  assert.deepEqual(toolchain.provenance.npmCiArguments, [...npmCiArguments]);

  const cwd = await mkdtemp(path.join(tmpdir(), "teruisi npm 空格-"));
  try {
    const postinstall = "node -e \"require('node:fs').writeFileSync('ci-marker.txt','exact-ci-ok')\"";
    await writeFile(path.join(cwd, "package.json"), `${JSON.stringify({
      name: "teruisi-npm-ci-fixture", version: "1.0.0", private: true, scripts: { postinstall },
    })}\n`, "utf8");
    await writeFile(path.join(cwd, "package-lock.json"), `${JSON.stringify({
      name: "teruisi-npm-ci-fixture",
      version: "1.0.0",
      lockfileVersion: 3,
      requires: true,
      packages: { "": { name: "teruisi-npm-ci-fixture", version: "1.0.0", scripts: { postinstall } } },
    })}\n`, "utf8");
    const direct = spawnSync(process.execPath, [toolchain.npmCliPath, "--version"], {
      cwd,
      encoding: "utf8",
      shell: false,
      windowsHide: true,
      timeout: 15_000,
    });
    assert.equal(direct.status, 0, direct.stderr);
    assert.equal(direct.stdout.trim(), toolchain.provenance.npmVersion);

    const exactCi = spawnSync(process.execPath, [toolchain.npmCliPath, ...npmCiArguments], {
      cwd,
      encoding: "utf8",
      env: {
        ...process.env,
        npm_config_cache: path.join(cwd, ".npm-cache"),
        npm_config_offline: "true",
      },
      shell: false,
      windowsHide: true,
      timeout: 30_000,
    });
    assert.equal(exactCi.status, 0, exactCi.stderr);
    assert.equal(await readFile(path.join(cwd, "ci-marker.txt"), "utf8"), "exact-ci-ok");
    assert.match(exactCi.stdout, /postinstall/);

    const legacy = spawnSync(path.join(path.dirname(process.execPath), "npm.cmd"), ["--version"], {
      cwd,
      encoding: "utf8",
      shell: false,
      windowsHide: true,
      timeout: 15_000,
    });
    assert.equal((legacy.error as NodeJS.ErrnoException | undefined)?.code, "EINVAL");
  } finally {
    await rm(cwd, { recursive: true, force: true });
  }

  const releaseTool = path.resolve("tools/worker-local-release.mjs");
  const rejectedOverride = spawnSync(process.execPath, [releaseTool, "deploy", "--npm-command", "npm.cmd"], {
    encoding: "utf8", shell: false, windowsHide: true,
  });
  assert.notEqual(rejectedOverride.status, 0);
  assert.match(rejectedOverride.stderr, /deploy 不支持参数 --npm-command/);
  const rejectedDuplicate = spawnSync(process.execPath, [
    releaseTool, "deploy", "--source-root", "D:\\first", "--source-root", "D:\\second",
  ], { encoding: "utf8", shell: false, windowsHide: true });
  assert.notEqual(rejectedDuplicate.status, 0);
  assert.match(rejectedDuplicate.stderr, /参数重复：--source-root/);

  const releaseSource = await readFile(releaseTool, "utf8");
  assert.match(releaseSource, /shell: false/);
  assert.doesNotMatch(releaseSource, /spawn\([^\n]*(?:npm\.cmd|--npm-command)/);
});

test("PowerShell 5 outer capture is isolated from noisy children and preserves bounded failures", async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), "teruisi ps5 warning 空格-"));
  try {
    const childPath = path.join(fixtureRoot, "noisy-child.mjs");
    const harnessPath = path.join(fixtureRoot, "release-harness.mjs");
    await writeFile(childPath, `
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
const mode = process.argv[2];
if (mode === "success") {
  process.stdout.write("child-normal-output\\n");
  process.stderr.write("npm warn deprecated fixture-success-warning\\n");
} else if (mode === "failure") {
  process.stdout.write("STDOUT_HEAD_CAUSE fixture failed\\n");
  process.stdout.write("y".repeat(3000));
  process.stdout.write("\\nSTDOUT_TAIL_CAUSE fixture failed\\n");
  process.stderr.write("HEAD_CAUSE fixture failed\\n");
  process.stderr.write("x".repeat(3000));
  process.stderr.write("\\nDATABASE_URL=postgresql://fixture:supersecret@127.0.0.1/db\\n");
  process.stderr.write(\`cwd=\${process.cwd()} secret_token=supersecret .dev.vars=must-not-leak\\n\`);
  process.stderr.write(\`\${process.env.TERUISI_FIXTURE_SECRET} \${encodeURIComponent(process.env.TERUISI_FIXTURE_SECRET ?? "")}\\n\`);
  process.stderr.write("\\rCARRIAGE_MARK\\u0007BELL_MARK\\n");
  process.stderr.write("TAIL_CAUSE fixture failed\\n");
  process.exitCode = 7;
} else if (mode === "flood") {
  process.stdout.write("z".repeat(4096));
} else if (mode === "hang") {
  setInterval(() => {}, 1000);
} else if (mode === "descendant") {
  const descendant = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], {
    stdio: ["ignore", "inherit", "inherit"],
    windowsHide: true,
  });
  writeFileSync(${JSON.stringify(path.join(fixtureRoot, "descendant.pid"))}, String(descendant.pid));
  setInterval(() => {}, 1000);
} else {
  process.exitCode = 9;
}
`, "utf8");
    const releaseModuleUrl = pathToFileURL(path.resolve("tools/worker-local-release.mjs")).href;
    await writeFile(harnessPath, `
import { runProcess, sha256Bytes } from ${JSON.stringify(releaseModuleUrl)};
try {
  const result = await runProcess(process.execPath, [${JSON.stringify(childPath)}, process.argv[2]], {
    cwd: ${JSON.stringify(fixtureRoot)},
    label: "fixture noisy child",
    maxOutputBytes: 8192,
    timeoutMs: 5000,
  });
  process.stdout.write(JSON.stringify({
    status: "ok",
    stdoutSha256: sha256Bytes(result.stdout),
    stderrSha256: sha256Bytes(result.stderr),
  }) + "\\n");
} catch (error) {
  process.stderr.write((error instanceof Error ? error.message : String(error)) + "\\n");
  process.exitCode = 1;
}
`, "utf8");
    const psFixture = `
$ErrorActionPreference = 'Stop'
try {
  $outerPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $lines = @(& $env:TERUISI_FIXTURE_NODE $env:TERUISI_FIXTURE_HARNESS $env:TERUISI_FIXTURE_MODE 2>&1)
    $nativeExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $outerPreference
  }
  if ($nativeExitCode -ne 0) { throw (($lines | ForEach-Object { $_.ToString() }) -join "\n") }
  [Console]::Out.WriteLine(($lines -join "\n"))
  exit 0
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}
`;
    const invokePs5 = (mode: string) => spawnSync("powershell.exe", [
      "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", psFixture,
    ], {
      encoding: "utf8",
      env: {
        ...process.env,
        TERUISI_FIXTURE_NODE: process.execPath,
        TERUISI_FIXTURE_HARNESS: harnessPath,
        TERUISI_FIXTURE_MODE: mode,
        TERUISI_FIXTURE_SECRET: "bare secret/value supersecret",
      },
      shell: false,
      windowsHide: true,
      timeout: 20_000,
    });

    const success = invokePs5("success");
    assert.equal(success.status, 0, success.stderr);
    const successLines = success.stdout.trim().split(/\r?\n/);
    assert.equal(successLines.length, 1);
    assert.equal(JSON.parse(successLines[0]).status, "ok");
    assert.doesNotMatch(success.stdout + success.stderr, /deprecated|fixture-success-warning|child-normal-output/);

    const failure = invokePs5("failure");
    assert.equal(failure.status, 1);
    const failureText = `${failure.stdout}\n${failure.stderr}`;
    assert.match(failureText, /exit=7/);
    assert.match(failureText, /HEAD_CAUSE/);
    assert.match(failureText, /TAIL_CAUSE/);
    assert.match(failureText, /STDOUT_HEAD_CAUSE/);
    assert.match(failureText, /STDOUT_TAIL_CAUSE/);
    assert.equal(failureText.includes("\\rCARRIAGE_MARK\\u0007BELL_MARK"), true);
    assert.doesNotMatch(failureText, /supersecret|postgresql:\/\/|must-not-leak|bare%20secret/i);
    assert.equal(failureText.toLowerCase().includes(fixtureRoot.toLowerCase()), false);

    await assert.rejects(
      runProcess(process.execPath, [childPath, "flood"], {
        cwd: fixtureRoot, label: "fixture flood", maxOutputBytes: 1024, timeoutMs: 5000,
      }),
      /输出超过 1024 字节上限/,
    );
    await assert.rejects(
      runProcess(process.execPath, [childPath, "hang"], {
        cwd: fixtureRoot, label: "fixture hang", maxOutputBytes: 1024, timeoutMs: 200,
      }),
      /超过 200ms 时限/,
    );
    await assert.rejects(
      runProcess(process.execPath, [childPath, "descendant"], {
        cwd: fixtureRoot, label: "fixture descendant", maxOutputBytes: 1024, timeoutMs: 600,
      }),
      /超过 600ms 时限/,
    );
    const descendantPid = Number.parseInt(await readFile(path.join(fixtureRoot, "descendant.pid"), "utf8"), 10);
    let descendantAlive = true;
    for (let index = 0; index < 20; index += 1) {
      try {
        process.kill(descendantPid, 0);
      } catch {
        descendantAlive = false;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.equal(descendantAlive, false, "timed-out child left a descendant holding release pipes");
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("helper builder accepts only exact unique import.meta contracts", () => {
  const entryGuard = 'if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {';
  assert.equal(
    rewriteControlledImportMeta(`${entryGuard}\n}`, "tools/tmall-sycm-cookie-pipeline.ts").neutralized,
    false,
  );
  assert.throws(() => rewriteControlledImportMeta(
    `${entryGuard}\nconst injected = import.meta.url;\n}`,
    "tools/tmall-sycm-cookie-pipeline.ts",
  ), /one exact CLI import\.meta guard/);

  const directGuard = 'process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href';
  const direct = rewriteControlledImportMeta(
    `if (${directGuard}) { void 0; }`,
    "tools/jd-multi-store-runner.ts",
  );
  assert.equal(direct.neutralized, true);
  assert.doesNotMatch(direct.source, /import\.meta/);
  assert.throws(() => rewriteControlledImportMeta(
    `if (${directGuard}) {}\nif (${directGuard}) {}`,
    "tools/jd-multi-store-runner.ts",
  ), /unique and exact/);

  const resource = 'const credentialScript = fileURLToPath(new URL("./jd-credential-vault.ps1", import.meta.url));';
  assert.equal(rewriteControlledImportMeta(resource, "tools/jd-secure-credential.ts").neutralized, false);
  assert.throws(() => rewriteControlledImportMeta(
    `${resource}\nconst leakedRoot = fileURLToPath(import.meta.url);`,
    "tools/jd-secure-credential.ts",
  ), /one exact sibling declaration/);

  const projectRoot = 'const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");';
  assert.equal(rewriteMutableProjectRoot(projectRoot, "tools/jd-multi-store-runner.ts").count, 1);
  assert.throws(() => rewriteMutableProjectRoot(
    `${projectRoot}\n${projectRoot}`,
    "tools/jd-multi-store-runner.ts",
  ), /must be unique and exact/);
  assert.equal(helperPathContainsMutableState("tools/tmp/injected.ts"), true);
  assert.equal(helperPathContainsMutableState("lib/domain/outputs/state.json"), true);
  assert.equal(helperPathContainsMutableState("tools/templates/helper.ts"), false);
});

test("source snapshot fingerprint detects a post-copy mutation", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-source-hash-"));
  try {
    await writeFile(path.join(root, "a.txt"), "one", "utf8");
    await writeFile(path.join(root, "b.txt"), "two", "utf8");
    const before = await hashRelativeFiles(root, ["a.txt", "b.txt"]);
    await writeFile(path.join(root, "b.txt"), "changed", "utf8");
    const after = await hashRelativeFiles(root, ["a.txt", "b.txt"]);
    assert.notEqual(after.sha256, before.sha256);
    assert.equal(after.fileCount, before.fileCount);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("bounded parallel tree hashing preserves the legacy ordinal byte protocol", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-parallel-hash-"));
  const relativeFiles = Array.from({ length: 73 }, (_, index) => (
    index % 3 === 0
      ? `nested/${String(index).padStart(3, "0")}-中文.txt`
      : `${String(index).padStart(3, "0")}.txt`
  ));
  try {
    for (const [index, relativePath] of relativeFiles.entries()) {
      const absolute = path.join(root, ...relativePath.split("/"));
      await mkdir(path.dirname(absolute), { recursive: true });
      await writeFile(absolute, `${relativePath}:${"x".repeat(index * 17)}\n`, "utf8");
    }
    const digest = createHash("sha256");
    for (const relativePath of [...relativeFiles].sort(ordinalCompare)) {
      const normalized = relativePath.replaceAll("\\", "/");
      const name = Buffer.from(normalized, "utf8");
      const content = await readFile(path.join(root, ...normalized.split("/")));
      const nameLength = Buffer.alloc(4);
      nameLength.writeUInt32BE(name.length);
      const contentLength = Buffer.alloc(8);
      contentLength.writeBigUInt64BE(BigInt(content.length));
      digest.update(nameLength).update(name).update(contentLength).update(content);
    }
    const expected = {
      algorithm: "sha256-ordinal-path-length-content-v1",
      fileCount: relativeFiles.length,
      sha256: digest.digest("hex"),
    };
    assert.deepEqual(await hashRelativeFiles(root, [...relativeFiles].reverse()), expected);
    assert.deepEqual(await hashTree(root), expected);
    await assert.rejects(
      hashRelativeFiles(root, ["zzz-missing.txt", "aaa-missing.txt"]),
      (error: unknown) => error instanceof Error
        && error.message.includes("aaa-missing.txt")
        && !error.message.includes("zzz-missing.txt"),
    );
    assert.equal(treeHashMetadataConcurrency, 64);
    assert.equal(treeHashReadConcurrency, 16);
    assert.equal(treeHashReadBatchBytes, 32 * 1024 * 1024);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("isolated source closure rejects build-added source files", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-source-closure-"));
  try {
    await writeFile(path.join(root, "a.ts"), "export const a = 1;\n", "utf8");
    const expected = await hashRelativeFiles(root, ["a.ts"]);
    await assertIsolatedSourceClosure(root, ["a.ts"], expected);
    await writeFile(path.join(root, "injected.ts"), "export const injected = true;\n", "utf8");
    await assert.rejects(
      assertIsolatedSourceClosure(root, ["a.ts"], expected),
      /文件集合发生增删/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("isolated source closure compares complete flat ordinal paths instead of DFS order", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-flat-source-"));
  const relativeFiles = [
    "a-.ts",
    "a/z.ts",
    "app.ts",
    "app/api/customer-service/import/chunks/route.ts",
    "app/api/customer-service/import/route.ts",
    "app/api/customer-service/import-history/route.ts",
    "甲-同级.ts",
    "甲/内.ts",
    "𐀀.ts",
    "\ue000.ts",
  ];
  try {
    for (const relativePath of relativeFiles) {
      const absolute = path.join(root, ...relativePath.split("/"));
      await mkdir(path.dirname(absolute), { recursive: true });
      await writeFile(absolute, `${relativePath}\n`, "utf8");
    }
    const flatOrdinal = [...relativeFiles].sort(ordinalCompare);
    const expectedTree = await hashRelativeFiles(root, [...relativeFiles].reverse());
    assert.deepEqual(expectedTree, await hashRelativeFiles(root, flatOrdinal));
    const unsortedExpected = [...flatOrdinal].reverse();
    const inputBefore = [...unsortedExpected];
    await assertIsolatedSourceClosure(root, unsortedExpected, expectedTree);
    assert.deepEqual(unsortedExpected, inputBefore, "closure mutated the caller's expected paths");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("isolated source closure reports bounded relative added and removed paths", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-source-diff-"));
  const removed = Array.from({ length: 12 }, (_, index) => `removed-${String(index).padStart(2, "0")}.ts`);
  const added = Array.from({ length: 12 }, (_, index) => `added-${String(index).padStart(2, "0")}.ts`);
  const expectedFiles = ["kept.ts", ...removed];
  try {
    for (const relativePath of expectedFiles) await writeFile(path.join(root, relativePath), `${relativePath}\n`, "utf8");
    const expectedTree = await hashRelativeFiles(root, expectedFiles);
    for (const relativePath of removed) await rm(path.join(root, relativePath));
    for (const relativePath of added) await writeFile(path.join(root, relativePath), `${relativePath}\n`, "utf8");
    await assert.rejects(
      assertIsolatedSourceClosure(root, expectedFiles, expectedTree),
      (error: unknown) => {
        assert.ok(error instanceof Error);
        const details = JSON.parse(error.message.slice(error.message.indexOf("：") + 1));
        assert.deepEqual(details.added, {
          count: 12,
          examples: ["added-00.ts", "added-01.ts", "added-02.ts", "added-03.ts"],
          omitted: 8,
        });
        assert.deepEqual(details.removed, {
          count: 12,
          examples: ["removed-00.ts", "removed-01.ts", "removed-02.ts", "removed-03.ts"],
          omitted: 8,
        });
        assert.doesNotMatch(error.message, /added-04\.ts|removed-04\.ts/);
        assert.equal(error.message.includes(root), false);
        assert.ok(error.message.length < 1_500);
        return true;
      },
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("isolated source closure diagnostics escape controls and cap long relative examples", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-source-diagnostic-"));
  try {
    const actualName = `${"actual-".repeat(18)}.ts`;
    await writeFile(path.join(root, actualName), "actual\n", "utf8");
    const unsafeExpected = `removed\r\n\t\u001b-${"\\\"long".repeat(30)}.ts`;
    await assert.rejects(
      assertIsolatedSourceClosure(root, [unsafeExpected], {
        algorithm: "not-read-on-set-mismatch", fileCount: 1, sha256: "0".repeat(64),
      }),
      (error: unknown) => {
        assert.ok(error instanceof Error);
        assert.doesNotMatch(error.message, /[\r\n\u001b]/);
        assert.equal(error.message.includes(root), false);
        assert.ok(error.message.length < 1_500);
        const details = JSON.parse(error.message.slice(error.message.indexOf("：") + 1));
        assert.ok(details.added.examples[0].length <= 64);
        assert.ok(details.removed.examples[0].length <= 64);
        return true;
      },
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("isolated source closure still reports content changes after set equality", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-source-content-"));
  try {
    await writeFile(path.join(root, "same.ts"), "before\n", "utf8");
    const expectedTree = await hashRelativeFiles(root, ["same.ts"]);
    await writeFile(path.join(root, "same.ts"), "after\n", "utf8");
    await assert.rejects(
      assertIsolatedSourceClosure(root, ["same.ts"], expectedTree),
      /内容发生变化/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Vinext scratch fixture accepts only the real 74-byte config and restores exact source closure", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-vinext-scratch-"));
  const sourceFiles = ["a.ts"];
  const raw = Buffer.from(JSON.stringify({
    configPath: "..\\..\\dist\\server\\wrangler.json",
    auxiliaryWorkers: [],
  }), "utf8");
  try {
    assert.equal(raw.length, 74);
    await writeFile(path.join(root, "a.ts"), "export const a = 1;\n", "utf8");
    const sourceTree = await hashRelativeFiles(root, sourceFiles);
    await assertVinextScratchAbsent(root, sourceFiles);
    await mkdir(path.join(root, "dist", "server"), { recursive: true });
    await writeFile(path.join(root, "dist", "server", "wrangler.json"), "{}\n", "utf8");
    await mkdir(path.join(root, ".wrangler", "deploy"), { recursive: true });
    await writeFile(path.join(root, ".wrangler", "deploy", "config.json"), raw);

    const consumed = await consumeVinextBuildScratch(root, sourceFiles);
    assert.equal(consumed.relativePath, ".wrangler/deploy/config.json");
    assert.equal(consumed.byteLength, 74);
    assert.equal(consumed.sha256, sha256Bytes(raw));
    await assert.rejects(lstat(path.join(root, ".wrangler")), (error: unknown) => (
      error instanceof Error && "code" in error && error.code === "ENOENT"
    ));
    await assertIsolatedSourceClosure(root, sourceFiles, sourceTree);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Vinext scratch preflight rejects source conflicts and any pre-build .wrangler object", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-vinext-preflight-"));
  try {
    assert.throws(
      () => assertNoWranglerSourceConflict(["a.ts", ".WRANGLER/deploy/config.json"]),
      /source snapshot 不得包含 \.wrangler/,
    );
    await assert.rejects(
      assertVinextScratchAbsent(root, [".wrangler"]),
      /source snapshot 不得包含 \.wrangler/,
    );
    await mkdir(path.join(root, ".wrangler"));
    await assert.rejects(
      assertVinextScratchAbsent(root, ["a.ts"]),
      /Vinext 构建前隔离 staging 已存在 \.wrangler scratch/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Vinext scratch rejects every unexpected sibling before deleting the verified config", async () => {
  const exactRaw = Buffer.from(JSON.stringify({
    configPath: "..\\..\\dist\\server\\wrangler.json",
    auxiliaryWorkers: [],
  }), "utf8");
  for (const location of ["wrangler-root", "deploy-root"] as const) {
    const root = await mkdtemp(path.join(tmpdir(), `teruisi-vinext-extra-${location}-`));
    try {
      await mkdir(path.join(root, "dist", "server"), { recursive: true });
      await writeFile(path.join(root, "dist", "server", "wrangler.json"), "{}\n", "utf8");
      await mkdir(path.join(root, ".wrangler", "deploy"), { recursive: true });
      const configPath = path.join(root, ".wrangler", "deploy", "config.json");
      await writeFile(configPath, exactRaw);
      if (location === "wrangler-root") await mkdir(path.join(root, ".wrangler", "unexpected-empty"));
      else await writeFile(path.join(root, ".wrangler", "deploy", "unexpected.json"), "{}", "utf8");
      await assert.rejects(
        consumeVinextBuildScratch(root, ["a.ts"]),
        /scratch对象集合无效/,
      );
      assert.deepEqual(await readFile(configPath), exactRaw, "rejected scratch deleted the verified config");
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  }
});

test("Vinext scratch config is strict JSON with one exact byte representation and target", async () => {
  const configPathValue = "..\\..\\dist\\server\\wrangler.json";
  const exactRaw = Buffer.from(JSON.stringify({ configPath: configPathValue, auxiliaryWorkers: [] }), "utf8");
  const serializedPath = JSON.stringify(configPathValue);
  const variants: Array<[string, Buffer]> = [
    ["bom", Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), exactRaw])],
    ["trailing-newline", Buffer.concat([exactRaw, Buffer.from("\n")])],
    ["oversized", Buffer.concat([exactRaw, Buffer.alloc(8192, 0x20)])],
    ["duplicate-key", Buffer.from(`{"configPath":${serializedPath},"configPath":${serializedPath},"auxiliaryWorkers":[]}`, "utf8")],
    ["nonempty-workers", Buffer.from(JSON.stringify({ configPath: configPathValue, auxiliaryWorkers: [{}] }), "utf8")],
    ["extra-key", Buffer.from(JSON.stringify({ configPath: configPathValue, auxiliaryWorkers: [], extra: true }), "utf8")],
    ["wrong-target", Buffer.from(JSON.stringify({ configPath: "..\\..\\other.json", auxiliaryWorkers: [] }), "utf8")],
  ];
  for (const [name, raw] of variants) {
    const root = await mkdtemp(path.join(tmpdir(), `teruisi-vinext-config-${name}-`));
    try {
      await mkdir(path.join(root, "dist", "server"), { recursive: true });
      await writeFile(path.join(root, "dist", "server", "wrangler.json"), "{}\n", "utf8");
      await mkdir(path.join(root, ".wrangler", "deploy"), { recursive: true });
      const configPath = path.join(root, ".wrangler", "deploy", "config.json");
      await writeFile(configPath, raw);
      await assert.rejects(consumeVinextBuildScratch(root, ["a.ts"]), /Vinext deploy scratch config\.json/);
      assert.deepEqual(await readFile(configPath), raw, `invalid ${name} config was deleted`);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  }

  const missingTargetRoot = await mkdtemp(path.join(tmpdir(), "teruisi-vinext-missing-target-"));
  try {
    await mkdir(path.join(missingTargetRoot, ".wrangler", "deploy"), { recursive: true });
    const configPath = path.join(missingTargetRoot, ".wrangler", "deploy", "config.json");
    await writeFile(configPath, exactRaw);
    await assert.rejects(consumeVinextBuildScratch(missingTargetRoot, ["a.ts"]), /wrangler\.json/);
    assert.deepEqual(await readFile(configPath), exactRaw);
  } finally {
    await rm(missingTargetRoot, { recursive: true, force: true });
  }
});

test("Vinext scratch rejects a hard-linked config before deleting either name", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-vinext-hardlink-"));
  const exactRaw = Buffer.from(JSON.stringify({
    configPath: "..\\..\\dist\\server\\wrangler.json",
    auxiliaryWorkers: [],
  }), "utf8");
  try {
    await mkdir(path.join(root, "dist", "server"), { recursive: true });
    await writeFile(path.join(root, "dist", "server", "wrangler.json"), "{}\n", "utf8");
    await mkdir(path.join(root, ".wrangler", "deploy"), { recursive: true });
    const outsideName = path.join(root, "linked-config-source.json");
    const configPath = path.join(root, ".wrangler", "deploy", "config.json");
    await writeFile(outsideName, exactRaw);
    await link(outsideName, configPath);
    await assert.rejects(
      consumeVinextBuildScratch(root, ["a.ts"]),
      /精确大小且无硬链接/,
    );
    assert.deepEqual(await readFile(outsideName), exactRaw);
    assert.deepEqual(await readFile(configPath), exactRaw);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Vinext scratch rejects a reparse-point .wrangler tree", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-vinext-reparse-root-"));
  const outside = await mkdtemp(path.join(tmpdir(), "teruisi-vinext-reparse-target-"));
  const exactRaw = Buffer.from(JSON.stringify({
    configPath: "..\\..\\dist\\server\\wrangler.json",
    auxiliaryWorkers: [],
  }), "utf8");
  try {
    await mkdir(path.join(root, "dist", "server"), { recursive: true });
    await writeFile(path.join(root, "dist", "server", "wrangler.json"), "{}\n", "utf8");
    await mkdir(path.join(outside, "deploy"), { recursive: true });
    await writeFile(path.join(outside, "deploy", "config.json"), exactRaw);
    await symlink(outside, path.join(root, ".wrangler"), "junction");
    await assert.rejects(
      consumeVinextBuildScratch(root, ["a.ts"]),
      /重解析点|真实路径不一致/,
    );
    assert.deepEqual(await readFile(path.join(outside, "deploy", "config.json")), exactRaw);
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(outside, { recursive: true, force: true });
  }
});

test("immutable helper bundle keeps code immutable and mutable state at the protected root", async () => {
  const buildOutput = await mkdtemp(path.join(tmpdir(), "teruisi-helper-builder-"));
  const runtimeProbeRoot = path.join(process.cwd(), `.tmp-worker-helper-probe-${process.pid}-${Date.now()}`);
  let helper: ReturnType<typeof spawn> | null = null;
  try {
    const output = path.join(buildOutput, "tmall-workflow-helper.mjs");
    const build = spawnSync(process.execPath, [
      path.resolve("tools/build-worker-helper.mjs"),
      "--source-root", path.resolve("."),
      "--output", output,
    ], { encoding: "utf8", windowsHide: true });
    assert.equal(build.status, 0, build.stderr);
    const evidence = JSON.parse(build.stdout.trim()) as {
      version: string;
      inputFiles: Array<{ relativePath: string; sha256: string }>;
      mutableRootRewritePaths: string[];
      importMetaNeutralizedPaths: string[];
      immutableResourceUrlPaths: string[];
      mutableConfigPaths: string[];
      resourceInputFiles: Array<{ relativePath: string; sha256: string }>;
      resourceOutputFiles: Array<{ relativePath: string; sha256: string }>;
      outputSha256: string;
    };
    assert.equal(evidence.version, "teruisi-worker-helper-build-v1");
    assert.equal(
      sha256Bytes(await readFile("tools/build-worker-helper.mjs")),
      trustedHelperBuilderSha256,
    );
    assert.equal(build.stdout, `${canonicalJson(evidence)}\n`);
    await validateHelperBuilderEvidence(evidence, path.resolve("."), buildOutput);
    await assert.rejects(validateHelperBuilderEvidence({
      ...evidence,
      mutableRootRewritePaths: evidence.mutableRootRewritePaths.filter(p => p !== "tools/jackyun-api-export.ts"),
    }, path.resolve("."), buildOutput), /mutable root rewrite 缺少/);
    await assert.rejects(validateHelperBuilderEvidence({
      ...evidence,
      inputFiles: evidence.inputFiles.filter(p => p.relativePath !== "config/jackyun-api-templates.json"),
    }, path.resolve("."), buildOutput), /不得嵌入 mutable config state/);
    const mutableConfig = "config/tmall-store-accounts.json";
    await assert.rejects(validateHelperBuilderEvidence({
      ...evidence,
      inputFiles: [...evidence.inputFiles, {
        relativePath: mutableConfig, sha256: sha256Bytes(await readFile(mutableConfig)),
      }].sort((a, b) => ordinalCompare(a.relativePath, b.relativePath)),
    }, path.resolve("."), buildOutput), /不得嵌入 mutable config state/);
    assert.deepEqual(evidence.mutableConfigPaths, [
      "config/tmall-store-accounts.json",
    ]);
    assert.deepEqual(evidence.inputFiles.filter((item) => item.relativePath.startsWith("config/")).map(item => item.relativePath), ["config/jackyun-api-templates.json"]);
    assert.equal(evidence.inputFiles.find(item => item.relativePath === "config/jackyun-api-templates.json")?.sha256, sha256Bytes(await readFile("config/jackyun-api-templates.json")));
    assert.deepEqual(evidence.mutableRootRewritePaths, [
      "lib/jackyun/run-lock.ts",
      "lib/jd/chromium-run-lock.ts",
      "tools/jackyun-api-export.ts",
      "tools/jackyun-automation-runner.ts",
      "tools/jackyun-browser-controller.ts",
      "tools/jackyun-daily-runner.ts",
      "tools/jackyun-download-runner.ts",
      "tools/jackyun-n8n-pipeline.ts",
      "tools/jd-market-ranking-daily.ts",
      "tools/jd-multi-store-runner.ts",
      "tools/jd-n8n-pipeline.ts",
      "tools/jd-promotion-export.ts",
      "tools/jd-promotion-n8n-pipeline.ts",
      "tools/sales-import-runner.ts",
      "tools/tmall-multi-store-import-runner.ts",
      "tools/tmall-pagewise-product-master-export.ts",
      "tools/tmall-product-master-cadence.ts",
      "tools/tmall-product-master-export.ts",
      "tools/tmall-promotion-export.ts",
      "tools/tmall-sycm-cookie-pipeline.ts",
    ]);
    assert.deepEqual(evidence.importMetaNeutralizedPaths, [
      "tools/jackyun-automation-runner.ts",
      "tools/jackyun-browser-controller.ts",
      "tools/jackyun-daily-runner.ts",
      "tools/jackyun-download-runner.ts",
      "tools/jd-multi-store-runner.ts",
      "tools/jd-promotion-export.ts",
      "tools/sales-import-runner.ts",
      "tools/tmall-download-receipt.ts",
      "tools/tmall-multi-store-import-runner.ts",
      "tools/tmall-product-master-export.ts",
      "tools/tmall-promotion-export.ts",
    ]);
    assert.deepEqual(evidence.immutableResourceUrlPaths, [
      "tools/jd-secure-credential.ts",
      "tools/tmall-secure-credential.ts",
    ]);
    assert.deepEqual(evidence.resourceInputFiles.map((item) => item.relativePath), [
      "tools/jd-credential-vault.ps1",
      "tools/tmall-credential-vault.ps1",
    ]);
    assert.deepEqual(evidence.resourceOutputFiles.map((item) => item.relativePath), [
      "jd-credential-vault.ps1",
      "tmall-credential-vault.ps1",
    ]);
    for (const item of evidence.resourceOutputFiles) {
      const content = await readFile(path.join(buildOutput, item.relativePath), "utf8");
      assert.match(content, /TERUISI_HELPER_MUTABLE_ROOT/);
      assert.doesNotMatch(content, /Split-Path -Parent \$PSScriptRoot.*(?:\.runtime|config)/);
    }
    assert.equal(sha256Bytes(await readFile(output)), evidence.outputSha256);

    // Run the generated bundle from beneath the repository so its package
    // externals resolve to the isolated package-lock closure.  Use an
    // ephemeral port; the production 5791 listener is never touched.
    await mkdir(runtimeProbeRoot);
    for (const relativePath of [
      "tmall-workflow-helper.mjs",
      "jd-credential-vault.ps1",
      "tmall-credential-vault.ps1",
    ]) await copyFile(path.join(buildOutput, relativePath), path.join(runtimeProbeRoot, relativePath));
    const port = await availableLoopbackPort();
    helper = spawn(process.execPath, [path.join(runtimeProbeRoot, "tmall-workflow-helper.mjs"), "serve", "--port", String(port)], {
      cwd: path.resolve("."),
      env: { ...process.env, TERUISI_HELPER_MUTABLE_ROOT: path.resolve(".") },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    await waitForHelperHealth(port);
  } finally {
    if (helper && helper.exitCode === null && helper.signalCode === null) {
      const exited = new Promise<void>((resolve) => helper!.once("exit", () => resolve()));
      helper.kill("SIGTERM");
      await Promise.race([exited, new Promise<void>((resolve) => setTimeout(resolve, 5_000))]);
    }
    await rm(runtimeProbeRoot, { recursive: true, force: true });
    await rm(buildOutput, { recursive: true, force: true });
  }
});

test("exclusive any-interface port probe rejects wildcard and IPv6 listeners", async () => {
  const wildcard = createServer();
  await new Promise<void>((resolve, reject) => {
    wildcard.once("error", reject);
    wildcard.listen({ host: "0.0.0.0", port: 0, exclusive: true }, resolve);
  });
  const wildcardAddress = wildcard.address();
  if (!wildcardAddress || typeof wildcardAddress === "string") throw new Error("wildcard listener address unavailable");
  try {
    assert.equal(await probeAnyLocalPort(wildcardAddress.port), true);
  } finally {
    await new Promise<void>((resolve, reject) => wildcard.close((error) => error ? reject(error) : resolve()));
  }

  const ipv6 = createServer();
  let listening = false;
  try {
    await new Promise<void>((resolve, reject) => {
      ipv6.once("error", reject);
      ipv6.listen({ host: "::", port: 0, exclusive: true, ipv6Only: true }, () => {
        listening = true;
        resolve();
      });
    });
    const ipv6Address = ipv6.address();
    if (!ipv6Address || typeof ipv6Address === "string") throw new Error("IPv6 listener address unavailable");
    assert.equal(await probeAnyLocalPort(ipv6Address.port), true);
  } catch (error) {
    const code = error && typeof error === "object" && "code" in error ? String(error.code) : "";
    if (!new Set(["EAFNOSUPPORT", "EADDRNOTAVAIL", "EPROTONOSUPPORT"]).has(code)) throw error;
  } finally {
    if (listening) await new Promise<void>((resolve, reject) => ipv6.close((error) => error ? reject(error) : resolve()));
  }
});

test("D1 retirement guard recognizes receipt or all exact tombstone views", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-tombstone-"));
  try {
    const legacyPath = path.join(root, "legacy.sqlite");
    let db = new DatabaseSync(legacyPath);
    for (const name of retiredNames) db.exec(`CREATE TABLE "${name}" (id INTEGER)`);
    db.close();
    assert.equal(await d1ContainsRetirementTombstone(legacyPath), false);

    const viewsPath = path.join(root, "views.sqlite");
    db = new DatabaseSync(viewsPath);
    for (const name of retiredNames) {
      db.exec(`CREATE VIEW \`${name}\` AS SELECT 'sales-domain-retired-v1' AS \`retirement_tombstone\` WHERE 0`);
    }
    db.close();
    assert.equal(await d1ContainsRetirementTombstone(viewsPath), true);
    assert.deepEqual(await inspectD1RetirementState(viewsPath), {
      detected: true,
      exactViewsPresent: true,
      exactSharedGuardsPresent: false,
      completedReceiptPresent: false,
      completed: false,
    });

    const receiptPath = path.join(root, "receipt.sqlite");
    db = new DatabaseSync(receiptPath);
    db.exec("CREATE TABLE domain_retirement_receipts (domain TEXT)");
    db.close();
    assert.equal(await d1ContainsRetirementTombstone(receiptPath), true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("immutable release completion requires views, shared guards, and completed receipt", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-retirement-complete-"));
  try {
    const databasePath = path.join(root, "complete.sqlite");
    const db = new DatabaseSync(databasePath);
    db.exec(`
      CREATE TABLE domain_retirement_receipts (domain TEXT, version TEXT, status TEXT);
      INSERT INTO domain_retirement_receipts VALUES ('sales', 'sales-domain-retirement-receipt-v1', 'completed');
      CREATE TABLE import_content_fingerprints (domain TEXT);
      CREATE TABLE import_content_attempts (domain TEXT);
      CREATE TABLE import_scope_heads (domain TEXT);
    `);
    for (const name of retiredNames) {
      db.exec(`CREATE VIEW \`${name}\` AS SELECT 'sales-domain-retired-v1' AS \`retirement_tombstone\` WHERE 0`);
    }
    for (const [shortName, tableName] of [
      ["fingerprints", "import_content_fingerprints"],
      ["attempts", "import_content_attempts"],
      ["scope_heads", "import_scope_heads"],
    ]) {
      db.exec(`
        CREATE TRIGGER \`sales_retired_${shortName}_insert_guard\`
        BEFORE INSERT ON \`${tableName}\`
        WHEN NEW.\`domain\` = 'sales'
        BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;
        CREATE TRIGGER \`sales_retired_${shortName}_update_guard\`
        BEFORE UPDATE ON \`${tableName}\`
        WHEN OLD.\`domain\` = 'sales' OR NEW.\`domain\` = 'sales'
        BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;
        CREATE TRIGGER \`sales_retired_${shortName}_delete_guard\`
        BEFORE DELETE ON \`${tableName}\`
        WHEN OLD.\`domain\` = 'sales'
        BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;
      `);
    }
    db.close();
    assert.deepEqual(await inspectD1RetirementState(databasePath), {
      detected: true,
      exactViewsPresent: true,
      exactSharedGuardsPresent: true,
      completedReceiptPresent: true,
      completed: true,
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("partial or malformed D1 tombstone views fail closed", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-tombstone-tamper-"));
  try {
    const partialPath = path.join(root, "partial.sqlite");
    let db = new DatabaseSync(partialPath);
    db.exec("CREATE VIEW sales_order_lines AS SELECT 'sales-domain-retired-v1' AS retirement_tombstone WHERE 0");
    db.close();
    await assert.rejects(d1ContainsRetirementTombstone(partialPath), /部分存在/);

    const malformedPath = path.join(root, "malformed.sqlite");
    db = new DatabaseSync(malformedPath);
    for (const name of retiredNames) {
      const marker = name === "sales_order_lines" ? "forged" : "sales-domain-retired-v1";
      db.exec(`CREATE VIEW \`${name}\` AS SELECT '${marker}' AS \`retirement_tombstone\` WHERE 0`);
    }
    db.close();
    await assert.rejects(d1ContainsRetirementTombstone(malformedPath), /tombstone SQL/);

    const triggerPath = path.join(root, "trigger.sqlite");
    db = new DatabaseSync(triggerPath);
    for (const name of retiredNames) {
      db.exec(`CREATE VIEW \`${name}\` AS SELECT 'sales-domain-retired-v1' AS \`retirement_tombstone\` WHERE 0`);
    }
    db.exec("CREATE TRIGGER forged_sales_tombstone INSTEAD OF INSERT ON sales_order_lines BEGIN SELECT 1; END");
    db.close();
    await assert.rejects(d1ContainsRetirementTombstone(triggerPath), /不得携带 trigger/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("installed runtime without current guard receipt never lets legacy launcher fail open", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-guard-missing-"));
  try {
    const marker = withPayloadSha256({
      version: "teruisi-local-worker-release-v1",
      runtimeRootPathSha256: windowsPathSha256(runtime),
    }, "markerPayloadSha256");
    await writeFile(path.join(runtime, "runtime-root.json"), `${canonicalJson(marker)}\n`, "utf8");
    await assert.rejects(assertLegacyWorkerLaunchAllowed({ runtimeRoot: runtime }), /旧源码 Worker 入口已永久失效/);
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test("authority first publication is create-only under competing tuples", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-authority-race-"));
  try {
    await mkdir(path.join(runtime, "state"));
    const common = {
      workerReleaseId: "20260830T120000Z-0123456789abcdef",
      workerReleaseManifestSha256: "1".repeat(64),
      guardReceiptSha256: "2".repeat(64),
      sourceD1PathSha256: "3".repeat(64),
      persistRootPathSha256: "4".repeat(64),
    };
    const left = authorityPayload({
      ...common, cutoverId: "cutover:left", djangoDeploymentManifestSha256: "5".repeat(64),
    });
    const right = authorityPayload({
      ...common, cutoverId: "cutover:right", djangoDeploymentManifestSha256: "6".repeat(64),
    });
    const results = await Promise.allSettled([
      publishSalesAuthorityPayload(runtime, left),
      publishSalesAuthorityPayload(runtime, right),
    ]);
    assert.equal(results.filter((item) => item.status === "fulfilled").length, 1);
    assert.equal(results.filter((item) => item.status === "rejected").length, 1);
    const stored = JSON.parse(await readFile(path.join(runtime, "state", "sales-postgresql-authority.json"), "utf8"));
    assert.ok(canonicalJson(stored) === canonicalJson(left) || canonicalJson(stored) === canonicalJson(right));
    const raw = await readFile(path.join(runtime, "state", "sales-postgresql-authority.json"));
    assert.equal(
      await readFile(path.join(runtime, "state", "sales-postgresql-authority.json.sha256"), "utf8"),
      `${createHash("sha256").update(raw).digest("hex")}\n`,
    );

    const sameRuntime = path.join(runtime, "same-tuple");
    await mkdir(path.join(sameRuntime, "state"), { recursive: true });
    const sameResults = await Promise.allSettled([
      publishSalesAuthorityPayload(sameRuntime, left),
      publishSalesAuthorityPayload(sameRuntime, left),
    ]);
    assert.equal(sameResults.every((item) => item.status === "fulfilled"), true);
    assert.equal(
      JSON.parse(await readFile(path.join(sameRuntime, "state", "sales-postgresql-authority.json"), "utf8")).cutoverId,
      left.cutoverId,
    );
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test("authority verification fails when current is deleted or tampered after publication", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-authority-current-"));
  try {
    await mkdir(path.join(runtime, "state"));
    const releaseId = "20260830T121500Z-0123456789abcdef";
    const releaseRoot = path.join(runtime, "releases", releaseId);
    await mkdir(releaseRoot, { recursive: true });
    const manifest = withPayloadSha256({
      version: "teruisi-local-worker-release-v1",
      releaseId,
      createdAt: "2026-08-30T12:15:00.000Z",
      source: {}, build: {},
      runtime: {
        runtimeRootPathSha256: windowsPathSha256(runtime),
        releaseRootPathSha256: windowsPathSha256(releaseRoot),
      },
      artifacts: {}, processIdentity: {},
    }, "manifestPayloadSha256");
    const manifestRaw = Buffer.from(`${canonicalJson(manifest)}\n`, "utf8");
    const manifestSha256 = sha256Bytes(manifestRaw);
    await writeFile(path.join(releaseRoot, "deployment-manifest.json"), manifestRaw);
    const pointer = withPayloadSha256({
      version: "teruisi-local-worker-current-v1",
      releaseId,
      manifestRelativePath: `releases/${releaseId}/deployment-manifest.json`,
      manifestSha256,
    }, "pointerPayloadSha256");
    const pointerPath = path.join(runtime, "current-deployment.json");
    await writeFile(pointerPath, `${canonicalJson(pointer)}\n`, "utf8");
    const payload = authorityPayload({
      cutoverId: "cutover:current-check",
      workerReleaseId: releaseId,
      workerReleaseManifestSha256: manifestSha256,
      djangoDeploymentManifestSha256: "5".repeat(64),
      guardReceiptSha256: "6".repeat(64),
      sourceD1PathSha256: "7".repeat(64),
      persistRootPathSha256: "8".repeat(64),
    });
    await publishSalesAuthorityPayload(runtime, payload);
    assert.equal((await verifyPublishedAuthorityForCurrent({
      runtimeRoot: runtime, payload, releaseId, manifestSha256, allowTestRuntimeRoot: true,
    })).status, "verified");

    await rm(pointerPath);
    await assert.rejects(
      verifyPublishedAuthorityForCurrent({
        runtimeRoot: runtime, payload, releaseId, manifestSha256, allowTestRuntimeRoot: true,
      }),
      /authority 只能绑定已安装的 exact current/,
    );
    await writeFile(pointerPath, '{"tampered":true}\n', "utf8");
    await assert.rejects(
      verifyPublishedAuthorityForCurrent({
        runtimeRoot: runtime, payload, releaseId, manifestSha256, allowTestRuntimeRoot: true,
      }),
      /current pointer.*字段集合无效/,
    );
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test("ordinary Deploy is first-publication only and rejects authority or retirement", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-deploy-policy-"));
  try {
    await mkdir(path.join(runtime, "state"));
    await mkdir(path.join(runtime, "releases"));
    const databasePath = path.join(runtime, "legacy.sqlite");
    const database = new DatabaseSync(databasePath);
    for (const name of retiredNames) database.exec(`CREATE TABLE \`${name}\` (id INTEGER)`);
    database.close();

    const first = await assertOrdinaryDeployAllowed({
      runtimeRoot: runtime, sourceD1Path: databasePath, allowTestRuntimeRoot: true,
    });
    assert.deepEqual(first, {
      currentPointerSha256: null,
      currentReleaseId: null,
      updatePolicy: "first-deploy-create-only",
    });
    await assert.rejects(
      assertAuthorityTargetsCurrent({
        runtimeRoot: runtime,
        releaseId: "20260830T140000Z-0123456789abcdef",
        manifestSha256: "0".repeat(64),
        allowTestRuntimeRoot: true,
      }),
      /authority 只能绑定已安装的 exact current/,
    );

    const releaseId = "20260830T140000Z-0123456789abcdef";
    const releaseRoot = path.join(runtime, "releases", releaseId);
    await mkdir(releaseRoot);
    const manifest = withPayloadSha256({
      version: "teruisi-local-worker-release-v1",
      releaseId,
      createdAt: "2026-08-30T14:00:00.000Z",
      source: {},
      build: {},
      runtime: {
        runtimeRootPathSha256: windowsPathSha256(runtime),
        releaseRootPathSha256: windowsPathSha256(releaseRoot),
      },
      artifacts: {},
      processIdentity: {},
    }, "manifestPayloadSha256");
    const manifestRaw = Buffer.from(`${canonicalJson(manifest)}\n`, "utf8");
    await writeFile(path.join(releaseRoot, "deployment-manifest.json"), manifestRaw);
    const pointer = withPayloadSha256({
      version: "teruisi-local-worker-current-v1",
      releaseId,
      manifestRelativePath: `releases/${releaseId}/deployment-manifest.json`,
      manifestSha256: sha256Bytes(manifestRaw),
    }, "pointerPayloadSha256");
    await writeFile(path.join(runtime, "current-deployment.json"), `${canonicalJson(pointer)}\n`, "utf8");

    await assert.rejects(
      assertOrdinaryDeployAllowed({
        runtimeRoot: runtime, sourceD1Path: databasePath, allowTestRuntimeRoot: true,
      }),
      /current pointer 已首次发布/,
    );
    const current = await assertAuthorityTargetsCurrent({
      runtimeRoot: runtime,
      releaseId,
      manifestSha256: sha256Bytes(manifestRaw),
      allowTestRuntimeRoot: true,
    });
    assert.equal(current.releaseId, releaseId);
    await assert.rejects(
      assertAuthorityTargetsCurrent({
        runtimeRoot: runtime,
        releaseId: "20260830T140001Z-1111111111111111",
        manifestSha256: sha256Bytes(manifestRaw),
        allowTestRuntimeRoot: true,
      }),
      /authority 只能绑定已安装的 exact current/,
    );
    await rm(path.join(runtime, "current-deployment.json"));

    await writeFile(path.join(runtime, "state", "sales-postgresql-authority.json"), "present\n", "utf8");
    await assert.rejects(
      assertOrdinaryDeployAllowed({ runtimeRoot: runtime, sourceD1Path: databasePath, allowTestRuntimeRoot: true }),
      /普通 Worker Deploy 永久禁用/,
    );
    await rm(path.join(runtime, "state", "sales-postgresql-authority.json"));

    const retiredDatabase = new DatabaseSync(databasePath);
    retiredDatabase.exec("CREATE TABLE domain_retirement_receipts (domain TEXT)");
    retiredDatabase.close();
    await assert.rejects(
      assertOrdinaryDeployAllowed({ runtimeRoot: runtime, sourceD1Path: databasePath, allowTestRuntimeRoot: true }),
      /D1 sales retirement 已开始/,
    );
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test("concurrent first current publication has one winner and never removes it", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-current-race-"));
  try {
    await mkdir(path.join(runtime, "state"));
    await mkdir(path.join(runtime, "releases"));
    const candidate = async (releaseId: string) => {
      const releaseRoot = path.join(runtime, "releases", releaseId);
      await mkdir(releaseRoot);
      const manifest = withPayloadSha256({
        version: "teruisi-local-worker-release-v1",
        releaseId,
        createdAt: "2026-08-30T15:00:00.000Z",
        source: {}, build: {},
        runtime: {
          runtimeRootPathSha256: windowsPathSha256(runtime),
          releaseRootPathSha256: windowsPathSha256(releaseRoot),
        },
        artifacts: {}, processIdentity: {},
      }, "manifestPayloadSha256");
      const manifestRaw = Buffer.from(`${canonicalJson(manifest)}\n`, "utf8");
      await writeFile(path.join(releaseRoot, "deployment-manifest.json"), manifestRaw);
      const currentPointer = withPayloadSha256({
        version: "teruisi-local-worker-current-v1",
        releaseId,
        manifestRelativePath: `releases/${releaseId}/deployment-manifest.json`,
        manifestSha256: sha256Bytes(manifestRaw),
      }, "pointerPayloadSha256");
      return { releaseId, releaseRoot, currentPointer };
    };
    const left = await candidate("20260830T150000Z-1111111111111111");
    const right = await candidate("20260830T150001Z-2222222222222222");
    const results = await Promise.allSettled([
      publishFirstCurrentPointer({ runtimeRoot: runtime, ...left, allowTestRuntimeRoot: true }),
      publishFirstCurrentPointer({ runtimeRoot: runtime, ...right, allowTestRuntimeRoot: true }),
    ]);
    assert.equal(results.filter((item) => item.status === "fulfilled").length, 1);
    assert.equal(results.filter((item) => item.status === "rejected").length, 1);
    const pointerRaw = await readFile(path.join(runtime, "current-deployment.json"));
    const winner = JSON.parse(pointerRaw.toString("utf8"));
    const loser = winner.releaseId === left.releaseId ? right : left;
    await readFile(path.join(runtime, "releases", winner.releaseId, "deployment-manifest.json"));
    await assert.rejects(readFile(path.join(loser.releaseRoot, "deployment-manifest.json")), /ENOENT/);
    assert.deepEqual(await readFile(path.join(runtime, "current-deployment.json")), pointerRaw);
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test("trusted Django verifier refuses a different candidate verifier", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-trusted-verifier-"));
  try {
    const trusted = path.join(root, "trusted.mjs");
    const candidate = path.join(root, "candidate.mjs");
    await writeFile(trusted, "export const trusted = true;\n", "utf8");
    await writeFile(candidate, "export const trusted = false;\n", "utf8");
    await assert.rejects(assertTrustedVerifierMatches(candidate, trusted), /trusted copy 不一致/);
    await writeFile(candidate, "export const trusted = true;\n", "utf8");
    assert.match(await assertTrustedVerifierMatches(candidate, trusted), /^[0-9a-f]{64}$/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("local port probe detects an exact IPv4 loopback listener", async () => {
  const server = createServer();
  try {
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, resolve);
    });
    const address = server.address();
    assert.ok(address && typeof address === "object");
    assert.equal(await probeAnyLocalPort(address.port), true);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve());
    });
  }
});

test("release source fixes build closure, real contract tests, guard and authority protocols", async () => {
  const source = await readFile("tools/worker-local-release.mjs", "utf8");
  assert.match(source, /Worker 发布构建固定要求 Node 24\.x/);
  assert.match(source, /stdio: \["ignore", "pipe", "pipe"\]/);
  assert.doesNotMatch(source, /stdio:\s*(?:"inherit"|capture)|capture:\s*true/);
  assert.match(source, /runProcess\(npmToolchain\.nodeExecutablePath, \[npmToolchain\.npmCliPath, \.\.\.npmCiArguments\]/);
  assert.match(source, /runProcess\(process\.execPath, \[path\.join\(buildRoot, "node_modules", "vinext", "dist", "cli\.js"\), "build"\]/);
  assert.match(source, /const copiedSourceTree = await hashRelativeFiles\(buildRoot, sourceFiles\)/);
  assert.match(source, /const postBuildSourceTree = await assertIsolatedSourceClosure\(/);
  assert.match(source, /return files\.sort\(ordinalCompare\)/);
  assert.match(source, /summarizeSortedFileDifferences\(actualFiles, expected\)/);
  assert.match(source, /canonicalJson\(differences\)/);
  assert.match(source, /await assertVinextScratchAbsent\(buildRoot, sourceFiles\)/);
  assert.match(source, /await consumeVinextBuildScratch\(buildRoot, sourceFiles\)/);
  assert.match(source, /immutable helper build 后的 source closure/);
  assert.doesNotMatch(source, /excludedPrefixes[^\n]+\.wrangler|recursive:\s*true[^\n]+Vinext/);
  const scratchConsumeAt = source.indexOf("await consumeVinextBuildScratch(buildRoot, sourceFiles)");
  const postBuildClosureAt = source.indexOf("const postBuildSourceTree = await assertIsolatedSourceClosure");
  const helperBuildAt = source.indexOf("const helperBuild = await buildWorkerHelperBundle(buildRoot, stageRoot)");
  assert.ok(scratchConsumeAt > 0 && scratchConsumeAt < postBuildClosureAt && postBuildClosureAt < helperBuildAt);
  assert.match(source, /runSalesContractTests\(buildRoot, sourceFingerprint, buildFingerprint\)/);
  assert.match(source, /tests\/sales-d1-retirement\.test\.ts/);
  assert.match(source, /tests\/django-sales-route-integration\.test\.ts/);
  assert.match(source, /"verify-guard", "write-authority", "verify-authority"/);
  assert.match(source, /sidecar_repaired/);
  assert.match(source, /supervisor_managed_immutable_bundle/);
  assert.match(source, /formal gate 要求 3000\/5791 两端口完全停止/);
  assert.match(source, /stopped policy 下不得残留 Worker process receipt/);
  assert.match(source, /supervisor-prelaunch policy 要求当前 supervisor PID/);
  assert.match(source, /const publication = await publishSalesAuthorityPayload/);
  assert.match(source, /verify-guard is the formal pre-recovery gate/);
  assert.doesNotMatch(source, /from\s+["'](?!node:)/);
  const rejected = spawnSync(process.execPath, [
    "tools/worker-local-release.mjs", "verify-authority", "--process-policy", "stopped",
  ], { encoding: "utf8", windowsHide: true });
  assert.notEqual(rejected.status, 0);
  assert.match(rejected.stderr, /verify-authority 不支持参数 --process-policy/);
  const acceptedPrelaunchFlag = spawnSync(process.execPath, [
    "tools/worker-local-release.mjs", "verify", "--write-supervisor-prelaunch-receipt",
  ], { encoding: "utf8", windowsHide: true });
  assert.notEqual(acceptedPrelaunchFlag.status, 0);
  assert.doesNotMatch(acceptedPrelaunchFlag.stderr, /参数无效.*write-supervisor-prelaunch-receipt/);
  assert.match(acceptedPrelaunchFlag.stderr, /缺少参数 --manifest/);
  const migration = await readFile("drizzle/0092_sales_domain_retirement.sql", "utf8");
  assert.equal(createHash("sha256").update(migration).digest("hex"), salesRetirementMigrationSha256);
  for (const name of retiredNames) assert.match(migration, new RegExp("CREATE VIEW `" + name + "` AS"));
  for (const name of [
    "sales_retired_fingerprints_insert_guard", "sales_retired_fingerprints_update_guard", "sales_retired_fingerprints_delete_guard",
    "sales_retired_attempts_insert_guard", "sales_retired_attempts_update_guard", "sales_retired_attempts_delete_guard",
    "sales_retired_scope_heads_insert_guard", "sales_retired_scope_heads_update_guard", "sales_retired_scope_heads_delete_guard",
  ]) assert.match(migration, new RegExp("CREATE TRIGGER `" + name + "`"));
  assert.match(await readFile("tools/sales-d1-retirement.ts", "utf8"), /sales-d1-retirement-v4/);
  const guard = await readFile("tools/worker-authority-guard.mjs", "utf8");
  assert.doesNotMatch(guard, /release-pre-cutover/);
  assert.match(guard, /readD1RetirementReceipt/);
  assert.doesNotMatch(guard, /node:sqlite|inspectD1RetirementState|DatabaseSync/);
  assert.match(guard, /authority\.guardReceiptSha256 !== context\.guard\.sha256/);
});

test("supervisor prelaunch policy binds its own exact starting identity instead of asserting stopped", async () => {
  const exactStartingStatus = {
    version: "teruisi-local-worker-status-v1",
    state: "starting_exact_release",
    supervisorProcessId: 4242,
  };
  assert.equal(isAcceptedExactWorkerProcessStatus(exactStartingStatus, {
    acceptedStates: ["starting_exact_release"],
    expectedSupervisorPid: 4242,
  }), true);
  assert.equal(isAcceptedExactWorkerProcessStatus({ ...exactStartingStatus, state: "exact_release" }, {
    acceptedStates: ["starting_exact_release"],
    expectedSupervisorPid: 4242,
  }), false);
  assert.equal(isAcceptedExactWorkerProcessStatus({ ...exactStartingStatus, supervisorProcessId: 4243 }, {
    acceptedStates: ["starting_exact_release"],
    expectedSupervisorPid: 4242,
  }), false);

  let stoppedFenceCalls = 0;
  let portProbeCalls = 0;
  let observed: Record<string, unknown> | undefined;
  const processState = await verifyWorkerReleaseProcessState({
    processPolicy: "supervisor-prelaunch",
    runtimeRoot: "D:\\runtime",
    manifestPath: "D:\\runtime\\releases\\20260830T130000Z-fedcba9876543210\\deployment-manifest.json",
    releaseRoot: "D:\\runtime\\releases\\20260830T130000Z-fedcba9876543210",
    expectedSupervisorPid: 4242,
    probePort: async () => {
      portProbeCalls += 1;
      return false;
    },
    readSupervisorPrelaunchState: async (options) => {
      observed = options;
      return "starting_exact_release";
    },
    assertStoppedState: async () => {
      stoppedFenceCalls += 1;
    },
  });
  assert.equal(processState, "starting_exact_release");
  assert.equal(stoppedFenceCalls, 0);
  assert.equal(portProbeCalls, 0);
  assert.deepEqual(observed, {
    manifestPath: "D:\\runtime\\releases\\20260830T130000Z-fedcba9876543210\\deployment-manifest.json",
    releaseRoot: "D:\\runtime\\releases\\20260830T130000Z-fedcba9876543210",
    expectedSupervisorPid: 4242,
  });
  await assert.rejects(
    verifyWorkerReleaseProcessState({ processPolicy: "supervisor-prelaunch", expectedSupervisorPid: 0 }),
    /要求当前 supervisor PID/,
  );
  await assert.rejects(
    assertSupervisorPrelaunchProcessState({ expectedSupervisorPid: Number.NaN }),
    /要求当前 supervisor PID/,
  );

  const supervisor = await readFile("tools/worker-local-runtime-supervisor.mjs", "utf8");
  const service = await readFile("tools/worker-local-service.ps1", "utf8");
  const serviceStartBlock = service.slice(
    service.indexOf("function Invoke-WorkerSystemStart"),
    service.indexOf("function Stop-WorkerOnly"),
  );
  const fullPrelaunchVerifyAt = serviceStartBlock.indexOf('Invoke-ReleaseVerification $identity "stopped" -WriteSupervisorPrelaunchReceipt');
  const verifiedLaunchAt = serviceStartBlock.indexOf("Start-VerifiedWorkerSupervisor");
  assert.ok(
    fullPrelaunchVerifyAt >= 0 && verifiedLaunchAt > fullPrelaunchVerifyAt,
    "the full stopped verifier must finish before the verified supervisor launch",
  );
  const verifiedLaunchFunction = service.slice(
    service.indexOf("function Start-VerifiedWorkerSupervisor"),
    service.indexOf("function Read-WorkerSystemMaintenance"),
  );
  assert.match(verifiedLaunchFunction, /\$process = Start-Process/);
  assert.match(supervisor, /consumeSupervisorPrelaunchVerificationReceipt\(\{/);
  assert.equal((supervisor.match(/assertSupervisorPrelaunchProcessState\(\{/g) ?? []).length, 3);
  assert.doesNotMatch(supervisor, /verifyWorkerRelease\(/);
  assert.match(service, /--write-supervisor-prelaunch-receipt/);
  assert.match(verifiedLaunchFunction, /Get-PortProcessIds \$WorkerPort \$WorkerHost[\s\S]*Get-PortProcessIds \$HelperPort \$HelperHost[\s\S]*Get-WorkerStatusInternal \$Identity/);
  assert.equal(supervisorPrelaunchReceiptWaitBudgetMs, 15_000);
  assert.equal(supervisorPrelaunchReceiptRetryDelayMs, 250);
  const releaseSource = await readFile("tools/worker-local-release.mjs", "utf8");
  const directReceiptFunction = releaseSource.slice(
    releaseSource.indexOf("export async function assertSupervisorPrelaunchProcessState"),
    releaseSource.indexOf("export async function verifyWorkerReleaseProcessState"),
  );
  assert.match(directReceiptFunction, /worker-process\.json/);
  assert.match(directReceiptFunction, /validatePayloadSha\(receipt/);
  assert.doesNotMatch(directReceiptFunction, /powershell|exactReleaseProcessState/);
});

test("hot restart keeps the verified supervisor and helper while replacing only the Worker child", async () => {
  const service = await readFile("tools/worker-local-service.ps1", "utf8");
  const restartBlock = service.slice(
    service.indexOf('if ($Action -eq "Restart")'),
    service.indexOf('if ($Action -eq "Stop")'),
  );
  assert.match(restartBlock, /\$readiness = Get-DjangoSystemReadiness/);
  assert.match(restartBlock, /Hot restart requires the aggregate-status Django runtime deployment/);
  assert.match(restartBlock, /if \(-not \$readiness\.Ready\)/);
  assert.match(restartBlock, /preflight exhausted its budget before changing the Worker process/);
  assert.doesNotMatch(restartBlock, /Ensure-DjangoSystemReady|Invoke-DjangoStartProcess/);
  assert.match(restartBlock, /\$status\.State -ne "exact_release"/);
  assert.match(restartBlock, /Restart-ExactWorkerChild \$status/);
  assert.match(restartBlock, /previousWorkerPortProcessId/);
  assert.match(restartBlock, /backendRestarted = \$false/);
  assert.match(restartBlock, /helperRestarted = \$false/);
  assert.match(restartBlock, /HotRestartBudgetMilliseconds - \$hotRestartTimer\.ElapsedMilliseconds/);
  assert.match(restartBlock, /elapsedMilliseconds = \[int64\]\$hotRestartTimer\.ElapsedMilliseconds/);
  assert.match(restartBlock, /http:\/\/127\.0\.0\.1:3000\//);
  assert.match(restartBlock, /http:\/\/127\.0\.0\.1:5791\/health/);
  assert.doesNotMatch(restartBlock, /Invoke-ReleaseVerification|Stop-ExactWorkerSnapshot|Remove-ExactProcessReceipt|Start-VerifiedWorkerSupervisor/);

  const childRestartFunction = service.slice(
    service.indexOf("function Restart-ExactWorkerChild"),
    service.indexOf("function Stop-ExactWorkerSnapshot"),
  );
  assert.match(childRestartFunction, /Test-SameProcessIdentity \$currentSupervisor \$supervisor/);
  assert.match(childRestartFunction, /Test-ExactWorkerRootProcess/);
  assert.ok(childRestartFunction.indexOf("Stop-ExactProcessIdentity $workerRoot") < childRestartFunction.indexOf("Get-CimInstance Win32_Process"));
  assert.match(childRestartFunction, /Worker root PID was reused before the hot-restart fence completed/);
  assert.match(childRestartFunction, /Get-ProcessTree \$workerRootId \$augmented/);
  assert.match(childRestartFunction, /Sort-Object Depth -Descending/);
  assert.match(childRestartFunction, /Stop-ExactProcessIdentity \$entry\.Process/);
  assert.match(childRestartFunction, /candidate\.State -eq "exact_release"/);
  assert.match(childRestartFunction, /\[DateTime\]::UtcNow -lt \$DeadlineUtc/);
  assert.match(childRestartFunction, /Hot restart did not replace the exact Worker port process/);

  const releaseSource = await readFile("tools/worker-local-release.mjs", "utf8");
  assert.match(
    releaseSource,
    /writeSupervisorPrelaunchReceipt && processPolicy !== "stopped"/,
  );
});

test("supervisor consumes one fresh full-verification receipt and rejects expired reuse", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-full-verification-receipt-"));
  const releaseId = "20260830T130000Z-fedcba9876543210";
  const releaseRoot = path.join(runtime, "releases", releaseId);
  const manifestPath = path.join(releaseRoot, "deployment-manifest.json");
  const stateRoot = path.join(runtime, "state");
  const receiptPath = path.join(runtime, ...supervisorPrelaunchVerificationReceiptRelativePath.split("/"));
  try {
    await mkdir(releaseRoot, { recursive: true });
    await mkdir(stateRoot, { recursive: true });
    const sourceFingerprint = "1".repeat(64);
    const buildFingerprint = "2".repeat(64);
    const contractReceiptSha256 = "3".repeat(64);
    const manifest = {
      source: { sourceFingerprint },
      build: { buildFingerprint },
      artifacts: { contractReceipt: { sha256: contractReceiptSha256 } },
    };
    const manifestRaw = Buffer.from(`${canonicalJson(manifest)}\n`, "utf8");
    const manifestSha256 = sha256Bytes(manifestRaw);
    await writeFile(manifestPath, manifestRaw);

    const makeReceipt = (issuedAtUnixMilliseconds: number) => withPayloadSha256({
      version: supervisorPrelaunchVerificationReceiptVersion,
      releaseId,
      manifestSha256,
      manifestPathSha256: windowsPathSha256(manifestPath),
      releaseRootPathSha256: windowsPathSha256(releaseRoot),
      sourceFingerprint,
      buildFingerprint,
      contractReceiptSha256,
      issuedAtUnixMilliseconds,
      nonce: "01234567-89ab-cdef-0123-456789abcdef",
    }, "receiptPayloadSha256");

    const fresh = makeReceipt(Date.now());
    await writeFile(receiptPath, `${canonicalJson(fresh)}\n`, "utf8");
    assert.equal((await consumeSupervisorPrelaunchVerificationReceipt({
      manifestPath, approvedManifestSha256: manifestSha256, releaseRoot,
    })).releaseId, releaseId);
    await assert.rejects(readFile(receiptPath), /ENOENT/);

    const expired = makeReceipt(Date.now() - supervisorPrelaunchVerificationReceiptMaxAgeMs - 1_000);
    await writeFile(receiptPath, `${canonicalJson(expired)}\n`, "utf8");
    await assert.rejects(consumeSupervisorPrelaunchVerificationReceipt({
      manifestPath, approvedManifestSha256: manifestSha256, releaseRoot,
    }), /身份或时效无效/);
    assert.equal((await readFile(receiptPath, "utf8")).length > 0, true);
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test("supervisor prelaunch waits for the real create-only receipt and rejects timeout or a forged PID", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-prelaunch-receipt-"));
  const releaseId = "20260830T130000Z-fedcba9876543210";
  const releaseRoot = path.join(runtime, "releases", releaseId);
  const toolsRoot = path.join(releaseRoot, "tools");
  const manifestPath = path.join(releaseRoot, "deployment-manifest.json");
  const supervisorPath = path.join(toolsRoot, "worker-local-runtime-supervisor.mjs");
  let supervisor: ReturnType<typeof spawn> | null = null;
  try {
    await mkdir(path.join(runtime, "state"), { recursive: true });
    await mkdir(toolsRoot, { recursive: true });
    await writeFile(supervisorPath, [
      "process.on('SIGTERM', () => process.exit(0));",
      "process.on('SIGINT', () => process.exit(0));",
      "setInterval(() => {}, 1000);",
      "",
    ].join("\n"), "utf8");
    const protectedRoot = path.resolve(".");
    const manifest = withPayloadSha256({
      version: "teruisi-local-worker-release-v1",
      releaseId,
      createdAt: "2026-08-30T13:00:00.000Z",
      source: {},
      build: {},
      runtime: {
        runtimeRootPathSha256: windowsPathSha256(runtime),
        releaseRootPathSha256: windowsPathSha256(releaseRoot),
        protectedSourceRoot: protectedRoot,
        protectedSourceRootPathSha256: windowsPathSha256(protectedRoot),
        helperMode: "supervisor_managed_immutable_bundle",
        helperHost: "127.0.0.1",
        helperPort: 5791,
        helperMutableRoot: protectedRoot,
        helperMutableRootPathSha256: windowsPathSha256(protectedRoot),
      },
      artifacts: {},
      processIdentity: {
        supervisorEntrypoint: "tools/worker-local-runtime-supervisor.mjs",
        serviceControl: "tools/worker-local-service.ps1",
        manifestFile: "deployment-manifest.json",
        processReceipt: "state/worker-process.json",
        processReceiptVersion: "teruisi-local-worker-process-v1",
        wranglerEntrypoint: "node_modules/wrangler/bin/wrangler.js",
        wranglerCliEntrypoint: "node_modules/wrangler/wrangler-dist/cli.js",
        fixedWranglerArguments: ["dev"],
        helperEntrypoint: "helper/tmall-workflow-helper.mjs",
        fixedHelperArguments: ["serve", "--port", "5791"],
      },
    }, "manifestPayloadSha256");
    const manifestRaw = Buffer.from(canonicalJson(manifest) + "\n", "utf8");
    await writeFile(manifestPath, manifestRaw);
    const manifestSha256 = sha256Bytes(manifestRaw);
    supervisor = spawn(process.execPath, [
      supervisorPath, "--manifest", manifestPath, "--approved-manifest-sha256", manifestSha256,
    ], { windowsHide: true, stdio: "ignore" });
    assert.ok(Number.isSafeInteger(supervisor.pid) && (supervisor.pid ?? 0) > 0);

    const servicePath = path.resolve("tools/worker-local-service.ps1").replaceAll("'", "''");
    const runtimePs = runtime.replaceAll("'", "''");
    const manifestPs = manifestPath.replaceAll("'", "''");
    const writerScript = [
      ". '" + servicePath + "' -FunctionsOnly -RuntimeRoot '" + runtimePs + "' -AllowTestRuntimeRoot",
      "Start-Sleep -Milliseconds 2500",
      "$identity = Get-ManifestIdentity '" + manifestPs + "'",
      "$supervisor = $null",
      "for ($attempt = 0; $attempt -lt 50 -and -not $supervisor; $attempt++) {",
      "  $supervisor = Get-CimInstance Win32_Process -Filter \"ProcessId = " + String(supervisor.pid) + "\" -ErrorAction SilentlyContinue",
      "  if (-not $supervisor) { Start-Sleep -Milliseconds 100 }",
      "}",
      "if (-not $supervisor -or -not (Test-AllowedTreeProcess $supervisor $identity " + String(supervisor.pid) + ")) { throw 'exact supervisor identity missing' }",
      "Write-ProcessReceipt $identity $supervisor",
      "[Console]::Out.WriteLine('written')",
    ].join("\n");
    const writer = runProcess("powershell.exe", [
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", writerScript,
    ], { label: "delayed real process receipt writer", timeoutMs: 10_000 });
    const waitedAt = Date.now();
    assert.equal(await assertSupervisorPrelaunchProcessState({
      manifestPath,
      releaseRoot,
      expectedSupervisorPid: supervisor.pid,
      waitBudgetMs: 10_000,
      retryDelayMs: 50,
    }), "starting_exact_release");
    assert.ok(Date.now() - waitedAt >= 2_000, "prelaunch did not actually wait for the delayed service receipt");
    assert.equal((await writer).stdout.trim(), "written");

    const receiptPath = path.join(runtime, "state", "worker-process.json");
    const validReceipt = JSON.parse(await readFile(receiptPath, "utf8"));
    const forgedCore = { ...validReceipt, supervisorPid: (supervisor.pid ?? 0) + 1 };
    delete forgedCore.receiptPayloadSha256;
    const forgedReceipt = withPayloadSha256(forgedCore, "receiptPayloadSha256");
    await writeFile(receiptPath, canonicalJson(forgedReceipt) + "\n", "utf8");
    await assert.rejects(assertSupervisorPrelaunchProcessState({
      manifestPath,
      releaseRoot,
      expectedSupervisorPid: supervisor.pid,
      waitBudgetMs: 500,
      retryDelayMs: 25,
    }), /未绑定当前 supervisor\/release 身份/);

    await rm(receiptPath, { force: true });
    const timeoutAt = Date.now();
    await assert.rejects(assertSupervisorPrelaunchProcessState({
      manifestPath,
      releaseRoot,
      expectedSupervisorPid: supervisor.pid,
      waitBudgetMs: 150,
      retryDelayMs: 25,
    }), /未在有界时间内取得 create-only process receipt/);
    assert.ok(Date.now() - timeoutAt < 2_000, "missing receipt wait exceeded its bounded test budget");
  } finally {
    if (supervisor && supervisor.exitCode == null && supervisor.signalCode == null) supervisor.kill("SIGTERM");
    await rm(runtime, { recursive: true, force: true });
  }
});

test("immutable Worker keeps Miniflare cache outside node_modules across two starts and rejects unsafe cache objects", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-miniflare-cache-"));
  const runtime = path.join(root, "runtime");
  const releaseRoot = path.join(runtime, "releases", "20260830T130000Z-fedcba9876543210");
  const nodeModulesRoot = path.join(releaseRoot, "node_modules");
  const fakeWrangler = path.join(releaseRoot, "fake-wrangler.mjs");
  const persistRoot = path.join(root, "persist");
  try {
    await mkdir(path.join(runtime, "state"), { recursive: true });
    await mkdir(nodeModulesRoot, { recursive: true });
    await mkdir(persistRoot);
    await writeFile(path.join(nodeModulesRoot, "immutable-package.txt"), "bound-by-manifest\n", "utf8");
    await writeFile(fakeWrangler, [
      "import { mkdir, writeFile } from 'node:fs/promises';",
      "import path from 'node:path';",
      "const custom = process.env.CLOUDFLARE_CF_FETCH_PATH;",
      "const target = custom || path.resolve(process.env.MINIFLARE_CACHE_DIR, 'cf.json');",
      "await mkdir(path.dirname(target), { recursive: true });",
      "await writeFile(target, JSON.stringify({ source: 'miniflare-fixture' }) + '\\n', 'utf8');",
      "",
    ].join("\n"), "utf8");
    const expectedNodeModulesTree = await hashTree(nodeModulesRoot);
    const maliciousCachePath = path.join(nodeModulesRoot, ".mf", "caller-controlled.json");
    const environment = immutableWorkerEnvironment({
      runtimeRoot: runtime,
      releaseRoot,
      persistRoot,
      inheritedEnvironment: {
        ...process.env,
        miniflare_cache_dir: path.join(nodeModulesRoot, ".mf"),
        cloudflare_cf_fetch_path: maliciousCachePath,
        cloudflare_cf_fetch_enabled: "false",
        teruisi_local_wrangler_state_dir: path.join(root, "wrong-persist"),
      },
    });
    const cacheBinding = immutableMiniflareCacheBinding({ runtimeRoot: runtime, releaseRoot, persistRoot });
    const cacheRoot = cacheBinding.cacheRoot;
    await assert.rejects(lstat(path.join(runtime, "cache")), (error: unknown) => (
      error instanceof Error && "code" in error && error.code === "ENOENT"
    ));
    assert.equal(miniflareCacheRelativePath, "cache/miniflare");
    assert.equal(cacheRoot, path.join(runtime, "cache", "miniflare"));
    assert.equal(resolveImmutableMiniflareCacheDirectory(runtime), cacheRoot);
    assert.equal(cacheBinding.cacheRootPathSha256, windowsPathSha256(cacheRoot));
    assert.equal(cacheBinding.cacheFile, path.join(cacheRoot, "cf.json"));
    assert.equal(cacheBinding.cacheFilePathSha256, windowsPathSha256(cacheBinding.cacheFile));
    assert.deepEqual(immutableMiniflareCacheBinding({ runtimeRoot: runtime, releaseRoot, persistRoot }), cacheBinding);
    assert.equal(environment.MINIFLARE_CACHE_DIR, cacheRoot);
    assert.equal(environment.CLOUDFLARE_CF_FETCH_PATH, cacheBinding.cacheFile);
    assert.equal(environment.CLOUDFLARE_CF_FETCH_ENABLED, "true");
    assert.equal(environment.TERUISI_LOCAL_WRANGLER_STATE_DIR, path.resolve(persistRoot));
    assert.deepEqual(
      Object.keys(environment).filter((name) => [
        "MINIFLARE_CACHE_DIR", "CLOUDFLARE_CF_FETCH_PATH", "CLOUDFLARE_CF_FETCH_ENABLED",
        "TERUISI_LOCAL_WRANGLER_STATE_DIR",
      ].includes(name.toUpperCase())).sort(),
      [
        "CLOUDFLARE_CF_FETCH_ENABLED", "CLOUDFLARE_CF_FETCH_PATH", "MINIFLARE_CACHE_DIR",
        "TERUISI_LOCAL_WRANGLER_STATE_DIR",
      ],
    );
    assert.throws(
      () => immutableMiniflareCacheBinding({
        runtimeRoot: runtime,
        releaseRoot: path.join(root, "foreign-release"),
        persistRoot,
      }),
      /release\/runtime 边界无效/,
    );
    assert.throws(
      () => immutableMiniflareCacheBinding({ runtimeRoot: runtime, releaseRoot, persistRoot: runtime }),
      /cache 与 Wrangler persist root 不得相互包含/,
    );
    assert.throws(
      () => immutableMiniflareCacheBinding({
        runtimeRoot: runtime,
        releaseRoot,
        persistRoot: path.join(cacheRoot, "nested-persist"),
      }),
      /cache 与 Wrangler persist root 不得相互包含/,
    );

    for (let start = 1; start <= 2; start += 1) {
      assert.deepEqual(
        await prepareImmutableMiniflareCacheDirectory({ runtimeRoot: runtime, releaseRoot, persistRoot }),
        cacheBinding,
      );
      await runProcess(process.execPath, [fakeWrangler], {
        cwd: releaseRoot,
        env: environment,
        label: `fake immutable Worker start ${start}`,
      });
      assert.deepEqual(await hashTree(nodeModulesRoot), expectedNodeModulesTree);
    }
    assert.equal(
      await readFile(path.join(cacheRoot, "cf.json"), "utf8"),
      '{"source":"miniflare-fixture"}\n',
    );
    await assert.rejects(lstat(path.join(nodeModulesRoot, ".mf")), (error: unknown) => (
      error instanceof Error && "code" in error && error.code === "ENOENT"
    ));
    await assert.rejects(lstat(maliciousCachePath), (error: unknown) => (
      error instanceof Error && "code" in error && error.code === "ENOENT"
    ));

    const fileRuntime = path.join(root, "file-runtime");
    const fileRelease = path.join(fileRuntime, "releases", "20260830T130001Z-fedcba9876543210");
    await mkdir(path.join(fileRuntime, "cache"), { recursive: true });
    await writeFile(resolveImmutableMiniflareCacheDirectory(fileRuntime), "not-a-directory", "utf8");
    await assert.rejects(
      prepareImmutableMiniflareCacheDirectory({ runtimeRoot: fileRuntime, releaseRoot: fileRelease, persistRoot }),
      /Miniflare cache必须是实体目录/,
    );

    const reparseRuntime = path.join(root, "reparse-runtime");
    const reparseRelease = path.join(reparseRuntime, "releases", "20260830T130002Z-fedcba9876543210");
    const outside = path.join(root, "outside-cache");
    await mkdir(path.join(reparseRuntime, "cache"), { recursive: true });
    await mkdir(outside);
    await symlink(outside, resolveImmutableMiniflareCacheDirectory(reparseRuntime), "junction");
    await assert.rejects(
      prepareImmutableMiniflareCacheDirectory({ runtimeRoot: reparseRuntime, releaseRoot: reparseRelease, persistRoot }),
      /Miniflare cache必须是实体目录|重解析点|真实路径不一致/,
    );

    const fileLinkRuntime = path.join(root, "file-link-runtime");
    const fileLinkRelease = path.join(fileLinkRuntime, "releases", "20260830T130003Z-fedcba9876543210");
    const releaseFile = path.join(fileLinkRelease, "node_modules", "immutable.txt");
    await mkdir(path.dirname(releaseFile), { recursive: true });
    await writeFile(releaseFile, "immutable\n", "utf8");
    const fileLinkBinding = await prepareImmutableMiniflareCacheDirectory({
      runtimeRoot: fileLinkRuntime,
      releaseRoot: fileLinkRelease,
      persistRoot,
    });
    try {
      await symlink(releaseFile, fileLinkBinding.cacheFile, "file");
    } catch (error) {
      if (!(error && typeof error === "object" && "code" in error && error.code === "EPERM")) throw error;
      // Creating a file symlink requires Developer Mode on some Windows hosts.
      // A directory junction at the exact file leaf exercises the same
      // release-directed reparse/write-through boundary without that privilege.
      await symlink(path.dirname(releaseFile), fileLinkBinding.cacheFile, "junction");
    }
    await assert.rejects(
      prepareImmutableMiniflareCacheDirectory({ runtimeRoot: fileLinkRuntime, releaseRoot: fileLinkRelease, persistRoot }),
      /Miniflare cf\.json 必须是普通非链接文件|重解析点|真实路径不一致/,
    );

    const hardLinkRuntime = path.join(root, "hard-link-runtime");
    const hardLinkRelease = path.join(hardLinkRuntime, "releases", "20260830T130004Z-fedcba9876543210");
    const hardLinkReleaseFile = path.join(hardLinkRelease, "node_modules", "immutable.txt");
    await mkdir(path.dirname(hardLinkReleaseFile), { recursive: true });
    await writeFile(hardLinkReleaseFile, "immutable\n", "utf8");
    const hardLinkBinding = await prepareImmutableMiniflareCacheDirectory({
      runtimeRoot: hardLinkRuntime,
      releaseRoot: hardLinkRelease,
      persistRoot,
    });
    await link(hardLinkReleaseFile, hardLinkBinding.cacheFile);
    await assert.rejects(
      prepareImmutableMiniflareCacheDirectory({ runtimeRoot: hardLinkRuntime, releaseRoot: hardLinkRelease, persistRoot }),
      /Miniflare cf\.json 必须保持单链接文件身份/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("public entrypoints converge on the immutable Worker start engine", async () => {
  const packageJson = JSON.parse(await readFile("package.json", "utf8"));
  for (const name of ["dev", "start", "start:local-worker"]) {
    assert.match(packageJson.scripts[name], /worker-local-service\.ps1.+-Action Start/);
    assert.doesNotMatch(packageJson.scripts[name], /vinext|wrangler|start-local-worker/);
  }
  const batch = await readFile("运行项目.bat", "utf8");
  const panel = await readFile("tools/operations-system-control.ps1", "utf8");
  const service = await readFile("tools/worker-local-service.ps1", "utf8");
  assert.match(batch, /operations-system-control\.ps1" -Action Start -Open/);
  assert.doesNotMatch(batch, /worker-local-service\.ps1|npm\s+(?:install|run\s+dev)|vinext\s+(?:dev|start)|wrangler\s+dev/);
  assert.match(batch, /Node\.js 24\.x/);
  assert.match(batch, /唯一启动总控/);
  assert.match(panel, /teruisi-operations-system-control-v2/);
  assert.match(panel, /tools\\worker-local-service\.ps1/);
  assert.doesNotMatch(panel, /start-local-worker\.mjs|--build|vinext\s+(?:dev|start)|wrangler\s+dev/);
  assert.match(service, /Ensure-DjangoSystemReady/);
});

test("PowerShell 5 service contract is parseable and never claims an unknown port is stopped", async () => {
  const service = await readFile("tools/worker-local-service.ps1", "utf8");
  const supervisor = await readFile("tools/worker-local-runtime-supervisor.mjs", "utf8");
  assert.match(service, /Local\\TERUISI\.Worker\.LocalService\.v1/);
  assert.match(service, /stale_or_invalid_receipt/);
  assert.match(service, /Get-StartupOwnershipDecision/);
  assert.match(service, /StartupOwnershipGraceMilliseconds = 3000/);
  assert.match(service, /supervisor exited before readiness: exit=/);
  assert.match(service, /did not establish stable exact 3000\/5791 ownership/);
  assert.doesNotMatch(service, /failed to reach exact 3000\/5791 ownership within 90 seconds/);
  assert.match(service, /Save-StartupShortcutAtomic/);
  assert.match(service, /Refusing to overwrite an unverified Startup shortcut/);
  assert.match(service, /wildcard, IPv6, or non-loopback listener/);
  assert.match(service, /Get-NetTCPConnection -State Listen -LocalPort \$Port/);
  assert.doesNotMatch(service, /Get-NetTCPConnection -State Listen -LocalAddress \$WorkerHost/);
  assert.match(service, /if \(\$childTicks -lt \$parentTicks\) \{ continue \}/);
  assert.match(service, /Test-ExactHelperProcess/);
  assert.match(service, /Get-ControlledStopSweep/);
  assert.match(service, /stableEmptySweeps -ge 3/);
  assert.match(service, /PID \$id was reused while stopping/);
  assert.doesNotMatch(service, /NpmCommand|--npm-command|npm\.cmd/);
  assert.match(service, /\$outerErrorActionPreference = \$ErrorActionPreference/);
  assert.match(service, /\$ErrorActionPreference = "Continue"/);
  assert.match(service, /\$nodeExitCode = \$LASTEXITCODE/);
  assert.doesNotMatch(supervisor, /isLocalWorkerPortInUse/);
  assert.match(supervisor, /while \(await probeAnyLocalPort\(port\)\)/);
  assert.match(supervisor, /if \(await probeAnyLocalPort\(workerPort\)\)/);
  assert.match(supervisor, /if \(await probeAnyLocalPort\(workerHelperPort\)\)/);

  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-status-"));
  try {
    const result = spawnSync("powershell.exe", [
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.resolve("tools/worker-local-service.ps1"),
      "-Action", "Status", "-RuntimeRoot", runtime, "-AllowTestRuntimeRoot", "-Json",
    ], { encoding: "utf8", windowsHide: true });
    assert.equal(result.status, 0, result.stderr);
    const status = JSON.parse(result.stdout.trim());
    assert.equal(status.version, "teruisi-local-worker-status-v1");
    assert.equal(status.releaseId, null);
    assert.equal(status.manifestSha256, null);
    assert.equal(status.supervisorProcessId, null);
    if (status.state === "stopped") assert.equal(status.portProcessId, null);
    else assert.equal(status.state, "foreign_or_ambiguous");

    const releaseId = "20260830T130000Z-fedcba9876543210";
    const releaseRoot = path.join(runtime, "releases", releaseId);
    await mkdir(releaseRoot, { recursive: true });
    const manifest = withPayloadSha256({
      version: "teruisi-local-worker-release-v1",
      releaseId,
      createdAt: "2026-08-30T13:00:00.000Z",
      source: {},
      build: {},
      runtime: {
        runtimeRootPathSha256: windowsPathSha256(runtime),
        releaseRootPathSha256: windowsPathSha256(releaseRoot),
        protectedSourceRoot: "D:\\运营管理系统",
        protectedSourceRootPathSha256: windowsPathSha256("D:\\运营管理系统"),
        helperMode: "supervisor_managed_immutable_bundle",
        helperHost: "127.0.0.1",
        helperPort: 5791,
        helperMutableRoot: "D:\\运营管理系统",
        helperMutableRootPathSha256: windowsPathSha256("D:\\运营管理系统"),
      },
      artifacts: {},
      processIdentity: {
        supervisorEntrypoint: "tools/worker-local-runtime-supervisor.mjs",
        serviceControl: "tools/worker-local-service.ps1",
        manifestFile: "deployment-manifest.json",
        processReceipt: "state/worker-process.json",
        processReceiptVersion: "teruisi-local-worker-process-v1",
        wranglerEntrypoint: "node_modules/wrangler/bin/wrangler.js",
        wranglerCliEntrypoint: "node_modules/wrangler/wrangler-dist/cli.js",
        fixedWranglerArguments: ["dev"],
        helperEntrypoint: "helper/tmall-workflow-helper.mjs",
        fixedHelperArguments: ["serve", "--port", "5791"],
      },
    }, "manifestPayloadSha256");
    const manifestPath = path.join(releaseRoot, "deployment-manifest.json");
    const manifestRaw = Buffer.from(`${canonicalJson(manifest)}\n`, "utf8");
    await writeFile(manifestPath, manifestRaw);
    const pointer = withPayloadSha256({
      version: "teruisi-local-worker-current-v1",
      releaseId,
      manifestRelativePath: `releases/${releaseId}/deployment-manifest.json`,
      manifestSha256: sha256Bytes(manifestRaw),
    }, "pointerPayloadSha256");
    await writeFile(path.join(runtime, "current-deployment.json"), `${canonicalJson(pointer)}\n`, "utf8");
    const identityResult = spawnSync("powershell.exe", [
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.resolve("tools/worker-local-service.ps1"),
      "-Action", "Status", "-RuntimeRoot", runtime, "-AllowTestRuntimeRoot", "-Json",
    ], { encoding: "utf8", windowsHide: true });
    assert.equal(identityResult.status, 0, identityResult.stderr);
    const identityStatus = JSON.parse(identityResult.stdout.trim());
    assert.equal(identityStatus.releaseId, releaseId);
    assert.equal(identityStatus.manifestSha256, sha256Bytes(manifestRaw));
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test("PowerShell startup ownership observation tolerates only a bounded transient anomaly", () => {
  const servicePath = path.resolve("tools/worker-local-service.ps1").replaceAll("'", "''");
  const fixture = `
. '${servicePath}' -FunctionsOnly -AllowTestRuntimeRoot
$started = [datetime]'2026-08-30T12:00:00Z'
$exact = Get-StartupOwnershipDecision ([pscustomobject]@{ State = 'exact_release' }) $null $started 3000
if ($exact.Action -cne 'Ready' -or $null -ne $exact.FirstAnomalyAt) { throw 'exact release was not accepted' }
$waiting = Get-StartupOwnershipDecision ([pscustomobject]@{ State = 'starting_exact_release' }) $started $started.AddSeconds(1) 3000
if ($waiting.Action -cne 'Wait' -or $null -ne $waiting.FirstAnomalyAt) { throw 'healthy startup did not reset anomaly state' }
$first = Get-StartupOwnershipDecision ([pscustomobject]@{ State = 'stale_or_invalid_receipt' }) $null $started 3000
if ($first.Action -cne 'Grace' -or $null -eq $first.FirstAnomalyAt) { throw 'first transient anomaly was not given bounded grace' }
$transient = Get-StartupOwnershipDecision ([pscustomobject]@{ State = 'foreign_or_ambiguous' }) $first.FirstAnomalyAt $started.AddMilliseconds(2999) 3000
if ($transient.Action -cne 'Grace') { throw 'bounded transient anomaly failed too early' }
$persistent = Get-StartupOwnershipDecision ([pscustomobject]@{ State = 'foreign_or_ambiguous' }) $first.FirstAnomalyAt $started.AddMilliseconds(3000) 3000
if ($persistent.Action -cne 'Fail') { throw 'persistent ownership anomaly was not failed closed' }
$unknown = Get-StartupOwnershipDecision ([pscustomobject]@{ State = 'unexpected' }) $null $started 3000
if ($unknown.Action -cne 'Fail') { throw 'unknown startup state was not failed closed' }
[Console]::Out.WriteLine('verified')
`;
  const result = spawnSync("powershell.exe", [
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", fixture,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "verified");
});

test("PowerShell process tree rejects PID-reuse creation inversions and unreadable identities", () => {
  const servicePath = path.resolve("tools/worker-local-service.ps1").replaceAll("'", "''");
  const fixture = `
. '${servicePath}' -FunctionsOnly -AllowTestRuntimeRoot
$root = [pscustomobject]@{ ProcessId = 100; ParentProcessId = 1; CreationDate = [datetime]'2026-08-30T12:00:00Z' }
$valid = [pscustomobject]@{ ProcessId = 101; ParentProcessId = 100; CreationDate = [datetime]'2026-08-30T12:00:01Z' }
$reused = [pscustomobject]@{ ProcessId = 102; ParentProcessId = 100; CreationDate = [datetime]'2026-08-30T11:59:59Z' }
$processes = @($root, $valid, $reused)
$tree = @(Get-ProcessTree 100 $processes)
if (($tree.Process.ProcessId -join ',') -cne '100,101') { throw 'PID reuse edge was admitted' }
if (Test-MonotonicAncestor 102 100 $processes) { throw 'creation inversion was accepted as ancestry' }
$bad = [pscustomobject]@{ ProcessId = 103; ParentProcessId = 100; CreationDate = 'not-a-creation-date' }
$failedClosed = $false
try { [void](Get-ProcessTree 100 @($root, $bad)) } catch { $failedClosed = $true }
if (-not $failedClosed) { throw 'unreadable creation identity did not fail closed' }
[Console]::Out.WriteLine('verified')
`;
  const result = spawnSync("powershell.exe", [
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", fixture,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "verified");
});

test("PowerShell Stop sweep follows a newly spawned exact helper lineage and rejects ambiguity", () => {
  const servicePath = path.resolve("tools/worker-local-service.ps1").replaceAll("'", "''");
  const fixture = `
. '${servicePath}' -FunctionsOnly -AllowTestRuntimeRoot
$node = Get-NodeExecutable
$releaseRoot = 'D:\\controlled worker\\releases\\20260830T130000Z-fedcba9876543210'
$manifestPath = Join-Path $releaseRoot 'deployment-manifest.json'
$sha = 'a' * 64
$supervisorPath = Join-Path $releaseRoot 'tools\\worker-local-runtime-supervisor.mjs'
$helperPath = Join-Path $releaseRoot 'helper\\tmall-workflow-helper.mjs'
$identity = [pscustomobject]@{
  ReleaseRoot = $releaseRoot
  Path = $manifestPath
  Sha256 = $sha
  Manifest = [pscustomobject]@{ processIdentity = [pscustomobject]@{
    fixedWranglerArguments = @('dev')
    fixedHelperArguments = @('serve', '--port', '5791')
    helperEntrypoint = 'helper/tmall-workflow-helper.mjs'
  } }
}
$nodeName = Split-Path -Leaf $node
$supervisor = [pscustomobject]@{
  ProcessId = 100; ParentProcessId = 1; CreationDate = [datetime]'2026-08-30T12:00:00Z'
  Name = $nodeName; ExecutablePath = $node
  CommandLine = ('"' + $node + '" "' + $supervisorPath + '" --manifest "' + $manifestPath + '" --approved-manifest-sha256 ' + $sha)
}
$oldHelper = [pscustomobject]@{
  ProcessId = 101; ParentProcessId = 100; CreationDate = [datetime]'2026-08-30T12:00:01Z'
  Name = $nodeName; ExecutablePath = $node
  CommandLine = ('"' + $node + '" "' + $helperPath + '" serve --port 5791')
}
$newHelper = [pscustomobject]@{
  ProcessId = 104; ParentProcessId = 100; CreationDate = [datetime]'2026-08-30T12:00:02Z'
  Name = $nodeName; ExecutablePath = $node
  CommandLine = ('"' + $node + '" "' + $helperPath + '" serve --port 5791')
}
$newChild = [pscustomobject]@{
  ProcessId = 105; ParentProcessId = 104; CreationDate = [datetime]'2026-08-30T12:00:03Z'
  Name = 'business-child.exe'; ExecutablePath = 'D:\\controlled child\\business-child.exe'; CommandLine = 'business-child.exe'
}
$cutoff = ([datetime]'2026-08-30T12:00:05Z').ToUniversalTime().Ticks
$sweep = Get-ControlledStopSweep $identity $supervisor @($supervisor, $oldHelper) @($newHelper, $newChild) $cutoff
if (($sweep.LiveEntries.Process.ProcessId -join ',') -cne '104,105') { throw 'new exact helper lineage was not admitted' }
if (($sweep.KnownSnapshots.ProcessId -join ',') -cne '100,101,104,105') { throw 'stop lineage ledger was incomplete' }

$reused = [pscustomobject]@{
  ProcessId = 101; ParentProcessId = 999; CreationDate = [datetime]'2026-08-30T12:00:04Z'
  Name = 'foreign.exe'; ExecutablePath = 'D:\\foreign.exe'; CommandLine = 'foreign.exe'
}
$reusedFailed = $false
try { [void](Get-ControlledStopSweep $identity $supervisor @($supervisor, $oldHelper) @($reused) $cutoff) }
catch { $reusedFailed = $_.Exception.Message -match 'reused' }
if (-not $reusedFailed) { throw 'reused helper PID was not rejected' }

$lateHelper = [pscustomobject]@{
  ProcessId = 106; ParentProcessId = 100; CreationDate = [datetime]'2026-08-30T12:00:06Z'
  Name = $nodeName; ExecutablePath = $node
  CommandLine = ('"' + $node + '" "' + $helperPath + '" serve --port 5791')
}
$lateFailed = $false
try { [void](Get-ControlledStopSweep $identity $supervisor @($supervisor) @($lateHelper) $cutoff) }
catch { $lateFailed = $_.Exception.Message -match 'after its termination fence' }
if (-not $lateFailed) { throw 'post-fence child was not rejected' }
[Console]::Out.WriteLine('verified')
`;
  const result = spawnSync("powershell.exe", [
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", fixture,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "verified");
});
