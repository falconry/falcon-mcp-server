"""MCP end point component."""

from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from typing import Any

import falcon.asgi

from falcon_mcp_server.server.server import RPCServer
from falcon_mcp_server.server.tools import ToolAnnotations
from falcon_mcp_server.server.tools import ToolFilter
from falcon_mcp_server.transport.streamablehttp import StreamableHTTP


class MCP:
    """Falcon MCP end point resource.

    Unless ``name`` is given, the server introduces itself to clients as a
    generic ``falcon-mcp-server``, with a title and description to match.

    The end point can also be added to an app on a route of your choice,
    including a templated one; the values of its fields are then available
    to tools and prompts as :attr:`Context.path_params`.

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
        tool_filter: Callable ``(context, tool) -> bool`` deciding whether a
            tool is listed and callable in a request (default: always).
    """

    def __init__(
        self,
        allowed_origins: str | Iterable[str] = (),
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        version: str = '1.0.0',
        instructions: str | None = None,
        tool_filter: ToolFilter | None = None,
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
            tool_filter=tool_filter,
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
        of MCP prompt messages. A parameter annotated with :class:`Context`
        receives the request context instead of a prompt argument.

        Raise a :class:`falcon.HTTPError` to respond with that HTTP error.
        Any other exception is logged, and reported without details.

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

    def add_resource(
        self,
        uri: str,
        media_type: str,
        resource: Callable[..., Awaitable[str | bytes]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        """Add a resource read by an async callable.

        The callable is awaited on every read, and returns the contents as
        text (``str``) or binary data (``bytes``). A parameter annotated with
        :class:`Context` receives the request context.

        Raise a :class:`falcon.HTTPError` to respond with that HTTP error.
        Any other exception is logged, and reported without details.

        Args:
            uri: URI identifying the resource, as defined by RFC 3986.
            media_type: Media type of the contents.
            resource: Async callable returning the contents.
            name: Name of the resource (default: the last segment of the URI).
            title: Human-readable name of the resource for display purposes.
            description: Description (default: the callable's docstring).

        Raises:
            ValueError: The URI is invalid or taken, or yields no default name.
        """
        self._rpc_server.resources.add_resource(
            uri,
            media_type,
            resource,
            name=name,
            title=title,
            description=description,
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
        """Add a resource with fixed contents.

        Args:
            uri: URI identifying the resource, as defined by RFC 3986.
            media_type: Media type of the contents.
            data: The contents as text (``str``) or binary data (``bytes``).
            name: Name of the resource (default: the last segment of the URI).
            title: Human-readable name of the resource for display purposes.
            description: Description (default: the title, or the name).

        Raises:
            ValueError: The URI is invalid or taken, or yields no default name.
        """
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
        annotations: ToolAnnotations | None = None,
    ) -> None:
        """Add a tool implemented by an async callable.

        The callable receives the tool arguments as keyword arguments. A
        parameter annotated with :class:`Context` receives the request
        context instead of a tool argument; leave it out of ``input_schema``.

        Raise :class:`ToolError` to report an error that the model can act
        upon, or a :class:`falcon.HTTPError` to respond with that HTTP error.
        Any other exception is logged, and reported without details.

        Clients mirror an argument whose schema carries ``x-mcp-header`` into
        the ``Mcp-Param-*`` HTTP header of that name, e.g., for a proxy to
        log or route on. A call where the two disagree is rejected.

        Args:
            tool: Async callable implementing the tool.
            name: Name of the tool (default: the callable's name).
            title: Human-readable name of the tool for display purposes.
            description: Description (default: the callable's docstring).
            input_schema: JSON Schema of the tool arguments (default: any).
            annotations: MCP tool annotations, see :class:`ToolAnnotations`.

        Raises:
            ValueError: The name is taken, or ``x-mcp-header`` is misused.
        """
        self._rpc_server.tools.add_tool(
            tool,
            name=name,
            title=title,
            description=description,
            input_schema=input_schema,
            annotations=annotations,
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
