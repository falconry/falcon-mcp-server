"""MCP tools."""

import dataclasses
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from falcon_mcp_server import errors


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
        }
        if self.input_schema:
            data['inputSchema'] = self.input_schema
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
        if name is self._tools:
            raise ValueError(f'Tool with the {name=} is already registered.')

        name = name or tool.__name__
        title = title or name
        description = description or tool.__doc__ or name

        self._tools[name] = Tool(
            tool,
            name=name,
            title=title,
            description=description,
            input_schema=input_schema,
        )
        self._tool_list.append(name)

    async def list_tools(self, cursor: str | None = None) -> dict[str, Any]:
        start = 0

        if cursor is not None:
            if cursor.startswith('tools'):
                _, _, offset = cursor.partition('tools')
                start = int(offset)
                if start >= len(self._tool_list):
                    raise errors.RPCInvalidParam('Invalid cursor')

        end = start + self.MAX_PAGE_SIZE
        tools = []
        for name in self._tool_list[start:end]:
            tools.append(self._tools[name].marshal())

        result: dict[str, Any] = {'tools': tools}
        if end < len(self._tool_list):
            result['nextCursor'] = f'tools{end:04d}'

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
