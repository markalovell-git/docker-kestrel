"""CLI entrypoint for DockerScope MCP server."""

import asyncio
import sys


def main():
    from .server import main as server_main
    asyncio.run(server_main())


if __name__ == "__main__":
    main()
