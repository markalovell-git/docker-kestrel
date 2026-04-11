"""Tool: log_analysis — structured analysis of container logs."""

from typing import Optional
from pydantic import BaseModel

from ..docker_client import DockerClient, ContainerNotFoundError, DockerClientError
from ..analysis.container import analyze_logs


class LogAnalysisInput(BaseModel):
    container: str
    tail: int = 500
    search_pattern: Optional[str] = None


async def log_analysis(args: LogAnalysisInput) -> dict:
    client = DockerClient()

    try:
        container = client.get_container(args.container)
    except ContainerNotFoundError as e:
        return {"error": str(e)}
    except DockerClientError as e:
        return {"error": str(e)}

    try:
        raw_logs = container.logs(tail=args.tail, timestamps=True).decode(
            "utf-8", errors="replace"
        )
    except Exception as e:
        return {"error": f"Could not retrieve logs: {e}"}

    name = container.name.lstrip("/")

    if args.search_pattern:
        import re
        try:
            rx = re.compile(args.search_pattern, re.IGNORECASE)
            filtered_lines = [l for l in raw_logs.splitlines() if rx.search(l)]
            matched_text = "\n".join(filtered_lines)
            summary = analyze_logs(matched_text)
            summary["search_pattern"] = args.search_pattern
            summary["matched_lines"] = len(filtered_lines)
            summary["sample"] = filtered_lines[:20]
        except re.error as e:
            return {"error": f"Invalid search_pattern regex: {e}"}
    else:
        summary = analyze_logs(raw_logs)

    summary["container"] = name
    summary["lines_analyzed"] = args.tail

    return summary
