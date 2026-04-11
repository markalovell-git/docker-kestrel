"""Tool: diagnose_container — full diagnostic report for a single container."""

from typing import Optional
from pydantic import BaseModel

from ..docker_client import DockerClient, ContainerNotFoundError, DockerClientError
from ..analysis.container import (
    analyze_container_state,
    analyze_logs,
    analyze_stats,
    parse_uptime,
)


class DiagnoseContainerInput(BaseModel):
    container: str
    log_lines: int = 100


async def diagnose_container(args: DiagnoseContainerInput) -> dict:
    client = DockerClient()

    try:
        container = client.get_container(args.container)
    except ContainerNotFoundError as e:
        return {"error": str(e)}
    except DockerClientError as e:
        return {"error": str(e)}

    inspect = container.attrs
    state = inspect.get("State", {})
    name = inspect.get("Name", "").lstrip("/")

    diagnosis = analyze_container_state(inspect)

    log_summary = {}
    if state.get("Status") != "created":
        try:
            raw_logs = container.logs(tail=args.log_lines, timestamps=False).decode(
                "utf-8", errors="replace"
            )
            log_summary = analyze_logs(raw_logs)
        except Exception:
            log_summary = {"error": "could not retrieve logs"}

    stats_snapshot = {}
    if state.get("Status") == "running":
        try:
            raw_stats = client.api.stats(container.id, stream=False, one_shot=True)
            stats_snapshot = analyze_stats(raw_stats)

            mem_pct = stats_snapshot.get("memory_percent", 0)
            if mem_pct >= 90:
                diagnosis["suggestions"].append(
                    f"Memory usage is {mem_pct}% of limit — approaching OOM territory"
                )
                diagnosis["evidence"].append(f"memory_percent: {mem_pct}")
        except Exception:
            stats_snapshot = {"error": "could not retrieve stats"}

    return {
        "container": name,
        "status": state.get("Status", "unknown"),
        "uptime": parse_uptime(state.get("StartedAt", "")),
        "restart_count": inspect.get("RestartCount", 0),
        "image": inspect.get("Config", {}).get("Image", "unknown"),
        "diagnosis": diagnosis,
        "log_summary": log_summary,
        "resource_snapshot": stats_snapshot,
    }
