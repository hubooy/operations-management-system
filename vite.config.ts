import vinext from "vinext";
import { defineConfig } from "vite";
import hostingConfig from "./.openai/hosting.json";
import { sites } from "./build/sites-vite-plugin";

const { r2 } = hostingConfig;

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

const localBindingConfig = {
  main: "./worker/index.ts",
  compatibility_flags: ["nodejs_compat"],
  r2_buckets: r2
    ? [
        {
          binding: r2,
          bucket_name: "site-creator-r2",
        },
      ]
    : [],
  images: {
    binding: "IMAGES",
  },
  triggers: {
    crons: ["* * * * *"],
  },
};

export default defineConfig(async ({ command }) => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");

  return {
    // Keep build transforms isolated from the shared workspace cache so a new
    // deployment never reuses a stale client bundle. The dev server must keep
    // its optimizer cache under node_modules/.vite*: vinext's bundled
    // vite-plugin-commonjs only skips pre-bundled deps whose path contains
    // "node_modules/.vite", and wrapping them again yields a second default
    // export ("A module cannot have multiple default exports") on startup.
    cacheDir: command === "serve" ? "node_modules/.vite-sites-cache" : ".vite-sites-cache",
    server: {
      // 端口被占时直接失败，不要静默顺延到 3001/3002：上一轮 dev server 若被
      // Ctrl+Z 或 SIGTTIN 停在后台，它仍持有监听 socket，顺延只会让多个实例
      // 意外启动第二个开发服务。
      port: 3000,
      strictPort: true,
      watch: isCodexSeatbeltSandbox
        ? { useFsEvents: false, usePolling: true }
        : {
            ignored: ["**/.runtime/**", "**/.wrangler/**"],
          },
    },
    plugins: [
      vinext(),
      sites(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        config: localBindingConfig,
      }),
    ],
  };
});
