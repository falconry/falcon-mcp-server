# SPDX-FileCopyrightText: 2026-present Vytautas Liuolia <vytautas.liuolia@gmail.com>
#
# SPDX-License-Identifier: Apache-2.0

"""
MCP stdio transport implementation.
See also: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio.
"""

import asyncio
import contextlib
import json
import os
import sys
import threading
from typing import Any, BinaryIO

import falcon
import falcon.testing

from falcon_mcp_server import errors
from falcon_mcp_server.context import Context
from falcon_mcp_server.logger import log
from falcon_mcp_server.server.server import RPCServer


class Stdio:
    """MCP stdio transport: newline-delimited JSON-RPC over stdin and stdout.

    Requests are handled concurrently, and each response is written as soon
    as it is ready; the client tells them apart by the JSON-RPC ``id``.
    """

    def __init__(self, rpc_server: RPCServer) -> None:
        self._rpc_server = rpc_server
        # NOTE(vytas): Shared by the placeholder requests, like the options of
        #   an app; creating them (along with the media handlers) would take
        #   most of the time it takes to create a request.
        self._req_options = falcon.RequestOptions()
        # NOTE(vytas): The requests in flight, by the ID, so that they can be
        #   cancelled.
        self._tasks: dict[Any, asyncio.Task[None]] = {}

    @staticmethod
    def _is_request_id(value: Any) -> bool:
        # NOTE(vytas): MCP Schema, RequestId: string | number.
        return isinstance(value, (str, int, float)) and not isinstance(
            value, bool
        )

    @staticmethod
    def _write(output: BinaryIO, message: Any) -> None:
        # NOTE(vytas): MCP Transports, 1. Messages.
        #   JSON-RPC messages MUST be UTF-8 encoded.
        # NOTE(vytas): MCP stdio.
        #   Messages are delimited by newlines, and MUST NOT contain embedded
        #   newlines.
        # NOTE(vytas): json.dumps() escapes the ones within strings.
        data = json.dumps(message, ensure_ascii=False)
        output.write(data.encode() + b'\n')
        output.flush()

    @classmethod
    def _write_error(
        cls, output: BinaryIO, req_id: Any, error: errors.RPCError
    ) -> None:
        cls._write(
            output, {'jsonrpc': '2.0', 'id': req_id, 'error': error.marshal()}
        )

    async def _respond(
        self, rpc_req: dict[str, Any], output: BinaryIO
    ) -> None:
        params = rpc_req.get('params')
        meta = params.get('_meta') if isinstance(params, dict) else None
        context = Context(
            # NOTE(vytas): A placeholder without any headers or parameters,
            #   for the tools that are also served over HTTP.
            req=falcon.testing.create_asgi_req(
                host='localhost', options=self._req_options
            ),
            path_params={},
            meta=meta if isinstance(meta, dict) else {},
        )

        try:
            self._write(
                output, await self._rpc_server.handle(rpc_req, context)
            )
            return
        except errors.RPCError as ex:
            error = ex
        except falcon.HTTPError as ex:
            # NOTE(vytas): There is no HTTP response to render it in, so it is
            #   reported as an internal error, along with its details.
            details = ex.title
            if ex.description:
                details = f'{details}: {ex.description}'
            error = errors.RPCInternalError(details)
        except Exception:
            # NOTE(vytas): On HTTP, Falcon would render HTTP 500.
            log.exception(f'Error handling {rpc_req.get("method")}')
            error = errors.RPCInternalError('Internal error')

        self._write_error(output, rpc_req.get('id'), error)

    def _receive(self, line: bytes, output: BinaryIO) -> None:
        if not line.strip():
            return

        try:
            message = json.loads(line)
        except ValueError:
            self._write_error(
                output, None, errors.RPCParseError('Parse error')
            )
            return
        if not isinstance(message, dict):
            self._write_error(
                output,
                None,
                errors.RPCInvalidRequest(
                    'A JSON-RPC message must be a JSON object.'
                ),
            )
            return

        # NOTE(vytas): MCP stdio, 1. Sending Messages.
        #   The client MUST NOT write JSON-RPC responses.
        if 'method' not in message:
            return

        if self._rpc_server.is_notification(message):
            # NOTE(vytas): MCP stdio, 4. Cancellation.
            #   To cancel an in-flight request, the client MUST send a
            #   notifications/cancelled notification referencing the
            #   request's ID.
            params = message.get('params')
            if (
                message['method'] == 'notifications/cancelled'
                and isinstance(params, dict)
                and self._is_request_id(req_id := params.get('requestId'))
                and (task := self._tasks.get(req_id)) is not None
            ):
                task.cancel()
            return

        if not self._is_request_id(req_id := message.get('id')):
            self._write_error(
                output,
                None,
                errors.RPCInvalidRequest(
                    'A request must have a string or number ID.'
                ),
            )
            return

        task = asyncio.create_task(self._respond(message, output))
        task.add_done_callback(lambda _: self._tasks.pop(req_id, None))
        self._tasks[req_id] = task

    async def serve_stream(
        self, reader: asyncio.StreamReader, output: BinaryIO
    ) -> None:
        """Serve the messages read from a stream until it ends.

        Args:
            reader: Stream to read the messages of the client from.
            output: Binary file to write the messages of the server to.
        """
        while line := await reader.readline():
            self._receive(line, output)

        # NOTE(vytas): MCP stdio, 5. Shutdown.
        #   Servers SHOULD exit promptly when their standard input is closed
        #   or reads return end-of-file.
        # NOTE(vytas): The requests already read are still answered, though.
        if self._tasks:
            await asyncio.wait(self._tasks.values())

    async def serve(self) -> None:
        """Serve the client that launched this process until stdin is closed.

        Anything else that the app prints goes to stderr meanwhile.
        """
        loop = asyncio.get_running_loop()
        # NOTE(vytas): The client may send arbitrarily long lines.
        reader = asyncio.StreamReader(limit=sys.maxsize)

        # NOTE(vytas): Unlike asyncio pipes, blocking reads in a daemon thread
        #   work on any platform, leave stdin in blocking mode, and do not hold
        #   up exiting the process on Ctrl+C. They bypass sys.stdin, though,
        #   since the interpreter crashes on exit if a daemon thread is still
        #   blocked holding the lock of its buffer.
        def read_stdin() -> None:
            fd = sys.stdin.fileno()
            while data := os.read(fd, 65536):
                loop.call_soon_threadsafe(reader.feed_data, data)
            loop.call_soon_threadsafe(reader.feed_eof)

        threading.Thread(target=read_stdin, daemon=True).start()

        # NOTE(vytas): MCP stdio.
        #   The server MUST NOT write anything to its stdout that is not a
        #   valid MCP message.
        output = sys.stdout.buffer
        with contextlib.redirect_stdout(sys.stderr):
            await self.serve_stream(reader, output)
