"""MCP server resources."""

import dataclasses
import os.path
import urllib.parse
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from falcon_mcp_server import errors


@dataclasses.dataclass
class Resource:
    fetch: Callable[[], Awaitable[str | bytes]]
    uri: str
    name: str
    title: str
    description: str
    media_type: str

    def marshal(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'uri': self.uri,
            'name': self.name,
            'title': self.title,
            'description': self.description,
            'mimeType': self.media_type,
        }
        return data


class Resources:
    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._resource_templates: dict[str, Any] = {}

    def add_simple_resource(
        self,
        uri: str,
        media_type: str,
        data: str | bytes,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        async def fetch() -> str | bytes:
            return data

        if not name:
            name = os.path.basename(urllib.parse.urlparse(uri).path)

        description = description or title or f'{name} resource'
        title = title or name

        self._resources[uri] = Resource(
            fetch,
            uri,
            name=name,
            title=title,
            description=description,
            media_type=media_type,
        )

    async def list_resources(
        self, cursor: str | None = None
    ) -> dict[str, Any]:
        resources = list(
            resource.marshal() for resource in self._resources.values()
        )
        return {'resources': resources}

    async def read_resource(
        self,
        uri: str,
    ) -> dict[str, str]:
        if (resource := self._resources.get(uri)) is None:
            raise errors.RPCInvalidParam('Resource not found')

        try:
            data = await resource.fetch()
            return {
                'uri': resource.uri,
                'mimeType': resource.media_type,
                'text': data,
            }
        except Exception as ex:
            raise errors.RPCInternalError(
                f'Error reading resource {uri}: {type(ex).__name__}: {ex}'
            )
