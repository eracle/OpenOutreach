"""CLI entrypoint for OpenOutreach MCP server."""
from __future__ import annotations

import logging

from mcp_server.server import serve_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    serve_forever()


if __name__ == "__main__":
    main()

