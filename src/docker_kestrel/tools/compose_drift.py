"""Tool: compose_drift — compare docker-compose.yml against running containers."""

import os
import re
from typing import Optional
from pydantic import BaseModel
import yaml

from ..docker_client import DockerClient, DockerClientError

_SENSITIVE_PATTERN = re.compile(
    r"(TOKEN|PASSWORD|PASSWD|SECRET|KEY|PRIVATE|CREDENTIAL|AUTH|API_KEY|CERT)",
    re.IGNORECASE,
)

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::-[^}]*)?\}")


def _redact(key: str, value) -> str:
    if value and _SENSITIVE_PATTERN.search(key):
        return "[redacted]"
    return value


def _load_env_file(compose_path: str) -> dict[str, str]:
    """Load .env file from the same directory as the compose file."""
    env_file = os.path.join(os.path.dirname(compose_path), ".env")
    env = {}
    if not os.path.isfile(env_file):
        return env
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _resolve(value: str, env: dict[str, str]) -> str:
    """Substitute ${VAR} and ${VAR:-default} references using env dict."""
    def replace(m):
        var_name = m.group(1)
        full_match = m.group(0)
        default_match = re.search(r"\$\{[^}:]+:-([^}]*)\}", full_match)
        default = default_match.group(1) if default_match else None
        return env.get(var_name, default if default is not None else full_match)
    return _ENV_VAR_PATTERN.sub(replace, value)


class ComposeDriftInput(BaseModel):
    compose_file: str = "docker-compose.yml"


def _normalize_env(env_input) -> dict[str, Optional[str]]:
    """Accept both list ('KEY=VAL') and dict formats."""
    if isinstance(env_input, dict):
        return env_input
    result = {}
    for item in env_input or []:
        if "=" in item:
            k, v = item.split("=", 1)
            result[k] = v
        else:
            result[item] = None
    return result


def _normalize_ports(ports_input) -> list[str]:
    normalized = []
    for p in ports_input or []:
        if isinstance(p, dict):
            target = p.get("target", "")
            published = p.get("published", "")
            normalized.append(f"{published}:{target}" if published else str(target))
        else:
            normalized.append(str(p))
    return normalized


async def compose_drift(args: ComposeDriftInput) -> dict:
    compose_path = os.path.abspath(args.compose_file)

    if not os.path.isfile(compose_path):
        return {"error": f"Compose file not found: {compose_path}"}

    try:
        with open(compose_path) as f:
            compose_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return {"error": f"Failed to parse Compose file: {e}"}

    services = compose_data.get("services", {})
    if not services:
        return {"error": "No services found in Compose file"}

    env = _load_env_file(compose_path)

    client = DockerClient()
    try:
        running_containers = client.list_containers(all=True)
    except DockerClientError as e:
        return {"error": str(e)}

    # Build lookup: container name → inspect data
    # Docker Compose typically names containers as <project>_<service>_1 or <project>-<service>-1
    container_lookup: dict[str, dict] = {}
    for c in running_containers:
        labels = c.attrs.get("Config", {}).get("Labels", {}) or {}
        service_name = labels.get("com.docker.compose.service")
        if service_name:
            container_lookup[service_name] = c.attrs
        container_lookup[c.name.lstrip("/")] = c.attrs

    drift_report = []
    not_running = []
    unknown_containers = []

    declared_service_names = set(services.keys())

    for service_name, service_def in services.items():
        declared_image = _resolve(service_def.get("image", f"{service_name}:latest"), env)
        declared_env = {k: (_resolve(v, env) if v else v) for k, v in _normalize_env(service_def.get("environment", {})).items()}
        declared_ports = [_resolve(p, env) for p in _normalize_ports(service_def.get("ports", []))]
        declared_volumes = [str(v) for v in (service_def.get("volumes") or [])]

        container_attrs = container_lookup.get(service_name)
        if not container_attrs:
            not_running.append(service_name)
            continue

        state = container_attrs.get("State", {})
        actual_image = container_attrs.get("Config", {}).get("Image", "")
        actual_env_list = container_attrs.get("Config", {}).get("Env") or []
        actual_env = _normalize_env(actual_env_list)

        diffs = []

        # Image drift
        if declared_image and actual_image and not actual_image.startswith(declared_image.split(":")[0]):
            diffs.append({
                "field": "image",
                "declared": declared_image,
                "running": actual_image,
            })

        # Environment drift (only check keys that are declared)
        for key, declared_val in declared_env.items():
            actual_val = actual_env.get(key)
            if declared_val is not None and actual_val != declared_val:
                diffs.append({
                    "field": f"env.{key}",
                    "declared": _redact(key, declared_val),
                    "running": _redact(key, actual_val),
                })

        # Port drift
        actual_ports = container_attrs.get("NetworkSettings", {}).get("Ports") or {}
        actual_port_strs = list({
            f"{b.get('HostPort', '')}:{container_port.split('/')[0]}"
            for container_port, bindings in actual_ports.items()
            if bindings
            for b in bindings
        })

        for dp in declared_ports:
            if dp not in actual_port_strs:
                diffs.append({
                    "field": "ports",
                    "declared": dp,
                    "running": actual_port_strs or "no bindings",
                })

        drift_report.append({
            "service": service_name,
            "status": state.get("Status", "unknown"),
            "drifted": len(diffs) > 0,
            "diffs": diffs,
        })

    # Find running containers not in compose
    compose_label_key = "com.docker.compose.service"
    for c in running_containers:
        labels = c.attrs.get("Config", {}).get("Labels", {}) or {}
        if labels.get("com.docker.compose.project") and labels.get(compose_label_key) not in declared_service_names:
            unknown_containers.append(c.name.lstrip("/"))

    total_drifted = sum(1 for r in drift_report if r["drifted"])

    return {
        "compose_file": compose_path,
        "services_declared": len(services),
        "services_running": len(drift_report),
        "services_not_running": not_running,
        "services_with_drift": total_drifted,
        "unknown_containers": unknown_containers,
        "drift": drift_report,
    }
