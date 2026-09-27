import { spawn, execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const runtime = path.join(root, ".runtime/preview");
const sessionFile = path.join(runtime, "session.json");
const dataFile = path.join(runtime, "data.json");
const children = new Set();
let stopping = false;
/** @param {Record<string, string | undefined>} source */
export function cleanEnvironment(source = process.env) {
    const allowed = /^(PATH|PATHEXT|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|LOCALAPPDATA|APPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|SYSTEMDRIVE|NUMBER_OF_PROCESSORS)$/i;
    return Object.fromEntries(Object.entries(source).filter(([key]) => allowed.test(key)));
}
export function validatePorts(port) {
    if (!Number.isInteger(port) || port < 3100 || port > 3900)
        throw new Error("Preview port must be 3100–3900");
    return { port, reader: port + 15000, writer: port + 15001, control: port + 10000 };
}
function safePaths() {
    if (!existsSync(path.join(root, ".git")) || !lstatSync(path.join(root, ".git")).isFile())
        throw new Error("请在独立 Git worktree 中使用预览入口");
    const branch = execFileSync("git", ["branch", "--show-current"], { cwd: root, encoding: "utf8", windowsHide: true }).trim();
    if (!branch.startsWith("codex/"))
        throw new Error("Preview requires a codex/* branch");
    for (const name of readdirSync(root)) {
        if (/^\.env($|\.)|^\.dev\.vars($|\.)/.test(name))
            throw new Error("预览 worktree 中存在环境文件；请使用未复制生产配置的独立 worktree");
    }
    for (const part of [root, path.join(root, "node_modules"), path.join(root, ".runtime"), runtime]) {
        if (existsSync(part) && lstatSync(part).isSymbolicLink())
            throw new Error("Preview directories cannot be links");
    }
    mkdirSync(runtime, { recursive: true });
    for (const entry of readdirSync(runtime)) {
        const stat = lstatSync(path.join(runtime, entry));
        if (stat.isSymbolicLink() || (stat.isFile() && stat.nlink !== 1))
            throw new Error("Preview state cannot be linked");
    }
    return branch;
}
function readJson(file) { return existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : null; }
function writeJson(file, value) { writeFileSync(file, JSON.stringify(value, null, 2) + "\n", { mode: 0o600 }); }
function launch(binary, args, env, ipc = false) {
    const child = spawn(binary, args, { cwd: root, env, windowsHide: true, stdio: ipc ? ["ignore", "inherit", "inherit", "ipc"] : ["ignore", "inherit", "inherit"] });
    children.add(child);
    child.once("exit", () => children.delete(child));
    return child;
}
async function run(binary, args, env) {
    const child = launch(binary, args, env);
    await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", code => code === 0 ? resolve() : reject(new Error(`${path.basename(binary)} failed (${code})`))); });
}
async function reserve(port) {
    const server = http.createServer();
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
    return server;
}
async function close(server) { await new Promise(resolve => server.close(resolve)); }
async function requestControl(action) {
    const session = readJson(sessionFile);
    if (!session)
        return null;
    try {
        const response = await fetch(`http://127.0.0.1:${session.control}/${action}`, { method: action === "stop" ? "POST" : "GET", headers: { authorization: `Bearer ${session.token}` }, signal: AbortSignal.timeout(2000) });
        if (!response.ok)
            throw new Error("Preview control identity mismatch");
        return await response.json();
    }
    catch (error) {
        if (error.cause?.code === "ECONNREFUSED")
            return null;
        throw error;
    }
}
async function shutdown(code = 0) {
    if (stopping)
        return;
    stopping = true;
    // Only live ChildProcess handles created by this invocation; no receipt PID killing.
    const pending = [...children].map(child => new Promise(resolve => {
        const timer = setTimeout(() => { child.kill(); resolve(); }, 10000);
        child.once("exit", () => { clearTimeout(timer); resolve(); });
        if (child.connected)
            child.send("stop");
        else
            child.kill();
    }));
    await Promise.all(pending);
    process.exit(typeof code === "number" ? code : 0);
}
async function ready(url) {
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
        try {
            const response = await fetch(url, { signal: AbortSignal.timeout(5000) });
            if (response.ok)
                return;
        }
        catch { /* startup */ }
        await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error(`Preview readiness timed out: ${url}`);
}
function freshName(prefix) { return `${prefix}-${Date.now()}-${randomBytes(4).toString("hex")}.sqlite3`; }
function privateDatabase(name) {
    if (typeof name !== "string" || !/^(demo|snapshot|restored)-[0-9]+-[0-9a-f]{8}\.sqlite3$/.test(name))
        throw new Error("Invalid private snapshot name");
    return path.join(runtime, name);
}
function sha256(file) { return createHash("sha256").update(readFileSync(file)).digest("hex"); }
export function validateSnapshot(name, manifest, digest) {
    if (!/^snapshot-[0-9]+-[0-9a-f]{8}\.sqlite3$/.test(name) || manifest?.fixture !== "synthetic-v1" || manifest?.file !== name || manifest?.sha256 !== digest) {
        throw new Error("快照名称、合成数据标记或 SHA-256 不一致，拒绝恢复");
    }
}
function openPreview(port) {
    if (!process.argv.includes("--open") || process.platform !== "win32")
        return;
    const chrome = [path.join(process.env.PROGRAMFILES ?? "C:/Program Files", "Google/Chrome/Application/chrome.exe"), path.join(process.env.LOCALAPPDATA ?? "", "Google/Chrome/Application/chrome.exe")].find(existsSync);
    if (chrome)
        spawn(chrome, [`http://127.0.0.1:${port}/?module=inventory`], { detached: true, stdio: "ignore", windowsHide: true }).unref();
}
async function main() {
    const action = process.argv[2] ?? "start";
    if (!["start", "status", "stop", "prepare", "snapshot", "restore"].includes(action))
        throw new Error("Use start/status/stop/prepare/snapshot/restore");
    const branch = safePaths();
    if (action === "status") {
        console.log(await requestControl(action) ?? { status: "stopped" });
        return;
    }
    if (action === "stop") {
        await requestControl("stop");
        for (let i = 0; i < 30; i++) {
            if (!await requestControl("status")) {
                console.log({ status: "stopped" });
                return;
            }
            await new Promise(resolve => setTimeout(resolve, 500));
        }
        throw new Error("预览尚未退出，请查看启动窗口；不会终止身份不明的进程");
    }
    const existing = await requestControl("status");
    if (existing) {
        if (action === "start") {
            console.log(existing);
            openPreview(Number(new URL(existing.url).port));
            return;
        }
        throw new Error("先运行 npm run preview:stop，再准备或恢复数据");
    }
    const ports = validatePorts(Number(process.env.TERUISI_PREVIEW_PORT ?? 3100));
    const control = await reserve(ports.control);
    let state = "preparing";
    const token = randomBytes(32).toString("hex");
    control.on("request", (req, res) => {
        if (req.headers.authorization !== `Bearer ${token}`) {
            res.writeHead(403).end();
            return;
        }
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({ status: state, url: `http://127.0.0.1:${ports.port}`, branch }));
        if (req.url === "/stop" && req.method === "POST")
            void shutdown();
    });
    writeJson(sessionFile, { ...ports, token });
    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);
    const env = { ...cleanEnvironment(), PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1",
        TERUISI_DJANGO_ENVIRONMENT: "development", TERUISI_DJANGO_PROCESS_ROLE: "development",
        // Market/netshop's signed sales consumer must never use its default
        // service port. The second isolated server handles internal read calls
        // so the public reader's request pool cannot wait on itself.
        TERUISI_DJANGO_SALES_READER_BASE_URL: `http://127.0.0.1:${ports.writer}`,
        DJANGO_DEBUG: "false", DJANGO_ALLOWED_HOSTS: "127.0.0.1,localhost",
        DJANGO_SECRET_KEY: randomBytes(48).toString("hex"), TERUISI_DJANGO_INTERNAL_SECRET: randomBytes(48).toString("hex") };
    const python = path.join(runtime, process.platform === "win32" ? "venv/Scripts/python.exe" : "venv/bin/python");
    try {
        if (!existsSync(path.join(root, "node_modules/vite/package.json"))) {
            console.log("正在安装此 worktree 的前端依赖…");
            if (process.platform === "win32")
                await run("cmd.exe", ["/d", "/c", "npm ci --no-audit --no-fund"], env);
            else
                await run("npm", ["ci", "--no-audit", "--no-fund"], env);
        }
        if (!existsSync(python)) {
            console.log("正在准备独立 Python 环境（首次运行需要安装依赖）…");
            await run(process.platform === "win32" ? "python" : "python3", ["-m", "venv", path.join(runtime, "venv")], env);
        }
        const requirements = readFileSync(path.join(root, "backend/requirements.txt"), "utf8");
        const installed = readJson(path.join(runtime, "requirements.json"));
        if (installed?.requirements !== requirements) {
            await run(python, ["-m", "pip", "install", "--disable-pip-version-check", "-r", "backend/requirements.txt"], env);
            writeJson(path.join(runtime, "requirements.json"), { requirements });
        }
        let data = readJson(dataFile);
        if (action === "restore" || action === "snapshot") {
            const source = action === "snapshot" ? data?.database : process.argv[3];
            if (action === "restore")
                validateSnapshot(source, readJson(privateDatabase(source) + ".json"), sha256(privateDatabase(source)));
            const target = freshName(action === "snapshot" ? "snapshot" : "restored");
            await run(python, ["tools/preview/data.py", "copy", privateDatabase(target), "--source", privateDatabase(source)], env);
            if (action === "restore")
                writeJson(dataFile, { database: target, source, fixture: "synthetic-v1" });
            else
                writeJson(privateDatabase(target) + ".json", { file: target, fixture: "synthetic-v1", sha256: sha256(privateDatabase(target)) });
            console.log(`预览数据已${action === "snapshot" ? "保存" : "恢复"}：${target}`);
            await close(control);
            return;
        }
        if (!data || action === "prepare") {
            const database = freshName("demo");
            await run(python, ["tools/preview/data.py", "seed", privateDatabase(database)], env);
            data = { database, fixture: "synthetic-v1" };
            writeJson(dataFile, data);
        }
        if (action === "prepare") {
            console.log("演示数据已准备；旧版本保留。启动：npm run preview:isolated");
            await close(control);
            return;
        }
        env.TERUISI_DJANGO_SQLITE_PATH = privateDatabase(data.database);
        if (!existsSync(env.TERUISI_DJANGO_SQLITE_PATH))
            throw new Error("预览数据库缺失；使用 preview:prepare 准备新版本");
        // This independent, explicitly marked fixture contains only synthetic
        // n8n metadata. Never inherit a production n8n path into the preview.
        env.TERUISI_N8N_STATUS_DATABASE_PATH = privateDatabase(freshName("demo"));
        await run(python, ["tools/preview/n8n_status.py", env.TERUISI_N8N_STATUS_DATABASE_PATH], env);
        // Detect conflicts before starting any persistent children.
        for (const port of [ports.port, ports.reader, ports.writer]) {
            const probe = await reserve(port);
            await close(probe);
        }
        await run(python, ["backend/manage.py", "migrate", "--no-input", "--verbosity", "0"], env);
        const bindings = { TERUISI_LOCAL_DIRECT_ACCESS: "true", TERUISI_RUNTIME_ENV: "development", TERUISI_DJANGO_INTERNAL_SECRET: env.TERUISI_DJANGO_INTERNAL_SECRET,
            PREVIEW_READER_URL: `http://127.0.0.1:${ports.reader}`, PREVIEW_WRITER_URL: `http://127.0.0.1:${ports.writer}` };
        for (const domain of ["SALES", "ERP", "FINANCE", "NETSHOP", "MARKET", "PRODUCTS", "INVENTORY", "WORKFLOW", "CUSTOMER_SERVICE", "ACCESS_CONTROL", "AI", "BI"]) {
            bindings[`TERUISI_DJANGO_${domain}_MODE`] = "django";
            bindings[`TERUISI_DJANGO_${domain}_READER_BASE_URL`] = bindings.PREVIEW_READER_URL;
            if (domain !== "BI")
                bindings[`TERUISI_DJANGO_${domain}_WRITER_BASE_URL`] = bindings.PREVIEW_WRITER_URL;
        }
        writeJson(path.join(runtime, "wrangler.json"), { name: "teruisi-isolated-preview", main: "../../tools/preview/worker.ts", compatibility_date: "2026-05-01", compatibility_flags: ["nodejs_compat"], vars: bindings, r2_buckets: [{ binding: "SALES_IMPORT_FILES", bucket_name: "preview-only" }] });
        for (const port of [ports.reader, ports.writer]) {
            const child = launch(python, ["-m", "waitress", `--listen=127.0.0.1:${port}`, "--threads=4", "teruisi_backend.wsgi:application"], { ...env, PYTHONPATH: path.join(root, "backend") });
            child.once("exit", () => { if (!stopping) {
                console.error("Preview backend exited");
                void shutdown(1);
            } });
            await ready(`http://127.0.0.1:${port}/health/live`);
        }
        const vite = launch(process.execPath, ["tools/preview/server.mjs"], { ...env, TERUISI_PREVIEW_SESSION: "1", CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV: "false", WRANGLER_SEND_METRICS: "false", WRANGLER_WRITE_LOGS: "false" }, true);
        vite.once("exit", () => { if (!stopping)
            void shutdown(1); });
        await ready(`http://127.0.0.1:${ports.port}/`);
        state = "ready";
        console.log(`\n演示预览已就绪：http://127.0.0.1:${ports.port}/?module=inventory\n分支：${branch}\n保存代码自动更新；Ctrl+C 或 npm run preview:stop 停止。`);
        openPreview(ports.port);
    }
    catch (error) {
        console.error(error.message);
        await shutdown(1);
    }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
    await main();
