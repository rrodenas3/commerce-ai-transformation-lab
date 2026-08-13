"use strict";

const { spawn } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");

const port = 42973;
const expectedIdentity = "stage2-evidence-explorer-v1";

const probe = () => new Promise((resolve) => {
  const request = http.get(`http://127.0.0.1:${port}/demo/`, (response) => {
    response.resume();
    resolve(response.headers["x-commerce-lab-test-server"] === expectedIdentity);
  });
  request.on("error", () => resolve(false));
  request.setTimeout(500, () => {
    request.destroy();
    resolve(false);
  });
});

module.exports = async () => {
  const server = spawn(
    process.execPath,
    [path.join(__dirname, "server.js"), String(port)],
    { stdio: "ignore", windowsHide: true }
  );
  let exited = false;
  server.once("exit", () => { exited = true; });

  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (exited) throw new Error("browser test server exited before readiness");
    if (await probe()) {
      return async () => {
        if (!exited) server.kill();
      };
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!exited) server.kill();
  throw new Error("browser test server did not become ready");
};
