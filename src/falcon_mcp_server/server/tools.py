"""MCP tools."""

import dataclasses
import inspect
import re
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any, TypedDict

import falcon

from falcon_mcp_server import errors
from falcon_mcp_server.context import Context
from falcon_mcp_server.logger import log
from falcon_mcp_server.server.introspection import find_context_param
from falcon_mcp_server.server.pagination import paginate


class ToolAnnotations(TypedDict, total=False):
    """MCP tool annotations: hints describing the behavior of a tool.

    All keys are optional; a client assumes the default for an absent hint.
    The destructive and idempotent hints only apply to a tool that is not
    read-only.

    Attributes:
        title: Human-readable name; the tool's own title takes precedence.
        readOnlyHint: The tool does not modify its environment (default false).
        destructiveHint: The tool may make destructive updates (default true).
        idempotentHint: Repeating a call has no further effect (default false).
        openWorldHint: The tool reaches external entities (default true).
    """

    title: str
    readOnlyHint: bool
    destructiveHint: bool
    idempotentHint: bool
    openWorldHint: bool


@dataclasses.dataclass
class Tool:
    """Tool registered with an MCP server.

    Attributes:
        method: Async callable implementing the tool.
        name: Name of the tool.
        title: Human-readable name of the tool for display purposes.
        description: Description of the tool.
        input_schema: JSON Schema of the tool arguments, if any.
        annotations: MCP tool annotations, such as ``readOnlyHint``.
        meta: The ``_meta`` object of the tool, if any.
        context_param: Name of the parameter receiving the context, if any.
        header_params: ``x-mcp-header`` names by the path of the argument.
    """

    method: Callable[..., Awaitable[Any]]
    name: str
    title: str
    description: str
    input_schema: dict[Any, Any] | None = None
    annotations: ToolAnnotations = dataclasses.field(
        default_factory=ToolAnnotations
    )
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)
    context_param: str | None = None
    header_params: dict[tuple[str, ...], str] = dataclasses.field(
        default_factory=dict
    )

    def marshal(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'name': self.name,
            'title': self.title,
            'description': self.description,
            # NOTE(vytas): MCP Tools, 5.1. Tool.
            #   inputSchema (...) MUST be a valid JSON Schema object (not
            #   null).
            'inputSchema': self.input_schema or {'type': 'object'},
        }
        if self.annotations:
            data['annotations'] = self.annotations
        if self.meta:
            data['_meta'] = self.meta
        return data


ToolFilter = Callable[[Context, Tool], bool]
ResultSerializer = Callable[..., str]


class Tools:
    MAX_PAGE_SIZE = 32

    # NOTE(vytas): JSON Schema keywords holding a subschema (or a list of
    #   them), and the ones mapping names to subschemas. Unlike properties,
    #   none of them leads to a property that x-mcp-header is allowed on.
    _SUBSCHEMAS = frozenset(
        {
            'additionalProperties',
            'allOf',
            'anyOf',
            'contains',
            'contentSchema',
            'else',
            'if',
            'items',
            'not',
            'oneOf',
            'prefixItems',
            'propertyNames',
            'then',
            'unevaluatedItems',
            'unevaluatedProperties',
        }
    )
    _SUBSCHEMA_MAPS = frozenset(
        {'$defs', 'definitions', 'dependentSchemas', 'patternProperties'}
    )

    _HEADER_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
    _HEADER_TYPES = ('boolean', 'integer', 'string')

    def __init__(self, tool_filter: ToolFilter | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._filter = tool_filter

    def __len__(self) -> int:
        return len(self._tools)

    def _is_visible(self, context: Context, tool: Tool) -> bool:
        return self._filter is None or self._filter(context, tool)

    @classmethod
    def _walk_schema(
        cls, schema: Any, path: tuple[str, ...] | None = ()
    ) -> Iterator[tuple[tuple[str, ...] | None, dict[str, Any]]]:
        """Yield every subschema of schema along with its path.

        The path is the chain of property names leading to the subschema, or
        ``None`` if it cannot be reached via ``properties`` alone.
        """
        if not isinstance(schema, dict):
            return
        yield path, schema

        for keyword, value in schema.items():
            if keyword == 'properties' and isinstance(value, dict):
                for name, subschema in value.items():
                    subpath = None if path is None else (*path, name)
                    yield from cls._walk_schema(subschema, subpath)
            elif keyword in cls._SUBSCHEMA_MAPS and isinstance(value, dict):
                for subschema in value.values():
                    yield from cls._walk_schema(subschema, None)
            elif keyword in cls._SUBSCHEMAS:
                for subschema in value if isinstance(value, list) else [value]:
                    yield from cls._walk_schema(subschema, None)

    @classmethod
    def _find_header_params(
        cls, input_schema: Any
    ) -> dict[tuple[str, ...], str]:
        """Map the paths of arguments annotated with x-mcp-header to its value.

        Raises:
            ValueError: An annotation violates the constraints of the spec.
        """
        # NOTE(vytas): MCP Streamable HTTP, 6.3.1. Schema Extension.
        #   Clients using the Streamable HTTP transport MUST reject tool
        #   definitions where any x-mcp-header value violates these
        #   constraints. Rejection means the client MUST exclude the invalid
        #   tool from the result of tools/list.
        # NOTE(vytas): Such a tool would silently go missing on the client
        #   side, so an invalid annotation is rather refused up front.
        header_params: dict[tuple[str, ...], str] = {}
        seen = set()

        for path, schema in cls._walk_schema(input_schema):
            if 'x-mcp-header' not in schema:
                continue
            if not path:
                raise ValueError(
                    'x-mcp-header is only allowed on the properties that are '
                    'reachable from the schema root via properties alone.'
                )

            header = schema['x-mcp-header']
            where = f'x-mcp-header of the property {".".join(path)}'
            if not isinstance(header, str) or not cls._HEADER_TOKEN.fullmatch(
                header
            ):
                raise ValueError(f'The {where} is not a valid header name.')
            if schema.get('type') not in cls._HEADER_TYPES:
                raise ValueError(
                    f'The {where} requires the type boolean, integer or '
                    f'string.'
                )
            if header.lower() in seen:
                raise ValueError(f'The {where} ({header}) is not unique.')

            seen.add(header.lower())
            header_params[path] = header

        return header_params

    def add_tool(
        self,
        tool: Callable[..., Awaitable[Any]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        input_schema: dict[Any, Any] | None = None,
        annotations: ToolAnnotations | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        name = name or tool.__name__
        if name in self._tools:
            raise ValueError(f'Tool with the {name=} is already registered.')

        title = title or name
        description = description or inspect.getdoc(tool) or name

        self._tools[name] = Tool(
            tool,
            name=name,
            title=title,
            description=description,
            input_schema=input_schema,
            annotations=annotations or {},
            meta=meta or {},
            context_param=find_context_param(tool),
            header_params=self._find_header_params(input_schema),
        )

    def get_header_params(
        self, context: Context, name: str
    ) -> dict[tuple[str, ...], str]:
        """Return the x-mcp-header names of a tool by the path of the argument.

        Nothing is returned for a tool that is missing or hidden from the
        request, so that the two cannot be told apart by a header mismatch.
        """
        tool = self._tools.get(name)
        if (
            tool is None
            or not tool.header_params
            or not self._is_visible(context, tool)
        ):
            return {}
        return tool.header_params

    async def list_tools(
        self, context: Context, cursor: str | None = None
    ) -> dict[str, Any]:
        # NOTE(vytas): Filter before paginating, so that the cursor is an
        #   offset among the tools visible to the request.
        visible = [
            tool
            for tool in self._tools.values()
            if self._is_visible(context, tool)
        ]
        page, next_cursor = paginate(
            visible, cursor, 'tools', self.MAX_PAGE_SIZE
        )

        result: dict[str, Any] = {'tools': [tool.marshal() for tool in page]}
        if next_cursor is not None:
            result['nextCursor'] = next_cursor

        return result

    async def call_tool(
        self, context: Context, name: str, arguments: Any
    ) -> Any:
        # NOTE(vytas): Hiding a tool from the list would not stop a client
        #   from calling it by name; a hidden tool is the same as a missing
        #   one.
        tool = self._tools.get(name)
        if tool is None or not self._is_visible(context, tool):
            raise errors.RPCInvalidParam('Tool not found')

        if not isinstance(arguments, dict):
            raise errors.RPCInvalidParam('Tool arguments must be an object')

        if tool.context_param is not None:
            # NOTE(vytas): The context is not a tool argument, so it must
            #   not be possible to supply it in the request.
            if tool.context_param in arguments:
                raise errors.RPCInvalidParam(
                    f'Unknown tool argument: {tool.context_param}'
                )
            arguments = {**arguments, tool.context_param: context}

        try:
            return await tool.method(**arguments)
        except (errors.RPCError, errors.ToolError, falcon.HTTPError):
            # NOTE(vytas): These are assumed to be raised intentionally.
            raise
        except Exception:
            # NOTE(vytas): The details of an unexpected exception may be
            #   sensitive, so they are logged, but not sent to the client.
            log.exception(f'Error calling tool {name}')
            raise errors.RPCInternalError(f'Error calling tool {name}')
