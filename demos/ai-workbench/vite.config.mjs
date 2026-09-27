import { defineConfig } from "vite";
import { demoMiddleware } from "./server.mjs";

export default defineConfig({
  css: { postcss: { plugins: [] } },
  plugins: [
    {
      name: "synthetic-ai-demo",
      configureServer(server) {
        server.middlewares.use(demoMiddleware);
      },
    },
  ],
  server: {
    host: "127.0.0.1",
    port: 3100,
    strictPort: true,
    headers: {
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  },
});
