# docker-kestrel

An MCP server that provides diagnostic intelligence for Docker environments. No Prometheus, no Datadog — just the daemon and smart analysis.

Connect docker-kestrel to Claude Desktop, Claude Code, Cursor, or any MCP-compatible client and ask plain-English questions about your containers.

## What it does

Existing Docker MCP servers wrap CLI commands (`start`, `stop`, `list`). docker-kestrel *reasons* about what's going on:

- **`diagnose_container`** — full diagnostic report: OOM detection, crash loop detection, health check analysis, resource snapshot, log summary
- **`resource_overview`** — ranked resource usage across all containers with anomaly flags
- **`network_map`** — Docker network topology, container IPs, port bindings, conflict detection
- **`compose_drift`** — compare a `docker-compose.yml` against running containers to find drift
- **`log_analysis`** — structured log analysis: error counts, pattern grouping, first/last occurrence

## Quick start

> **Not yet published to PyPI.** Use the local-path instructions below until the package is released. The PyPI instructions are here for reference post-publish.

### Before PyPI publish (local install)

```bash
git clone https://github.com/markalovell-git/docker-kestrel
cd docker-kestrel
uv sync
```

**Claude Desktop** — `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "docker-kestrel": {
      "command": "uvx",
      "args": ["--from", "/path/to/docker-kestrel", "docker-kestrel"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add docker-kestrel uvx --from /path/to/docker-kestrel docker-kestrel
```

**Cursor** — `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per-project):

```json
{
  "mcpServers": {
    "docker-kestrel": {
      "command": "uvx",
      "args": ["--from", "/path/to/docker-kestrel", "docker-kestrel"]
    }
  }
}
```

### After PyPI publish

```bash
# Install with uv
uv tool install docker-kestrel

# Or run directly without installing
uvx docker-kestrel
```

**Claude Desktop** — `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "docker-kestrel": {
      "command": "uvx",
      "args": ["docker-kestrel"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add docker-kestrel uvx docker-kestrel
```

**Cursor** — `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per-project):

```json
{
  "mcpServers": {
    "docker-kestrel": {
      "command": "uvx",
      "args": ["docker-kestrel"]
    }
  }
}
```

## Example queries

Once connected, you can ask your AI client:

- *"Why is my api container unhealthy?"*
- *"Which container is using the most memory?"*
- *"Why can't container A reach container B?"*
- *"Has anything drifted from my docker-compose.yml?"*
- *"Summarize the errors in the worker container's logs"*

## Development

```bash
git clone https://github.com/markalovell-git/docker-kestrel
cd docker-kestrel
uv sync --dev
uv pip install -e .

# Run unit tests (no Docker daemon required)
uv run pytest tests/test_analysis.py -v

# Run integration tests (requires Docker)
uv run pytest tests/test_integration.py -v
```

## Requirements

- Python 3.11+
- Docker daemon running and accessible (via `/var/run/docker.sock`)

## License

MIT
