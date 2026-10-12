"""JSON-RPC server implementing the MCP Protocol."""

import json
from typing import Any

import falcon

from falcon_mcp_server import errors
from falcon_mcp_server.constants import META_CLIENT_CAPABILITIES
from falcon_mcp_server.constants import META_PROTOCOL_VERSION
from falcon_mcp_server.constants import META_SERVER_INFO
from falcon_mcp_server.constants import PROJECT_URL
from falcon_mcp_server.constants import SUPPORTED_MCP_PROTOCOL_VERSIONS
from falcon_mcp_server.logger import log
from falcon_mcp_server.server.resources import Resources
from falcon_mcp_server.server.tools import Tools

try:
    import toon_format
except ImportError:
    toon_format = None

# NOTE(vytas): MCP Caching, 1. Cacheable Results.
#   Servers MUST include caching hints on results with
#   resultType: "complete" returned by the following operations:
#   server/discover, tools/list, prompts/list, resources/list,
#   resources/templates/list, resources/read.
# TODO(vytas): Make the hints configurable. For now, everything is advertised
#   as immediately stale, i.e., clients behave as before this was introduced.
CACHE_HINTS = {'ttlMs': 0, 'cacheScope': 'private'}


def _get_str(params: dict[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise errors.RPCInvalidParam(f'Missing or invalid parameter: {name}')
    return value


class RPCServer:
    def __init__(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        version: str = '1.0.0',
        instructions: str | None = None,
    ) -> None:
        self._resources: Resources = Resources()
        self._tools: Tools = Tools()

        self._rpc_api = {
            # Discovery
            'server/discover': self._server_discover,
            # Resources
            'resources/list': self._resources_list,
            'resources/read': self._resources_read,
            # Tools
            'tools/call': self._tools_call,
            'tools/list': self._tools_list,
        }

        # NOTE(vytas): The default title and description are about this
        #   project, so they would only mislead next to a custom name.
        if name is None:
            name = 'falcon-mcp-server'
            title = title or 'Falcon MCP Server project'
            description = description or (
                f'MCP server using falcon-mcp-server: {PROJECT_URL}'
            )

        self._server_info = {'name': name, 'version': version}
        if title is not None:
            self._server_info['title'] = title
        if description is not None:
            self._server_info['description'] = description

        self._instructions = instructions

    @property
    def resources(self) -> Resources:
        return self._resources

    @property
    def tools(self) -> Tools:
        return self._tools

    def is_notification(self, rpc_req: dict[str, Any]) -> bool:
        method: str | None = rpc_req.get('method')
        if (
            method
            and isinstance(method, str)
            and method.startswith('notifications/')
        ):
            return True

        return False

    def _validate_meta(self, params: dict[str, Any]) -> None:
        meta = params.get('_meta')
        if not isinstance(meta, dict):
            meta = {}

        # NOTE(vytas): MCP Overview, 6.1. _meta.
        #   A request missing any required field is malformed; the server MUST
        #   reject it with JSON-RPC error code -32602 (Invalid params).
        for key in (META_PROTOCOL_VERSION, META_CLIENT_CAPABILITIES):
            if key not in meta:
                raise errors.RPCInvalidParam(
                    f'Missing required _meta field: {key}'
                )

        # NOTE(vytas): MCP Versioning, 2. Protocol Version Negotiation.
        #   If the server does not implement the requested version (...), it
        #   MUST respond with an UnsupportedProtocolVersionError listing the
        #   versions it does support.
        version = meta[META_PROTOCOL_VERSION]
        if (
            not isinstance(version, str)
            or version not in SUPPORTED_MCP_PROTOCOL_VERSIONS
        ):
            raise errors.RPCUnsupportedProtocolVersion(str(version))

    async def handle(self, rpc_req: dict[str, Any]) -> dict[str, Any] | None:
        # NOTE(vytas): MCP Overview, 1. Messages.
        #   All messages between MCP clients and servers MUST follow the
        #   JSON-RPC 2.0 specification.
        method_name = rpc_req.get('method')
        params = rpc_req.get('params') or {}
        if (
            rpc_req.get('jsonrpc') != '2.0'
            or not isinstance(method_name, str)
            or not isinstance(params, dict)
        ):
            raise falcon.HTTPUnprocessableEntity(
                description=(
                    'All MCP messages MUST follow the JSON-RPC 2.0 '
                    'specification.'
                )
            )

        log.debug(f'Handle {rpc_req=}')

        if self.is_notification(rpc_req):
            # NOTE(vytas): MCP Overview, 1.3. Notifications.
            #   The receiver MUST NOT send a response.
            return None

        # TODO(vytas): Handle missing ID where it must be present according to
        #   the spec.
        req_id = rpc_req.get('id')

        self._validate_meta(params)

        # NOTE(vytas): MCP Streamable HTTP, 6.1. Protocol Version Header.
        #   If the server does not implement the requested RPC method, it MUST
        #   respond with 404 Not Found and a JSON-RPC error with code -32601
        #   (Method not found).
        if (method := self._rpc_api.get(method_name)) is None:
            raise errors.RPCMethodNotFound(f'Method not found: {method_name}')

        result = await method(params)

        # NOTE(vytas): MCP Overview, 1.2.1. Result Responses.
        #   The result MUST include a resultType field to indicate the type of
        #   the result.
        result['resultType'] = 'complete'
        # NOTE(vytas): MCP Overview, 6.1. _meta.
        #   Servers SHOULD include [serverInfo] in every result's _meta, unless
        #   specifically configured not to do so, to identify themselves
        #   without relying on any prior connection state.
        result.setdefault('_meta', {})[META_SERVER_INFO] = self._server_info

        rpc_resp = {'jsonrpc': '2.0', 'result': result}
        if req_id is not None:
            rpc_resp['id'] = req_id

        log.debug(f'<= {rpc_resp}')

        return rpc_resp

    async def _server_discover(self, params: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            'supportedVersions': sorted(SUPPORTED_MCP_PROTOCOL_VERSIONS),
            'capabilities': {
                'resources': {},
                'tools': {},
            },
            **CACHE_HINTS,
        }
        # NOTE(vytas): MCP Discovery, 4.1. DiscoverResult.
        #   instructions: Optional natural-language guidance for LLMs on how
        #   to use this server effectively.
        if self._instructions is not None:
            result['instructions'] = self._instructions
        return result

    async def _resources_list(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._resources.list_resources()
        return {**result, **CACHE_HINTS}

    async def _resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = _get_str(params, 'uri')
        resource = await self._resources.read_resource(uri)
        return {'contents': [resource], **CACHE_HINTS}

    async def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = _get_str(params, 'name')
        arguments = params.get('arguments') or {}

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

    async def _tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        cursor = params.get('cursor')

        result = await self._tools.list_tools(cursor=cursor)
        return {**result, **CACHE_HINTS}
