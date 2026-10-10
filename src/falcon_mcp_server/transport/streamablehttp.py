# SPDX-FileCopyrightText: 2026-present Vytautas Liuolia <vytautas.liuolia@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""
MCP Streamable HTTP transport implementation.
See also: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http.
"""

import base64
from collections.abc import Iterable
from typing import Any

import falcon
from falcon.asgi import Request
from falcon.asgi import Response

from falcon_mcp_server import errors
from falcon_mcp_server.constants import CONTENT_EVENT_STREAM
from falcon_mcp_server.constants import META_PROTOCOL_VERSION
from falcon_mcp_server.constants import SUPPORTED_MCP_PROTOCOL_VERSIONS
from falcon_mcp_server.server.server import RPCServer

# NOTE(vytas): MCP Streamable HTTP, 6.2. Standard Request Headers.
#   Mcp-Name: params.name or params.uri; required for tools/call,
#   resources/read, prompts/get requests.
_MCP_NAME_PARAMS = {
    'prompts/get': 'name',
    'resources/read': 'uri',
    'tools/call': 'name',
}

# NOTE(vytas): The spec only assigns HTTP status codes to the below errors;
#   the rest are rendered as 200 OK, same as in the official SDKs.
_ERROR_STATUS = {
    errors.RPCHeaderMismatch.code: falcon.HTTP_400,
    errors.RPCInvalidParam.code: falcon.HTTP_400,
    errors.RPCMethodNotFound.code: falcon.HTTP_404,
    errors.RPCUnsupportedProtocolVersion.code: falcon.HTTP_400,
}


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else None


def _decode_header(value: str | None) -> str | None:
    # NOTE(vytas): MCP Streamable HTTP, 6.3.2. Value Encoding.
    #   The prefix =?base64? and suffix ?= indicate that the value is
    #   Base64-encoded. (...) Servers MUST decode an encoded Mcp-Name or
    #   Mcp-Param-{Name} value before comparing it to the corresponding
    #   request body value during Server Validation.
    if value and value.startswith('=?base64?') and value.endswith('?='):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode()
        except ValueError:
            return None
    return value


class StreamableHTTP:
    """Falcon resource implementing the MCP Streamable HTTP transport.

    Only POST is supported; Falcon renders HTTP 405 Method Not Allowed in
    response to the GET and DELETE requests of the earlier protocol revisions.
    """

    def __init__(
        self,
        rpc_server: RPCServer,
        allowed_origins: str | Iterable[str] = (),
    ) -> None:
        self._rpc_server: RPCServer = rpc_server

        # NOTE(vytas): The same convention as falcon.CORSMiddleware's
        #   allow_origins: a single origin, '*' or an iterable of origins.
        if isinstance(allowed_origins, str):
            allowed_origins = [allowed_origins]
        self._allowed_origins = frozenset(allowed_origins)

    # NOTE(vytas): MCP Streamable HTTP, 6.5. Server Validation.
    #   Servers that process the request body MUST reject requests where the
    #   values specified in the headers do not match the corresponding values
    #   in the request body.
    # TODO(vytas): Validate Mcp-Param-{Name} against tools' x-mcp-header.
    def _validate_headers(self, req: Request, rpc_req: dict[str, Any]) -> None:
        method = rpc_req.get('method')
        params = rpc_req.get('params')

        # NOTE(vytas): MCP Streamable HTTP, 6.1. Protocol Version Header.
        #   Every POST request to the MCP endpoint MUST include an
        #   MCP-Protocol-Version header.
        if not (version := req.get_header('MCP-Protocol-Version')):
            # NOTE(vytas): MCP Versioning, 4. Backward Compatibility with
            #   Initialization-Based Versions.
            #   A server that supports only modern versions SHOULD name the
            #   protocol versions it supports in any error it returns to an
            #   initialize request.
            supported = ', '.join(sorted(SUPPORTED_MCP_PROTOCOL_VERSIONS))
            raise errors.RPCHeaderMismatch(
                f'Header mismatch: missing MCP-Protocol-Version header '
                f'(supported protocol versions: {supported})'
            )
        if version not in SUPPORTED_MCP_PROTOCOL_VERSIONS:
            raise errors.RPCUnsupportedProtocolVersion(version)

        # NOTE(vytas): MCP Streamable HTTP, 6.1. Protocol Version Header.
        #   The header value MUST match the
        #   io.modelcontextprotocol/protocolVersion field carried in the
        #   request body's _meta.
        body_version = _get(_get(params, '_meta'), META_PROTOCOL_VERSION)
        if body_version is not None and body_version != version:
            raise errors.RPCHeaderMismatch(
                f'Header mismatch: MCP-Protocol-Version header value '
                f'{version!r} does not match body value {body_version!r}'
            )

        if (header := req.get_header('Mcp-Method')) != method:
            raise errors.RPCHeaderMismatch(
                f'Header mismatch: Mcp-Method header value {header!r} does '
                f'not match body value {method!r}'
            )

        name = _get(params, _MCP_NAME_PARAMS.get(str(method), ''))
        header = req.get_header('Mcp-Name')
        if name is not None and _decode_header(header) != name:
            raise errors.RPCHeaderMismatch(
                f'Header mismatch: Mcp-Name header value {header!r} does not '
                f'match body value {name!r}'
            )

    # NOTE(vytas): MCP Streamable HTTP, 2. Sending Messages.
    #   The client MUST use HTTP POST to send JSON-RPC messages.
    async def on_post(self, req: Request, resp: Response) -> None:
        # NOTE(vytas): MCP Streamable HTTP, 1. Security & Endpoint.
        #   Servers MUST validate the Origin header on all incoming
        #   connections to prevent DNS rebinding attacks.
        #   * If the Origin header is present and invalid, servers MUST
        #     respond with HTTP 403 Forbidden.
        origin = req.get_header('Origin')
        if (
            origin is not None
            and origin not in self._allowed_origins
            and '*' not in self._allowed_origins
        ):
            raise falcon.HTTPForbidden(
                description=(
                    f'Requests from the origin {origin} are not allowed.'
                )
            )

        # NOTE(vytas): MCP Streamable HTTP, 2. Sending Messages.
        #   The client MUST include an Accept header listing both
        #   application/json and text/event-stream as supported content types.
        if not req.client_accepts_json or not req.client_accepts(
            CONTENT_EVENT_STREAM
        ):
            raise falcon.HTTPNotAcceptable(
                description=(
                    'The MCP client MUST accept both application/json '
                    'and text/event-stream.'
                )
            )

        # NOTE(vytas): MCP Streamable HTTP, 2. Sending Messages.
        #   The body of the HTTP POST MUST be a single JSON-RPC request or
        #   notification.
        rpc_req: dict[str, Any] = await req.get_media()
        if not isinstance(rpc_req, dict):
            raise falcon.HTTPUnprocessableEntity(
                description='A JSON-RPC message must be a JSON object.'
            )

        try:
            # NOTE(vytas): MCP Streamable HTTP, 2. Sending Messages.
            #   Header requirements for notification POSTs are not defined by
            #   this revision.
            if not self._rpc_server.is_notification(rpc_req):
                self._validate_headers(req, rpc_req)

            result = await self._rpc_server.handle(rpc_req)
        except errors.RPCError as ex:
            rpc_resp = {'jsonrpc': '2.0', 'error': ex.marshal()}
            if (req_id := rpc_req.get('id')) is not None:
                rpc_resp['id'] = req_id

            resp.status = _ERROR_STATUS.get(ex.code, falcon.HTTP_200)
            resp.media = rpc_resp
            return

        # NOTE(vytas): MCP Streamable HTTP, 2. Sending Messages.
        #   If the body is a JSON-RPC notification:
        #   * If the server accepts it, the server MUST return HTTP status
        #     code 202 Accepted with no body.
        if result is None:
            resp.status = falcon.HTTP_202
            return

        # NOTE(vytas): MCP Streamable HTTP, 2. Sending Messages.
        #   If the body is a JSON-RPC request, the server MUST return either
        #   Content-Type: application/json (a single JSON object) or
        #   Content-Type: text/event-stream (an SSE response stream). The
        #   client MUST support both.
        # TODO(vytas): We only support plain JSON objects in the MVP.
        resp.media = result
