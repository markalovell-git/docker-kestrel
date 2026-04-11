"""DockerScope MCP server — tool registration and entrypoint."""

import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from .tools.diagnose_container import diagnose_container, DiagnoseContainerInput
from .tools.resource_overview import resource_overview, ResourceOverviewInput
from .tools.network_map import network_map, NetworkMapInput
from .tools.compose_drift import compose_drift, ComposeDriftInput
from .tools.log_analysis import log_analysis, LogAnalysisInput

app = Server("dockerscope")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="diagnose_container",
            description=(
                "Full diagnostic report for a single container: health state, exit code analysis, "
                "OOM detection, restart loop detection, resource snapshot, and log summary. "
                "Use this when a container is unhealthy, restarting, or behaving unexpectedly."
            ),
            inputSchema=DiagnoseContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="resource_overview",
            description=(
                "Ranked resource usage (CPU, memory, network, disk I/O) across all containers. "
                "Flags containers approaching their memory limit or high CPU usage. "
                "Use this to answer 'what is consuming all my resources?'"
            ),
            inputSchema=ResourceOverviewInput.model_json_schema(),
        ),
        types.Tool(
            name="network_map",
            description=(
                "Enumerates Docker networks and their connected containers with IPs and aliases. "
                "Identifies port bindings and conflicts. "
                "Use this to debug container-to-container connectivity issues."
            ),
            inputSchema=NetworkMapInput.model_json_schema(),
        ),
        types.Tool(
            name="compose_drift",
            description=(
                "Compares a docker-compose.yml file against running containers to detect drift: "
                "environment variable mismatches, port differences, services not running, "
                "and containers running that aren't in the Compose file."
            ),
            inputSchema=ComposeDriftInput.model_json_schema(),
        ),
        types.Tool(
            name="log_analysis",
            description=(
                "Structured analysis of container logs: counts errors/warnings/tracebacks, "
                "groups recurring patterns, identifies first/last occurrence. "
                "Optionally filter by a search pattern. Returns a summary, not a raw dump."
            ),
            inputSchema=LogAnalysisInput.model_json_schema(),
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "diagnose_container":
            result = await diagnose_container(DiagnoseContainerInput(**arguments))
        elif name == "resource_overview":
            result = await resource_overview(ResourceOverviewInput(**arguments))
        elif name == "network_map":
            result = await network_map(NetworkMapInput(**arguments))
        elif name == "compose_drift":
            result = await compose_drift(ComposeDriftInput(**arguments))
        elif name == "log_analysis":
            result = await log_analysis(LogAnalysisInput(**arguments))
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": f"Tool execution failed: {type(e).__name__}: {e}"}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
