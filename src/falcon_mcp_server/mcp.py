"""MCP end point component."""

from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from typing import Any

import falcon.asgi

from falcon_mcp_server.server.server import RPCServer
from falcon_mcp_server.transport.streamablehttp import StreamableHTTP


class MCP:
    """Falcon MCP end point resource.

    Args:
        allowed_origins: Origin(s) that web browsers are allowed to send
            requests from, or ``'*'`` to allow any origin. Requests carrying
            any other ``Origin`` header are rejected with HTTP 403 Forbidden
            (default: no origins are allowed).
    """

    def __init__(self, allowed_origins: str | Iterable[str] = ()) -> None:
        if not isinstance(allowed_origins, str):
            allowed_origins = tuple(allowed_origins)
        self._allowed_origins = allowed_origins

        self._rpc_server = RPCServer()
        self._streamable_http = StreamableHTTP(
            self._rpc_server, allowed_origins=allowed_origins
        )

        self.on_post = self._streamable_http.on_post

    # TODO: add_prompt(...)

    def add_simple_resource(
        self,
        uri: str,
        media_type: str,
        data: str | bytes,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        self._rpc_server.resources.add_simple_resource(
            uri,
            media_type,
            data,
            name=name,
            title=title,
            description=description,
        )

    def add_tool(
        self,
        tool: Callable[..., Awaitable[Any]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        input_schema: dict[Any, Any] | None = None,
    ) -> None:
        self._rpc_server.tools.add_tool(
            tool,
            name=name,
            title=title,
            description=description,
            input_schema=input_schema,
        )

    def create_app(self, cors_enable: bool = False) -> falcon.asgi.App:
        """Create a Falcon app serving this MCP end point at ``/mcp``.

        Args:
            cors_enable: Set to ``True`` to add :class:`falcon.CORSMiddleware`
                letting web frontends served from the allowed origins access
                the end point (default ``False``).
        """
        middleware = []
        if cors_enable:
            middleware.append(
                falcon.CORSMiddleware(allow_origins=self._allowed_origins)
            )

        app = falcon.asgi.App(middleware=middleware)
        app.add_route('/mcp', self)
        return app
