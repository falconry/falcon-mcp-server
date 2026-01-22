"""JSON-RPC server implementing the MCP Protocol."""

import json
from typing import Any

import falcon

from falcon_mcp_server.constants import PROJECT_URL
from falcon_mcp_server.constants import SUPPORTED_MCP_PROTOCOL_VERSIONS
from falcon_mcp_server.logger import log
from falcon_mcp_server.server.resources import Resources
from falcon_mcp_server.server.tools import Tools
from falcon_mcp_server.session.session import Session

try:
    import toon_format
except ImportError:
    toon_format = None


class RPCServer:
    def __init__(
        self,
        name: str = 'falcon-mcp-server',
        title: str = 'Falcon MCP Server project',
        description: str = (
            f'MCP server using falcon-mcp-server: {PROJECT_URL}'
        ),
        version: str = '1.0.0',
    ) -> None:
        self._resources: Resources = Resources()
        self._tools: Tools = Tools()

        self._rpc_api = {
            # Initialization
            'initialize': self._initialize,
            # Notifications, currently no-op
            # 'notifications/initialized': ...
            # 'notifications/cancelled': ...
            # Utilities
            'logging/setLevel': self._logging_set_level,
            'ping': self._ping,
            # Resources
            'resources/list': self._resources_list,
            'resources/read': self._resources_read,
            # Tools
            'tools/call': self._tools_call,
            'tools/list': self._tools_list,
        }

        self._server_info = {
            'name': name,
            'title': title,
            'version': version,
            'description': description,
        }

    @property
    def resources(self) -> Resources:
        return self._resources

    @property
    def tools(self) -> Tools:
        return self._tools

    def _is_notification(self, rpc_req: dict[str, Any]) -> bool:
        method: str | None = rpc_req.get('method')
        if (
            method
            and isinstance(method, str)
            and method.startswith('notifications/')
        ):
            return True

        return False

    async def handle(
        self, rpc_req: dict[str, Any], session: Session
    ) -> dict[str, Any] | None:
        # NOTE(vytas): MCP Overview, 1. Messages.
        #   All messages between MCP clients and servers MUST follow the
        #   JSON-RPC 2.0 specification.
        if rpc_req.get('jsonrpc') != '2.0':
            raise falcon.HTTPUnprocessableEntity(
                description=(
                    'All MCP messages MUST follow the JSON-RPC 2.0 '
                    'specification.'
                )
            )

        log.debug(f'Handle {rpc_req=}')

        if self._is_notification(rpc_req):
            # NOTE(vytas): MCP Overview, 1.3. Notifications.
            #   The receiver MUST NOT send a response.
            # TODO(vytas): Relay anything to the session, if needed.
            return None

        # TODO(vytas): Handle missing ID where it must be present according to
        #   the spec.
        req_id = rpc_req.get('id')

        # TODO(vytas): Handle missing keys.
        method_name = rpc_req['method']
        method = self._rpc_api[method_name]
        params = rpc_req.get('params') or {}

        result = await method(params, session)

        rpc_resp = {'jsonrpc': '2.0', 'result': result}
        if req_id is not None:
            rpc_resp['id'] = req_id

        log.debug(f'<= {rpc_resp}')

        return rpc_resp

    async def _initialize(
        self, params: dict[str, Any], session: Session
    ) -> dict[str, Any] | None:
        # TODO(vytas): Handle the case where protocolVersion is missing.
        protocol_version = params['protocolVersion']
        if protocol_version not in SUPPORTED_MCP_PROTOCOL_VERSIONS:
            protocol_version = max(SUPPORTED_MCP_PROTOCOL_VERSIONS)

        return {
            'protocolVersion': protocol_version,
            'capabilities': {
                'logging': {},
                'resources': {},
                'tools': {},
            },
            'serverInfo': self._server_info,
        }

    async def _logging_set_level(
        self, params: dict[str, Any], session: Session
    ) -> dict[str, Any] | None:
        # TODO(vytas): Relay to the session.
        return {}

    async def _ping(
        self, params: dict[str, Any], session: Session
    ) -> dict[str, Any] | None:
        return {}

    async def _resources_list(
        self, params: dict[str, Any], session: Session
    ) -> dict[str, Any] | None:
        return await self._resources.list_resources()

    async def _resources_read(
        self, params: dict[str, Any], session: Session
    ) -> dict[str, Any] | None:
        # TODO(vytas): Handle missing name/arguments.
        uri = params['uri']
        resource = await self._resources.read_resource(uri)
        return {'contents': [resource]}

    async def _tools_call(
        self, params: dict[str, Any], session: Session
    ) -> dict[str, Any] | None:
        # TODO(vytas): Handle missing name/arguments.
        name = params['name']
        arguments = params['arguments']

        result = await self._tools.call_tool(name, arguments)

        if isinstance(result, str):
            return {
                'content': [
                    {
                        'type': 'text',
                        'text': result,
                    },
                ],
            }

        if toon_format is not None:
            return {
                'content': [
                    {
                        'type': 'text',
                        'mimeType': 'text/toon',
                        'text': toon_format.encode(result),
                    },
                ],
            }

        return {
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps(result),
                },
            ],
        }

    async def _tools_list(
        self, params: dict[str, Any], session: Session
    ) -> dict[str, Any] | None:
        cursor = params.get('cursor')

        return await self._tools.list_tools(cursor=cursor)
