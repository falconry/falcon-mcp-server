"""MCP tools."""

import dataclasses
import inspect
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from falcon_mcp_server import errors
from falcon_mcp_server.server.pagination import paginate


@dataclasses.dataclass
class Tool:
    method: Callable[..., Awaitable[Any]]
    name: str
    title: str
    description: str
    input_schema: dict[Any, Any] | None = None

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
        return data


class Tools:
    MAX_PAGE_SIZE = 32

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._tool_list: list[str] = []

    def add_tool(
        self,
        tool: Callable[..., Awaitable[Any]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        input_schema: dict[Any, Any] | None = None,
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
        )
        self._tool_list.append(name)

    async def list_tools(self, cursor: str | None = None) -> dict[str, Any]:
        names, next_cursor = paginate(
            self._tool_list, cursor, 'tools', self.MAX_PAGE_SIZE
        )

        tools = [self._tools[name].marshal() for name in names]
        result: dict[str, Any] = {'tools': tools}
        if next_cursor is not None:
            result['nextCursor'] = next_cursor

        return result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if (tool := self._tools.get(name)) is None:
            raise errors.RPCInvalidParam('Tool not found')

        try:
            return await tool.method(**arguments)
        except Exception as ex:
            raise errors.RPCInternalError(
                f'Error calling tool {name}: {type(ex).__name__}: {ex}'
            )
