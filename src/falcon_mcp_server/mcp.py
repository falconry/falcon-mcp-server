"""MCP end point component."""

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import falcon.asgi

from falcon_mcp_server.server.server import RPCServer
from falcon_mcp_server.session.storage import SessionStorage
from falcon_mcp_server.transport.streamablehttp import StreamableHTTP


class MCP:
    """Falcon MCP end point resource."""

    def __init__(self) -> None:
        self._rpc_server = RPCServer()
        self._session_storage = SessionStorage()
        self._streamable_http = StreamableHTTP(
            self._rpc_server, self._session_storage
        )

        self.on_delete = self._streamable_http.on_delete
        self.on_get = self._streamable_http.on_get
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

    def create_app(self) -> falcon.asgi.App:
        app = falcon.asgi.App()
        app.add_route('/mcp', self)
        return app
