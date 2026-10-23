from __future__ import annotations

import asyncio
import json
import sys

import uvicorn
from dotenv import load_dotenv

from .config import load_config
from .errors import public_error
from .http import create_http_app
from .server import create_mcp_server


async def _run() -> None:
    load_dotenv()
    config = load_config()
    server = create_mcp_server(config)
    if config.transport == "stdio":
        await server.run_stdio_async()
        return

    app = create_http_app(config, server)
    uvicorn_config = uvicorn.Config(
        app,
        host=config.host,
        port=config.port,
        log_config=None,
        access_log=False,
    )
    await uvicorn.Server(uvicorn_config).serve()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(json.dumps(public_error(exc), ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
