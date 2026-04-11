"""Unit tests for the analysis layer — no Docker daemon required."""

import pytest
from dockerscope.analysis.container import (
    analyze_container_state,
    analyze_logs,
    analyze_stats,
    parse_uptime,
    map_exit_code,
)


# --- parse_uptime ---

def test_parse_uptime_returns_unknown_on_bad_input():
    assert parse_uptime("not-a-date") == "unknown"


def test_parse_uptime_seconds():
    from datetime import datetime, timezone, timedelta
    started = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    result = parse_uptime(started)
    assert result.endswith("s")


def test_parse_uptime_minutes():
    from datetime import datetime, timezone, timedelta
    started = (datetime.now(timezone.utc) - timedelta(minutes=5, seconds=30)).isoformat()
    result = parse_uptime(started)
    assert "m" in result


# --- map_exit_code ---

def test_map_exit_code_known():
    assert "OOM" in map_exit_code(137)
    assert "SIGTERM" in map_exit_code(143)
    assert "clean" in map_exit_code(0)


def test_map_exit_code_unknown():
    result = map_exit_code(42)
    assert "42" in result


# --- analyze_container_state ---

def _make_inspect(status="running", exit_code=0, oom_killed=False, restart_count=0,
                  health_status=None, mem_limit=0):
    data = {
        "State": {
            "Status": status,
            "ExitCode": exit_code,
            "OOMKilled": oom_killed,
            "StartedAt": "2024-01-01T00:00:00Z",
        },
        "RestartCount": restart_count,
        "HostConfig": {"Memory": mem_limit},
        "Config": {},
    }
    if health_status:
        data["State"]["Health"] = {
            "Status": health_status,
            "Log": [{"Output": "health check failed: connection refused"}],
        }
    return data


def test_healthy_running_container():
    result = analyze_container_state(_make_inspect())
    assert result["primary_issue"] == "HEALTHY"
    assert result["suggestions"] == []


def test_oom_killed_detected():
    result = analyze_container_state(_make_inspect(
        status="exited", exit_code=137, oom_killed=True, mem_limit=512 * 1024 * 1024
    ))
    assert result["primary_issue"] == "OOM_KILLED"
    assert "512" in result["detail"]
    assert any("memory" in s.lower() for s in result["suggestions"])


def test_crash_loop_detected():
    result = analyze_container_state(_make_inspect(exit_code=1, restart_count=5))
    assert "CRASH_LOOP" in result["primary_issue"] or result["primary_issue"] == "APPLICATION_ERROR"
    # At minimum restart count should be in evidence
    assert any("restart_count" in e for e in result["evidence"])


def test_health_check_failing():
    result = analyze_container_state(_make_inspect(health_status="unhealthy"))
    assert result["primary_issue"] == "HEALTH_CHECK_FAILING"
    assert any("health_check_output" in e for e in result["evidence"])


def test_sigterm_exit():
    result = analyze_container_state(_make_inspect(status="exited", exit_code=143))
    assert result["primary_issue"] == "SIGTERM"


# --- analyze_logs ---

def test_analyze_logs_empty():
    result = analyze_logs("")
    assert result["total_lines"] == 0
    assert result["issues_found"] == {}


def test_analyze_logs_errors():
    logs = "\n".join([
        "INFO starting up",
        "ERROR failed to connect",
        "ERROR retry attempt 2",
        "WARN memory pressure",
        "Traceback (most recent call last):",
        "  File foo.py",
    ])
    result = analyze_logs(logs)
    assert result["issues_found"]["error"]["count"] == 2
    assert result["issues_found"]["warning"]["count"] == 1
    assert result["issues_found"]["python_traceback"]["count"] == 1


def test_analyze_logs_total_lines():
    logs = "line1\nline2\nline3"
    result = analyze_logs(logs)
    assert result["total_lines"] == 3


# --- analyze_stats ---

def _make_stats(cpu_total=1_000_000, precpu_total=900_000,
                system_cpu=100_000_000, presystem_cpu=99_000_000,
                mem_usage=200 * 1024 * 1024, mem_limit=512 * 1024 * 1024):
    return {
        "cpu_stats": {
            "cpu_usage": {
                "total_usage": cpu_total,
                "percpu_usage": [0, 0, 0, 0],
            },
            "system_cpu_usage": system_cpu,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": precpu_total},
            "system_cpu_usage": presystem_cpu,
        },
        "memory_stats": {
            "usage": mem_usage,
            "limit": mem_limit,
            "stats": {"cache": 0},
        },
        "networks": {
            "eth0": {"rx_bytes": 1024 * 1024, "tx_bytes": 512 * 1024},
        },
        "blkio_stats": {
            "io_service_bytes_recursive": [
                {"op": "read", "value": 10 * 1024 * 1024},
                {"op": "write", "value": 5 * 1024 * 1024},
            ]
        },
    }


def test_analyze_stats_memory():
    result = analyze_stats(_make_stats())
    assert result["memory_usage_mb"] == pytest.approx(200.0, abs=1)
    assert result["memory_limit_mb"] == pytest.approx(512.0, abs=1)
    assert result["memory_percent"] == pytest.approx(39.06, abs=1)


def test_analyze_stats_network():
    result = analyze_stats(_make_stats())
    assert result["net_rx_mb"] == pytest.approx(1.0, abs=0.01)
    assert result["net_tx_mb"] == pytest.approx(0.5, abs=0.01)


def test_analyze_stats_blkio():
    result = analyze_stats(_make_stats())
    assert result["blk_read_mb"] == pytest.approx(10.0, abs=0.01)
    assert result["blk_write_mb"] == pytest.approx(5.0, abs=0.01)
