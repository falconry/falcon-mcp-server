# SPDX-FileCopyrightText: 2026-present Vytautas Liuolia <vytautas.liuolia@gmail.com>
#
# SPDX-License-Identifier: Apache-2.0

"""MCP server resources."""

import base64
import dataclasses
import inspect
import re
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import falcon

from falcon_mcp_server import errors
from falcon_mcp_server.context import Context
from falcon_mcp_server.logger import log
from falcon_mcp_server.server.introspection import find_context_param


@dataclasses.dataclass
class Resource:
    method: Callable[..., Awaitable[str | bytes]]
    uri: str
    name: str
    title: str
    description: str
    media_type: str
    context_param: str | None = None
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)

    def marshal(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'uri': self.uri,
            'name': self.name,
            'title': self.title,
            'description': self.description,
            'mimeType': self.media_type,
        }
        if self.meta:
            data['_meta'] = self.meta
        return data


class Resources:
    # NOTE(vytas): RFC 3986, 3. Syntax Components.
    #   URI = scheme ":" hier-part [ "?" query ] [ "#" fragment ]
    #   Past the scheme, this only checks that the characters are the ones
    #   allowed in a URI, not how its components are structured.
    _URI = re.compile(
        r'[A-Za-z][A-Za-z0-9+.-]*:'
        r"(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})*"
    )

    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._resource_templates: dict[str, Any] = {}

    def __len__(self) -> int:
        return len(self._resources)

    def add_resource(
        self,
        uri: str,
        media_type: str,
        resource: Callable[..., Awaitable[str | bytes]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        # NOTE(vytas): MCP Resources, 6.4. Custom URI Schemes.
        #   Custom URI schemes MUST be in accordance with RFC3986.
        if not self._URI.fullmatch(uri):
            raise ValueError(f'Resource {uri=} is not a valid URI (RFC 3986).')

        if uri in self._resources:
            raise ValueError(
                f'Resource with the {uri=} is already registered.'
            )

        if not name:
            # NOTE(vytas): The name defaults to the last segment of a URI
            #   that looks like scheme://[...], e.g., README.md for
            #   file:///project/README.md, or settings for config://settings.
            name = uri.partition('://')[2].rpartition('/')[2]
            if not name:
                raise ValueError(
                    f'Cannot derive a resource name from the {uri=}; '
                    f'pass the name explicitly.'
                )

        description = (
            description
            or inspect.getdoc(resource)
            or title
            or f'{name} resource'
        )
        title = title or name

        self._resources[uri] = Resource(
            resource,
            uri,
            name=name,
            title=title,
            description=description,
            media_type=media_type,
            context_param=find_context_param(resource),
            meta=meta or {},
        )

    def add_simple_resource(
        self,
        uri: str,
        media_type: str,
        data: str | bytes,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        async def read() -> str | bytes:
            return data

        self.add_resource(
            uri,
            media_type,
            read,
            name=name,
            title=title,
            description=description,
            meta=meta,
        )

    async def list_resources(
        self, cursor: str | None = None
    ) -> dict[str, Any]:
        resources = list(
            resource.marshal() for resource in self._resources.values()
        )
        return {'resources': resources}

    async def list_resource_templates(
        self, cursor: str | None = None
    ) -> dict[str, Any]:
        # TODO(vytas): Resource templates are not supported yet; the list is
        #   still served, since clients list them along with the resources.
        return {'resourceTemplates': []}

    async def read_resource(
        self, context: Context, uri: str
    ) -> dict[str, Any]:
        if (resource := self._resources.get(uri)) is None:
            raise errors.RPCInvalidParam('Resource not found')

        kwargs = {}
        if resource.context_param is not None:
            kwargs[resource.context_param] = context

        try:
            data = await resource.method(**kwargs)
        except (errors.RPCError, falcon.HTTPError):
            # NOTE(vytas): These are assumed to be raised intentionally.
            raise
        except Exception:
            # NOTE(vytas): The details of an unexpected exception may be
            #   sensitive, so they are logged, but not sent to the client.
            log.exception(f'Error reading resource {uri}')
            raise errors.RPCInternalError(f'Error reading resource {uri}')

        contents: dict[str, Any] = {
            'uri': resource.uri,
            'mimeType': resource.media_type,
        }
        # NOTE(vytas): MCP Resources, 8. Security Considerations.
        #   Binary data MUST be properly encoded.
        if isinstance(data, bytes):
            contents['blob'] = base64.b64encode(data).decode()
        else:
            contents['text'] = data
        # NOTE(vytas): MCP Apps, e.g., read the CSP of a ui:// view from the
        #   _meta of its contents.
        if resource.meta:
            contents['_meta'] = resource.meta
        return contents
