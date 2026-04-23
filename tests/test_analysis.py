"""Unit tests for the analysis layer — no Docker daemon required."""

import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from docker_kestrel.analysis.container import (
    analyze_container_state,
    analyze_logs,
    analyze_stats,
    parse_uptime,
    map_exit_code,
)
from docker_kestrel.tools.compose_drift import _redact, _normalize_env, _normalize_ports, _load_env_file, _resolve, compose_drift, ComposeDriftInput


# --- _redact ---

def test_redact_token():
    assert _redact("TRUEFACE_TOKEN", "supersecret") == "[redacted]"

def test_redact_password():
    assert _redact("POSTGRES_PASSWORD", "tf_password") == "[redacted]"

def test_redact_secret():
    assert _redact("MY_SECRET", "abc123") == "[redacted]"

def test_redact_api_key():
    assert _redact("API_KEY", "key-value") == "[redacted]"

def test_redact_preserves_non_sensitive():
    assert _redact("DB_NAME", "tf_db") == "tf_db"

def test_redact_preserves_none():
    assert _redact("POSTGRES_PASSWORD", None) is None

def test_redact_case_insensitive():
    assert _redact("postgres_password", "secret") == "[redacted]"


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


def test_analyze_stats_cpu():
    result = analyze_stats(_make_stats(
        cpu_total=1_000_000, precpu_total=900_000,
        system_cpu=100_000_000, presystem_cpu=99_000_000,
    ))
    # cpu_delta=100000, system_delta=1000000, num_cpus=4 → 40%
    assert result["cpu_percent"] == pytest.approx(40.0, abs=0.1)


def test_analyze_stats_zero_system_delta():
    result = analyze_stats(_make_stats(system_cpu=100_000_000, presystem_cpu=100_000_000))
    assert result["cpu_percent"] == 0.0


def test_analyze_stats_zero_mem_limit():
    result = analyze_stats(_make_stats(mem_limit=0))
    assert result["memory_limit_mb"] is None
    assert result["memory_percent"] == 0.0


def test_analyze_stats_multiple_network_interfaces():
    stats = _make_stats()
    stats["networks"]["eth1"] = {"rx_bytes": 1024 * 1024, "tx_bytes": 0}
    result = analyze_stats(stats)
    assert result["net_rx_mb"] == pytest.approx(2.0, abs=0.01)


# --- analyze_container_state edge cases ---

def test_sigkill_without_oom():
    result = analyze_container_state(_make_inspect(status="exited", exit_code=137, oom_killed=False))
    assert result["primary_issue"] == "SIGKILL"
    assert "exit_code: 137" in result["evidence"]


def test_application_error():
    result = analyze_container_state(_make_inspect(status="exited", exit_code=1))
    assert result["primary_issue"] == "APPLICATION_ERROR"
    assert any("exit_code" in e for e in result["evidence"])


def test_stopped_status():
    result = analyze_container_state(_make_inspect(status="exited", exit_code=0))
    assert result["primary_issue"] == "STOPPED"


def test_health_check_starting():
    result = analyze_container_state(_make_inspect(health_status="starting"))
    assert result["primary_issue"] == "HEALTH_CHECK_FAILING"


def test_health_check_empty_log():
    data = _make_inspect(health_status="unhealthy")
    data["State"]["Health"]["Log"] = []
    result = analyze_container_state(data)
    assert result["primary_issue"] == "HEALTH_CHECK_FAILING"
    assert not any("health_check_output" in e for e in result["evidence"])


# --- analyze_logs additional patterns ---

def test_analyze_logs_case_insensitive():
    result = analyze_logs("error: something failed\nERROR: another failure")
    assert result["issues_found"]["error"]["count"] == 2


def test_analyze_logs_critical():
    result = analyze_logs("CRITICAL: disk full")
    assert "critical" in result["issues_found"]


def test_analyze_logs_go_panic():
    result = analyze_logs("panic: runtime error: index out of range")
    assert "go_panic" in result["issues_found"]


def test_analyze_logs_exception():
    result = analyze_logs("java.lang.NullPointerException\nCaused by: exception in thread main")
    assert "exception" in result["issues_found"]


def test_analyze_logs_oom_pattern():
    result = analyze_logs("OOMKilled: true")
    assert "oom" in result["issues_found"]


# --- _normalize_env ---

def test_normalize_env_dict():
    result = _normalize_env({"KEY": "val", "OTHER": "x"})
    assert result == {"KEY": "val", "OTHER": "x"}


def test_normalize_env_list():
    result = _normalize_env(["KEY=val", "OTHER=x"])
    assert result == {"KEY": "val", "OTHER": "x"}


def test_normalize_env_list_no_value():
    result = _normalize_env(["KEY_ONLY"])
    assert result == {"KEY_ONLY": None}


def test_normalize_env_none():
    result = _normalize_env(None)
    assert result == {}


# --- _normalize_ports ---

def test_normalize_ports_string():
    result = _normalize_ports(["8080:80", "5432:5432"])
    assert result == ["8080:80", "5432:5432"]


def test_normalize_ports_dict():
    result = _normalize_ports([{"published": "8080", "target": "80"}])
    assert result == ["8080:80"]


def test_normalize_ports_dict_no_published():
    result = _normalize_ports([{"target": "80"}])
    assert result == ["80"]


def test_normalize_ports_empty():
    result = _normalize_ports([])
    assert result == []


# --- _load_env_file ---

def test_load_env_file_parses_values():
    with tempfile.TemporaryDirectory() as d:
        env_path = os.path.join(d, ".env")
        with open(env_path, "w") as f:
            f.write("DB_PASSWORD=secret\nDB_PORT=5433\n# comment\n\nDB_NAME=mydb\n")
        compose_path = os.path.join(d, "docker-compose.yml")
        result = _load_env_file(compose_path)
    assert result == {"DB_PASSWORD": "secret", "DB_PORT": "5433", "DB_NAME": "mydb"}


def test_load_env_file_missing_returns_empty():
    result = _load_env_file("/nonexistent/docker-compose.yml")
    assert result == {}


def test_load_env_file_strips_quotes():
    with tempfile.TemporaryDirectory() as d:
        env_path = os.path.join(d, ".env")
        with open(env_path, "w") as f:
            f.write('KEY="quoted value"\n')
        result = _load_env_file(os.path.join(d, "docker-compose.yml"))
    assert result == {"KEY": "quoted value"}


# --- _resolve ---

def test_resolve_simple_var():
    assert _resolve("${DB_PORT}", {"DB_PORT": "5433"}) == "5433"


def test_resolve_with_default():
    assert _resolve("${MODE:-default}", {}) == "default"


def test_resolve_var_overrides_default():
    assert _resolve("${MODE:-default}", {"MODE": "production"}) == "production"


def test_resolve_missing_no_default_returns_original():
    assert _resolve("${MISSING}", {}) == "${MISSING}"


def test_resolve_plain_string_unchanged():
    assert _resolve("plainvalue", {"KEY": "val"}) == "plainvalue"


# --- compose_drift error paths ---

@pytest.mark.asyncio
async def test_compose_drift_file_not_found():
    result = await compose_drift(ComposeDriftInput(compose_file="/nonexistent/path/docker-compose.yml"))
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_compose_drift_invalid_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(": invalid: yaml: {{{")
        path = f.name
    try:
        result = await compose_drift(ComposeDriftInput(compose_file=path))
        assert "error" in result
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_compose_drift_no_services():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("version: '3'\n")
        path = f.name
    try:
        result = await compose_drift(ComposeDriftInput(compose_file=path))
        assert "error" in result
    finally:
        os.unlink(path)
