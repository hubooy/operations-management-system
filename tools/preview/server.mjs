import { createServer } from "vite";
if (process.env.TERUISI_PREVIEW_SESSION !== "1" || !process.send)
    throw new Error("Use preview launcher");
const server = await createServer({ configFile: "tools/preview/vite.config.ts" });
await server.listen();
server.printUrls();
let closing = false;
async function stop() {
    if (closing)
        return;
    closing = true;
    await server.close();
    process.exit(0);
}
process.on("message", message => { if (message === "stop")
    void stop(); });
process.on("disconnect", stop);
process.on("SIGTERM", stop);
