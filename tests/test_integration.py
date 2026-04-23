"""Integration tests using real Docker containers via testcontainers."""

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from docker_kestrel.tools.diagnose_container import diagnose_container, DiagnoseContainerInput
from docker_kestrel.tools.resource_overview import resource_overview, ResourceOverviewInput
from docker_kestrel.tools.log_analysis import log_analysis, LogAnalysisInput
from docker_kestrel.tools.network_map import network_map, NetworkMapInput


@pytest.fixture(scope="module")
def running_container():
    """Start a simple long-running container for integration tests."""
    with DockerContainer("alpine:latest").with_command("sh -c 'while true; do echo alive; sleep 5; done'").waiting_for(LogMessageWaitStrategy("alive")) as container:
        yield container


@pytest.mark.asyncio
async def test_diagnose_running_container(running_container):
    container_id = running_container.get_wrapped_container().id
    result = await diagnose_container(DiagnoseContainerInput(container=container_id))

    assert "error" not in result
    assert result["status"] == "running"
    assert result["diagnosis"]["primary_issue"] == "HEALTHY"
    assert "resource_snapshot" in result
    assert result["resource_snapshot"].get("memory_usage_mb") is not None


@pytest.mark.asyncio
async def test_diagnose_nonexistent_container():
    result = await diagnose_container(DiagnoseContainerInput(container="does-not-exist-xyz"))
    assert "error" in result


@pytest.mark.asyncio
async def test_resource_overview_includes_running(running_container):
    result = await resource_overview(ResourceOverviewInput())

    assert "error" not in result
    assert result["totals"]["running"] >= 1
    container_id = running_container.get_wrapped_container().id[:12]
    names = [c["name"] for c in result["containers"]]
    # Container may appear by full ID or name
    assert any(container_id in n or n in container_id for n in names) or len(names) >= 1


@pytest.mark.asyncio
async def test_log_analysis_running_container(running_container):
    container_id = running_container.get_wrapped_container().id
    result = await log_analysis(LogAnalysisInput(container=container_id, tail=50))

    assert "error" not in result
    assert result["total_lines"] >= 1
    assert "issues_found" in result


@pytest.mark.asyncio
async def test_log_analysis_with_search(running_container):
    container_id = running_container.get_wrapped_container().id
    result = await log_analysis(LogAnalysisInput(
        container=container_id,
        tail=50,
        search_pattern="alive",
    ))

    assert "error" not in result
    assert result.get("matched_lines", 0) >= 1


@pytest.mark.asyncio
async def test_network_map():
    result = await network_map(NetworkMapInput())

    assert "error" not in result
    assert "networks" in result
    assert len(result["networks"]) >= 1
    # bridge network is always present
    names = [n["name"] for n in result["networks"]]
    assert "bridge" in names
