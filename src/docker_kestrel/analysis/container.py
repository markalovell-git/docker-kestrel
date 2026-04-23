"""Analysis logic for container diagnostics."""

import re
from datetime import datetime, timezone
from typing import Optional


EXIT_CODE_MAP = {
    0: "clean exit",
    1: "application error",
    137: "OOM killed (SIGKILL)",
    139: "segmentation fault",
    143: "graceful shutdown (SIGTERM)",
    255: "exit status out of range",
}

LOG_ERROR_PATTERNS = [
    (re.compile(r"\bERROR\b", re.IGNORECASE), "error"),
    (re.compile(r"\bWARN(ING)?\b", re.IGNORECASE), "warning"),
    (re.compile(r"\bCRITICAL\b", re.IGNORECASE), "critical"),
    (re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE), "python_traceback"),
    (re.compile(r"\bpanic:", re.IGNORECASE), "go_panic"),
    (re.compile(r"\bfatal\b", re.IGNORECASE), "fatal"),
    (re.compile(r"\bexception\b", re.IGNORECASE), "exception"),
    (re.compile(r"OOMKilled|oom.kill", re.IGNORECASE), "oom"),
]


def parse_uptime(started_at: str) -> str:
    try:
        # Docker returns timestamps like "2024-01-01T00:00:00.000000000Z"
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m{seconds % 60}s"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h{minutes}m"
    except Exception:
        return "unknown"


def map_exit_code(code: int) -> str:
    return EXIT_CODE_MAP.get(code, f"exit code {code}")


def analyze_container_state(inspect_data: dict) -> dict:
    state = inspect_data.get("State", {})
    host_config = inspect_data.get("HostConfig", {})

    exit_code = state.get("ExitCode", 0)
    oom_killed = state.get("OOMKilled", False)
    restart_count = inspect_data.get("RestartCount", 0)
    status = state.get("Status", "unknown")
    started_at = state.get("StartedAt", "")
    health = state.get("Health", {})

    issues = []
    primary_issue = None
    suggestions = []
    evidence = []

    if oom_killed:
        primary_issue = "OOM_KILLED"
        mem_limit = host_config.get("Memory", 0)
        limit_mb = mem_limit // (1024 * 1024) if mem_limit else None
        detail = "Container killed by the kernel OOM killer."
        if limit_mb:
            detail += f" Memory limit is {limit_mb}MB."
        issues.append(detail)
        evidence.extend([f"oom_killed: true", f"exit_code: {exit_code}"])
        if limit_mb:
            evidence.append(f"memory_limit_mb: {limit_mb}")
        suggestions.extend([
            "Increase the container memory limit",
            "Profile application memory usage to find leaks",
        ])

    elif exit_code == 137 and not oom_killed:
        primary_issue = "SIGKILL"
        issues.append("Container received SIGKILL (force-stopped or out of memory).")
        evidence.append(f"exit_code: 137")
        suggestions.append("Check if the container was manually killed or hit an OOM condition")

    elif exit_code == 143:
        primary_issue = "SIGTERM"
        issues.append("Container received SIGTERM (graceful shutdown).")
        evidence.append(f"exit_code: 143")

    elif exit_code not in (0, None) and exit_code != 0:
        primary_issue = "APPLICATION_ERROR"
        issues.append(f"Container exited with code {exit_code}: {map_exit_code(exit_code)}.")
        evidence.append(f"exit_code: {exit_code}")
        suggestions.append("Check container logs for the error that caused the exit")

    if restart_count > 3:
        if not primary_issue:
            primary_issue = "CRASH_LOOP"
        issues.append(f"Container has restarted {restart_count} times — likely in a crash loop.")
        evidence.append(f"restart_count: {restart_count}")
        suggestions.append("Inspect logs across restart boundaries to find the recurring failure")

    health_status = health.get("Status", "")
    if health_status in ("unhealthy", "starting"):
        if not primary_issue:
            primary_issue = "HEALTH_CHECK_FAILING"
        log = health.get("Log", [])
        last_result = log[-1] if log else {}
        output = last_result.get("Output", "").strip()
        issues.append(f"Health check is {health_status}.")
        if output:
            evidence.append(f"health_check_output: {output[:200]}")
        suggestions.append("Check the health check command and its exit code")

    if not primary_issue:
        if status == "running":
            primary_issue = "HEALTHY"
        elif status == "exited":
            primary_issue = "STOPPED"
        else:
            primary_issue = status.upper()

    return {
        "primary_issue": primary_issue,
        "detail": " ".join(issues) if issues else f"Container is {status}.",
        "evidence": evidence,
        "suggestions": suggestions,
    }


def analyze_logs(log_text: str) -> dict:
    lines = log_text.splitlines()
    counts: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}

    for line in lines:
        for pattern, label in LOG_ERROR_PATTERNS:
            if pattern.search(line):
                counts[label] = counts.get(label, 0) + 1
                if label not in first_seen:
                    first_seen[label] = line[:120]
                last_seen[label] = line[:120]

    summary = {}
    for label, count in sorted(counts.items(), key=lambda x: -x[1]):
        summary[label] = {
            "count": count,
            "first": first_seen.get(label, ""),
            "last": last_seen.get(label, ""),
        }

    return {
        "total_lines": len(lines),
        "issues_found": summary,
    }


def analyze_stats(stats: dict) -> dict:
    """Parse raw Docker stats API response into useful numbers."""
    cpu_delta = stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0) - \
                stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
    system_delta = stats.get("cpu_stats", {}).get("system_cpu_usage", 0) - \
                   stats.get("precpu_stats", {}).get("system_cpu_usage", 0)
    num_cpus = len(stats.get("cpu_stats", {}).get("cpu_usage", {}).get("percpu_usage") or [1])

    cpu_percent = 0.0
    if system_delta > 0 and cpu_delta > 0:
        cpu_percent = round((cpu_delta / system_delta) * num_cpus * 100.0, 2)

    mem = stats.get("memory_stats", {})
    mem_usage = mem.get("usage", 0) - mem.get("stats", {}).get("cache", 0)
    mem_limit = mem.get("limit", 0)
    mem_percent = round((mem_usage / mem_limit) * 100, 2) if mem_limit else 0.0

    net_rx, net_tx = 0, 0
    for iface in stats.get("networks", {}).values():
        net_rx += iface.get("rx_bytes", 0)
        net_tx += iface.get("tx_bytes", 0)

    blk_read, blk_write = 0, 0
    for entry in stats.get("blkio_stats", {}).get("io_service_bytes_recursive") or []:
        if entry.get("op") == "read":
            blk_read += entry.get("value", 0)
        elif entry.get("op") == "write":
            blk_write += entry.get("value", 0)

    return {
        "cpu_percent": cpu_percent,
        "memory_usage_mb": round(mem_usage / (1024 * 1024), 2),
        "memory_limit_mb": round(mem_limit / (1024 * 1024), 2) if mem_limit else None,
        "memory_percent": mem_percent,
        "net_rx_mb": round(net_rx / (1024 * 1024), 3),
        "net_tx_mb": round(net_tx / (1024 * 1024), 3),
        "blk_read_mb": round(blk_read / (1024 * 1024), 3),
        "blk_write_mb": round(blk_write / (1024 * 1024), 3),
    }
