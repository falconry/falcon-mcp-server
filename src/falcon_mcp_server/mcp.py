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

    Unless ``name`` is given, the server introduces itself to clients as a
    generic ``falcon-mcp-server``, with a title and description to match.

    Args:
        allowed_origins: Origin(s) that web browsers are allowed to send
            requests from, or ``'*'`` to allow any origin. Requests carrying
            any other ``Origin`` header are rejected with HTTP 403 Forbidden
            (default: no origins are allowed).
        name: Programmatic name identifying the server to clients.
        title: Human-readable name of the server for display purposes.
        description: Human-readable description of what the server does.
        version: Version of the server (default ``'1.0.0'``).
        instructions: Guidance for LLMs on how to use the server.
    """

    def __init__(
        self,
        allowed_origins: str | Iterable[str] = (),
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        version: str = '1.0.0',
        instructions: str | None = None,
    ) -> None:
        if not isinstance(allowed_origins, str):
            allowed_origins = tuple(allowed_origins)
        self._allowed_origins = allowed_origins

        self._rpc_server = RPCServer(
            name=name,
            title=title,
            description=description,
            version=version,
            instructions=instructions,
        )
        self._streamable_http = StreamableHTTP(
            self._rpc_server, allowed_origins=allowed_origins
        )

        self.on_post = self._streamable_http.on_post

    def add_prompt(
        self,
        prompt: Callable[..., Awaitable[Any]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        arguments: list[dict[str, Any]] | None = None,
    ) -> None:
        """Add a prompt template rendered by an async callable.

        The callable receives the prompt arguments (strings) as keyword
        arguments, and returns the text of a single user message, or a list
        of MCP prompt messages.

        Args:
            prompt: Async callable rendering the prompt.
            name: Name of the prompt (default: the callable's name).
            title: Human-readable name of the prompt for display purposes.
            description: Description (default: the callable's docstring).
            arguments: MCP argument definitions (default: from the signature).
        """
        self._rpc_server.prompts.add_prompt(
            prompt,
            name=name,
            title=title,
            description=description,
            arguments=arguments,
        )

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

    def create_app(
        self, cors_enable: bool = False
    ) -> falcon.asgi.App[falcon.asgi.Request, falcon.asgi.Response]:
        """Create a Falcon app serving this MCP end point at ``/mcp``.

        Args:
            cors_enable: Set to ``True`` to add :class:`falcon.CORSMiddleware`
                letting web frontends served from the allowed origins access
                the end point (default ``False``).
        """
        app = falcon.asgi.App()
        if cors_enable:
            app.add_middleware(
                falcon.CORSMiddleware(allow_origins=self._allowed_origins)
            )

        app.add_route('/mcp', self)
        return app
