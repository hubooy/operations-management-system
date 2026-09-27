// Synthetic Worker lifecycle only: no application imports, credentials or APIs.
import { createServer } from "node:http";
import { parentPort, workerData } from "node:worker_threads";

const server = createServer((request, response) => {
  if (request.headers["x-teruisi-helper-slot-token"] !== workerData.token) {
    response.writeHead(403); response.end(); return;
  }
  response.end("ok");
  if (request.url === "/finish") server.close(() => parentPort.postMessage({ type: "finished", clean: true }));
  if (request.url === "/crash") setTimeout(() => process.exit(1), 10);
  if (request.url === "/throw") {
    parentPort.postMessage({type:"diagnostic_phase",phase:"master_browser_connect"});
    parentPort.postMessage({type:"diagnostic_phase",phase:"https://secret.invalid/?token=DO_NOT_LOG"});
    setTimeout(() => { throw new TypeError("synthetic secret=DO_NOT_LOG https://secret.invalid/"); }, 10);
  }
});
server.listen(0, "127.0.0.1", () => parentPort.postMessage({ type: "ready", port: server.address().port }));
