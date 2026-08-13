"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const port = Number(process.argv[2] || 4173);
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error("server port must be an integer between 1024 and 65535");
}
const origin = `http://127.0.0.1:${port}`;
const types = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".md", "text/markdown; charset=utf-8"]
]);

const server = http.createServer((request, response) => {
  try {
    const url = new URL(request.url, origin);
    const requested = url.pathname === "/demo/" ? "/demo/index.html" : url.pathname;
    const target = path.resolve(root, `.${decodeURIComponent(requested)}`);
    if (target !== root && !target.startsWith(`${root}${path.sep}`)) throw new Error("path escapes root");
    const stat = fs.statSync(target);
    if (!stat.isFile()) throw new Error("not a file");
    response.writeHead(200, {
      "Cache-Control": "no-store",
      "Content-Type": types.get(path.extname(target)) || "application/octet-stream",
      "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      "X-Commerce-Lab-Test-Server": "stage2-evidence-explorer-v1",
      "X-Content-Type-Options": "nosniff"
    });
    fs.createReadStream(target).pipe(response);
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
  }
});

server.listen(port, "127.0.0.1");
