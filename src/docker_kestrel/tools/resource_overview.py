"""Tool: resource_overview — ranked resource usage across all containers."""

import re
from typing import Optional
from pydantic import BaseModel

from ..docker_client import DockerClient, DockerClientError
from ..analysis.container import analyze_stats


class ResourceOverviewInput(BaseModel):
    running_only: bool = True
    name_pattern: Optional[str] = None


async def resource_overview(args: ResourceOverviewInput) -> dict:
    client = DockerClient()

    try:
        containers = client.list_containers(all=not args.running_only)
    except DockerClientError as e:
        return {"error": str(e)}

    if args.name_pattern:
        try:
            rx = re.compile(args.name_pattern, re.IGNORECASE)
            containers = [
                c for c in containers
                if rx.search(c.name)
            ]
        except re.error as e:
            return {"error": f"Invalid name_pattern regex: {e}"}

    results = []
    for container in containers:
        name = container.name
        status = container.status
        entry: dict = {"name": name, "status": status}

        if status == "running":
            try:
                raw_stats = client.api.stats(container.id, stream=False, one_shot=True)
                stats = analyze_stats(raw_stats)
                entry.update(stats)

                flags = []
                if stats.get("memory_percent", 0) >= 90:
                    flags.append(f"memory at {stats['memory_percent']}% of limit")
                if stats.get("cpu_percent", 0) >= 80:
                    flags.append(f"CPU at {stats['cpu_percent']}%")
                if flags:
                    entry["anomalies"] = flags
            except Exception:
                entry["stats_error"] = "could not retrieve stats"
        else:
            entry["cpu_percent"] = None
            entry["memory_usage_mb"] = None

        results.append(entry)

    # Sort running containers by memory usage descending, then append non-running
    running = [r for r in results if r.get("status") == "running" and r.get("memory_usage_mb") is not None]
    other = [r for r in results if r not in running]
    running.sort(key=lambda x: x.get("memory_usage_mb") or 0, reverse=True)

    total_mem = sum(r.get("memory_usage_mb") or 0 for r in running)
    total_cpu = sum(r.get("cpu_percent") or 0 for r in running)

    anomalies = [r["name"] for r in running if r.get("anomalies")]

    return {
        "containers": running + other,
        "totals": {
            "running": len(running),
            "total": len(results),
            "total_memory_mb": round(total_mem, 2),
            "total_cpu_percent": round(total_cpu, 2),
        },
        "anomalies": anomalies,
    }
