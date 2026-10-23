import { StdioServerTransport } from "@modelcontextprotocol/server/stdio";
import { loadConfig } from "./config.js";
import { publicError } from "./errors.js";
import { createHttpServer } from "./http.js";
import { createMcpServer } from "./server.js";

async function main() {
  const config = loadConfig();
  if (config.transport === "stdio") {
    const server = createMcpServer(config);
    await server.connect(new StdioServerTransport());
    const shutdown = () => { void server.close().finally(() => process.exit(0)); };
    process.once("SIGTERM", shutdown);
    process.once("SIGINT", shutdown);
  } else {
    const http = createHttpServer(config);
    http.on("error", () => {
      console.error("MCP HTTP 启动失败，请检查绑定地址和端口。");
      process.exitCode = 1;
    });
    http.listen(config.port, config.host, () => {
      console.error(`领星 MCP HTTP 已监听 ${config.host}:${config.port}`);
    });
    const shutdown = () => { http.close(() => process.exit(0)); };
    process.once("SIGTERM", shutdown);
    process.once("SIGINT", shutdown);
  }
}

void main().catch((error) => {
  console.error(JSON.stringify(publicError(error)));
  process.exitCode = 1;
});
