"""Thin wrapper around the Docker SDK with error handling."""

import docker
from docker.errors import DockerException, NotFound, APIError
from typing import Optional


class DockerClientError(Exception):
    pass


class ContainerNotFoundError(DockerClientError):
    pass


class DockerConnectionError(DockerClientError):
    pass


class DockerClient:
    def __init__(self):
        try:
            self._client = docker.from_env()
            self._client.ping()
        except DockerException as e:
            raise DockerConnectionError(
                f"Cannot connect to Docker daemon. Is Docker running? ({e})"
            )

    def get_container(self, name_or_id: str):
        try:
            return self._client.containers.get(name_or_id)
        except NotFound:
            raise ContainerNotFoundError(f"Container not found: {name_or_id!r}")
        except APIError as e:
            raise DockerClientError(f"Docker API error: {e}")

    def list_containers(self, all: bool = False, filters: Optional[dict] = None):
        try:
            return self._client.containers.list(all=all, filters=filters or {})
        except APIError as e:
            raise DockerClientError(f"Docker API error: {e}")

    def list_networks(self):
        try:
            return self._client.networks.list()
        except APIError as e:
            raise DockerClientError(f"Docker API error: {e}")

    @property
    def api(self):
        return self._client.api
