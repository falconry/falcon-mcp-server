# SPDX-FileCopyrightText: 2026-present Vytautas Liuolia <vytautas.liuolia@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""
MCP Streamable HTTP transport implementation.
See also: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.
"""

import asyncio
from typing import Any

import falcon
from falcon.asgi import Request
from falcon.asgi import Response
from falcon.asgi import SSEvent

from falcon_mcp_server.constants import CONTENT_EVENT_STREAM
from falcon_mcp_server.server.server import RPCServer
from falcon_mcp_server.session.session import Session
from falcon_mcp_server.session.storage import SessionStorage


class StreamableHTTP:
    """Falcon resource implementing the MCP Streamable HTTP transport."""

    def __init__(
        self, rpc_server: RPCServer, session_storage: SessionStorage
    ) -> None:
        self._rpc_server: RPCServer = rpc_server
        self._session_storage: SessionStorage = session_storage

    async def _get_session(self, req: Request) -> Session:
        if not (session_id := req.get_header('MCP-Session-Id')):
            # NOTE(vytas): MCP Transports, 2.5 Session Management.
            #   Servers that require a session ID SHOULD respond to requests
            #   without an MCP-Session-Id header (other than initialization)
            #   with HTTP 400 Bad Request.
            raise falcon.HTTPMissingHeader('MCP-Session-Id')

        session = await self._session_storage.load(session_id)

        # NOTE(vytas): MCP Transports, 2.5 Session Management.
        #   The server MAY terminate the session at any time, after which it
        #   MUST respond to requests containing that session ID with
        #   HTTP 404 Not Found.
        if session is None:
            raise falcon.HTTPNotFound(
                description=(
                    f'No session could be found corresponding to the provided '
                    f'MCP-Session-Id: {session_id}.'
                )
            )

        return session

    # NOTE(vytas): MCP Transports, 2.5 Session Management.
    #   Clients that no longer need a particular session (e.g., because the
    #   user is leaving the client application) SHOULD send an HTTP DELETE to
    #   the MCP endpoint with the MCP-Session-Id header, to explicitly
    #   terminate the session.
    async def on_delete(self, req: Request, resp: Response) -> None:
        session = await self._get_session(req)
        await self._session_storage.delete(session)

    # NOTE(vytas): MCP Transports, 2.2. Listening for Messages from the Server.
    #   The client MAY issue an HTTP GET to the MCP endpoint. This can be used
    #   to open an SSE stream, allowing the server to communicate to the
    #   client, without the client first sending data via HTTP POST.
    async def on_get(self, req: Request, resp: Response) -> None:
        # TODO: implement
        async def dummy_sse():
            while True:
                await asyncio.sleep(3.0)
                yield SSEvent()

        # NOTE(vytas): MCP Transports, 2.2. Listening for Messages from the
        #   Server.
        #   The client MUST include an Accept header, listing text/event-stream
        #   as a supported content type.
        if not req.client_accepts(CONTENT_EVENT_STREAM):
            raise falcon.HTTPNotAcceptable(
                description=(
                    'The MCP client MUST accept text/event-stream in order '
                    'to listen for messages from the server.'
                )
            )

        # NOTE(vytas): MCP Transports, 2.5 Session Management.
        #   If an MCP-Session-Id is returned by the server during
        #   initialization, clients using the Streamable HTTP transport MUST
        #   include it in the MCP-Session-Id header on all of their subsequent
        #   HTTP requests.
        await self._get_session(req)

        # NOTE(vytas): MCP Transports, 2.2. Listening for Messages from the
        #   Server.
        #   The server MUST either return Content-Type: text/event-stream in
        #   response to this HTTP GET, or else return HTTP 405 Method Not
        #   Allowed, indicating that the server does not offer an SSE stream at
        #   this endpoint.
        resp.sse = dummy_sse()

    # NOTE(vytas): MCP Transports, 2.1. Sending Messages to the Server.
    #   The client MUST use HTTP POST to send JSON-RPC messages to the MCP
    #   endpoint.
    async def on_post(self, req: Request, resp: Response) -> None:
        # NOTE(vytas): MCP Transports, 2.1. Sending Messages to the Server.
        #   The client MUST include an Accept header, listing both
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

        # NOTE(vytas): MCP Transports, 2.1. Sending Messages to the Server.
        #   The body of the POST request MUST be a single JSON-RPC request,
        #   notification, or response.
        rpc_req: dict[str, Any] = await req.get_media()
        if not isinstance(rpc_req, dict):
            raise falcon.HTTPUnprocessableEntity(
                description='A JSON-RPC message must be a JSON object.'
            )

        if rpc_req.get('method') == 'initialize':
            # NOTE(vytas): MCP Transports, 2.5 Session Management.
            #   A server using the Streamable HTTP transport MAY assign a
            #   session ID at initialization time, by including it in an
            #   MCP-Session-Id header on the HTTP response containing the
            #   InitializeResult.
            session = await self._session_storage.create()
            resp.set_header('MCP-Session-Id', session.session_id)
        else:
            # NOTE(vytas): MCP Transports, 2.5 Session Management.
            #   If an MCP-Session-Id is returned by the server during
            #   initialization, clients using the Streamable HTTP transport
            #   MUST include it in the MCP-Session-Id header on all of their
            #   subsequent HTTP requests.
            session = await self._get_session(req)

        result = await self._rpc_server.handle(rpc_req, session)

        # NOTE(vytas): MCP Transports, 2.1. Sending Messages to the Server.
        #   If the input is a JSON-RPC response or notification:
        #   * If the server accepts the input, the server MUST return HTTP
        #     status code 202 Accepted with no body.
        if result is None:
            resp.status = falcon.HTTP_202
            return

        # NOTE(vytas): MCP Transports, 2.1. Sending Messages to the Server.
        #   If the input is a JSON-RPC request, the server MUST either return
        #   Content-Type: text/event-stream, to initiate an SSE stream, or
        #   Content-Type: application/json, to return one JSON object. The
        #   client MUST support both these cases.
        # TODO(vytas): We only support plain JSON objects in the MVP.
        resp.media = result
