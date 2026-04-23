"""CLI entrypoint for docker-kestrel MCP server."""

import asyncio


def main():
    from .server import main as server_main
    asyncio.run(server_main())


if __name__ == "__main__":
    main()
