"""
MCP authorization, with the end point acting as an OAuth 2.1 resource server.
See also: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization.
"""

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import falcon
from falcon.asgi import Request
from falcon.asgi import Response

from falcon_mcp_server.constants import WELL_KNOWN_RESOURCE_METADATA
from falcon_mcp_server.context import Context

Authenticator = Callable[[Context], Awaitable[bool]]


class Authorization:
    def __init__(
        self,
        authenticator: Authenticator | None = None,
        resource_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.authenticator = authenticator
        self.resource_metadata = resource_metadata

    @staticmethod
    def _quote(value: str) -> str:
        # NOTE(vytas): RFC 9110, 5.6.4. Quoted Strings.
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _origin(req: Request) -> str:
        return f'{req.forwarded_scheme}://{req.forwarded_host}'

    def _challenge(self, req: Request) -> str:
        params = []
        # NOTE(vytas): RFC 6750, 3.1. Error Codes.
        #   If the request lacks any authentication information (...), the
        #   resource server SHOULD NOT include an error code or other error
        #   information.
        if req.get_header('Authorization'):
            params.append('error="invalid_token"')

        # NOTE(vytas): MCP Authorization Server Discovery, 2. Protected
        #   Resource Metadata Discovery Requirements.
        #   WWW-Authenticate Header: Include the resource metadata URL in the
        #   WWW-Authenticate HTTP header under resource_metadata when returning
        #   401 Unauthorized responses.
        if self.resource_metadata is not None:
            url = self._origin(req) + WELL_KNOWN_RESOURCE_METADATA + req.path
            params.append(f'resource_metadata={self._quote(url)}')

            # NOTE(vytas): MCP Authorization, 6. Scope Selection Strategy.
            #   MCP servers SHOULD include a scope parameter in the
            #   WWW-Authenticate header.
            if scopes := self.resource_metadata.get('scopes_supported'):
                params.append(f'scope={self._quote(" ".join(scopes))}')

        return f'Bearer {", ".join(params)}' if params else 'Bearer'

    async def authenticate(self, context: Context) -> None:
        if self.authenticator is None or await self.authenticator(context):
            return

        # NOTE(vytas): MCP Authorization, 9.2. Token Handling.
        #   Invalid or expired tokens MUST receive a HTTP 401 response.
        challenge = self._challenge(context.req)
        raise falcon.HTTPUnauthorized(challenges=[challenge])

    async def on_get(self, req: Request, resp: Response) -> None:
        if self.resource_metadata is None:
            raise falcon.HTTPNotFound()

        # NOTE(vytas): RFC 9728, 3.3. Protected Resource Metadata Validation.
        #   The resource value returned MUST be identical to the protected
        #   resource's resource identifier value into which the well-known URI
        #   path suffix was inserted to create the URL used to retrieve the
        #   metadata.
        path = req.path.removeprefix(WELL_KNOWN_RESOURCE_METADATA)
        resp.media = {
            'resource': f'{self._origin(req)}{path}',
            # NOTE(vytas): MCP Authorization, 9.1. Token Requirements.
            #   MCP client MUST use the Authorization request header field.
            'bearer_methods_supported': ['header'],
            **self.resource_metadata,
        }
