"""MCP prompts."""

import dataclasses
import inspect
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from falcon_mcp_server import errors
from falcon_mcp_server.server.pagination import paginate


@dataclasses.dataclass
class Prompt:
    method: Callable[..., Awaitable[Any]]
    name: str
    title: str
    description: str
    arguments: list[dict[str, Any]]

    def marshal(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'name': self.name,
            'title': self.title,
            'description': self.description,
            'arguments': self.arguments,
        }
        return data


class Prompts:
    MAX_PAGE_SIZE = 32

    def __init__(self) -> None:
        self._prompts: dict[str, Prompt] = {}
        self._prompt_list: list[str] = []

    @staticmethod
    def _derive_arguments(
        prompt: Callable[..., Awaitable[Any]],
    ) -> list[dict[str, Any]]:
        """Describe the named parameters of prompt as prompt arguments."""
        arguments = []
        for param in inspect.signature(prompt).parameters.values():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            arguments.append(
                {'name': param.name, 'required': param.default is param.empty}
            )
        return arguments

    def add_prompt(
        self,
        prompt: Callable[..., Awaitable[Any]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        arguments: list[dict[str, Any]] | None = None,
    ) -> None:
        name = name or prompt.__name__
        if name in self._prompts:
            raise ValueError(f'Prompt with the {name=} is already registered.')

        title = title or name
        description = description or inspect.getdoc(prompt) or name
        if arguments is None:
            arguments = self._derive_arguments(prompt)

        self._prompts[name] = Prompt(
            prompt,
            name=name,
            title=title,
            description=description,
            arguments=arguments,
        )
        self._prompt_list.append(name)

    async def list_prompts(self, cursor: str | None = None) -> dict[str, Any]:
        names, next_cursor = paginate(
            self._prompt_list, cursor, 'prompts', self.MAX_PAGE_SIZE
        )

        prompts = [self._prompts[name].marshal() for name in names]
        result: dict[str, Any] = {'prompts': prompts}
        if next_cursor is not None:
            result['nextCursor'] = next_cursor

        return result

    async def get_prompt(self, name: str, arguments: Any) -> dict[str, Any]:
        # NOTE(vytas): MCP Prompts, 6. Error Handling.
        #   Servers SHOULD return standard JSON-RPC errors for common failure
        #   cases:
        #   * Invalid prompt name: -32602 (Invalid params)
        #   * Missing required arguments: -32602 (Invalid params)
        #   * Internal errors: -32603 (Internal error)
        if (prompt := self._prompts.get(name)) is None:
            raise errors.RPCInvalidParam('Prompt not found')

        # NOTE(vytas): MCP Prompts, 7. Implementation Considerations.
        #   Servers SHOULD validate prompt arguments before processing.
        if not isinstance(arguments, dict) or not all(
            isinstance(value, str) for value in arguments.values()
        ):
            raise errors.RPCInvalidParam(
                'Prompt arguments must be an object with string values'
            )

        known = [argument['name'] for argument in prompt.arguments]
        if unknown := sorted(set(arguments).difference(known)):
            raise errors.RPCInvalidParam(
                f'Unknown prompt argument(s): {", ".join(unknown)}'
            )

        missing = [
            argument['name']
            for argument in prompt.arguments
            if argument.get('required') and argument['name'] not in arguments
        ]
        if missing:
            raise errors.RPCInvalidParam(
                f'Missing required prompt argument(s): {", ".join(missing)}'
            )

        try:
            messages = await prompt.method(**arguments)
        except Exception as ex:
            raise errors.RPCInternalError(
                f'Error getting prompt {name}: {type(ex).__name__}: {ex}'
            )

        if isinstance(messages, str):
            messages = [
                {
                    'role': 'user',
                    'content': {'type': 'text', 'text': messages},
                },
            ]

        return {'description': prompt.description, 'messages': messages}
