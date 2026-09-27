import { readFileSync } from "node:fs";
import path from "node:path";
import vinext from "vinext";
import { defineConfig } from "vite";
import { cloudflare } from "@cloudflare/vite-plugin";
export default defineConfig(({ command }) => {
    if (command !== "serve" || process.env.TERUISI_PREVIEW_SESSION !== "1") {
        throw new Error("Use npm run preview:isolated in an isolated worktree");
    }
    const config = JSON.parse(readFileSync(".runtime/preview/session.json", "utf8"));
    return {
        envDir: path.resolve(".runtime/preview"),
        cacheDir: "node_modules/.vite-preview-cache",
        define: { "import.meta.env.VITE_TERUISI_PREVIEW": JSON.stringify("true") },
        server: {
            host: "127.0.0.1", port: config.port, strictPort: true,
            watch: { ignored: ["**/.runtime/**", "**/.wrangler/**"] },
            headers: {
                "Content-Security-Policy": `connect-src 'self' ws://127.0.0.1:${config.port}; frame-src 'none'; form-action 'none'; img-src 'self' data: blob:; object-src 'none'`,
            },
        },
        plugins: [vinext(), cloudflare({
                configPath: path.resolve(".runtime/preview/wrangler.json"),
                viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
                inspectorPort: false, remoteBindings: false, tunnel: false,
                persistState: { path: path.resolve(".runtime/preview/r2") },
            })],
    };
});
