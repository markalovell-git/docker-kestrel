"""Tool: network_map — enumerate Docker networks and container connectivity."""

from typing import Optional
from pydantic import BaseModel

from ..docker_client import DockerClient, DockerClientError


class NetworkMapInput(BaseModel):
    network_name: Optional[str] = None


async def network_map(args: NetworkMapInput) -> dict:
    client = DockerClient()

    try:
        networks = client.list_networks()
    except DockerClientError as e:
        return {"error": str(e)}

    # Collect port bindings across all running containers to detect conflicts
    port_bindings: dict[str, list[str]] = {}
    try:
        containers = client.list_containers(all=False)
        for c in containers:
            inspect = c.attrs
            ports = inspect.get("NetworkSettings", {}).get("Ports", {}) or {}
            for container_port, host_bindings in ports.items():
                if not host_bindings:
                    continue
                for binding in host_bindings:
                    host_port = f"{binding.get('HostIp', '0.0.0.0')}:{binding.get('HostPort', '')}"
                    port_bindings.setdefault(host_port, []).append(c.name)
    except Exception:
        pass

    port_conflicts = {
        hp: names for hp, names in port_bindings.items() if len(names) > 1
    }

    network_map_data = []
    for network in networks:
        if args.network_name and network.name != args.network_name:
            continue

        attrs = network.attrs
        ipam = attrs.get("IPAM", {}).get("Config", [])
        subnet = ipam[0].get("Subnet") if ipam else None

        connected = []
        containers_in_net = attrs.get("Containers", {}) or {}
        for container_id, info in containers_in_net.items():
            connected.append({
                "name": info.get("Name", container_id[:12]),
                "ipv4": info.get("IPv4Address", "").split("/")[0],
                "ipv6": info.get("IPv6Address", "").split("/")[0] or None,
                "mac": info.get("MacAddress", None),
            })

        network_map_data.append({
            "name": network.name,
            "id": network.id[:12],
            "driver": attrs.get("Driver", "unknown"),
            "subnet": subnet,
            "internal": attrs.get("Internal", False),
            "containers": connected,
            "container_count": len(connected),
        })

    return {
        "networks": network_map_data,
        "port_bindings": port_bindings,
        "port_conflicts": port_conflicts,
        "notes": (
            "Containers on the same Docker network can reach each other by container name. "
            "Containers on different networks cannot communicate unless explicitly connected."
        ),
    }
