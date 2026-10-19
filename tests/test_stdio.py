import asyncio
import io
import json
import logging
import os
import sys
import threading

import falcon
import pytest

import falcon_mcp_server
from falcon_mcp_server import Context

META_VERSION = 'io.modelcontextprotocol/protocolVersion'
META = {
    META_VERSION: '2026-07-28',
    'io.modelcontextprotocol/clientCapabilities': {},
}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022


def request(req_id, method, **params):
    return {
        'jsonrpc': '2.0',
        'id': req_id,
        'method': method,
        'params': {'_meta': META, **params},
    }


def cancel(req_id):
    return {
        'jsonrpc': '2.0',
        'method': 'notifications/cancelled',
        'params': {'requestId': req_id},
    }


def encode(*messages):
    """Encode messages as lines, passing raw bytes through as they are."""
    return b''.join(
        msg if isinstance(msg, bytes) else json.dumps(msg).encode() + b'\n'
        for msg in messages
    )


@pytest.fixture
def patch_stdio(monkeypatch):
    """Return a function replacing stdin with a pipe, and stdout with a buffer.

    The function returns the write end of the pipe, and the buffer.
    """
    # NOTE: Patched by the test itself rather than by a fixture, since pytest
    #   restores its own sys.stdout for capturing when the test is called.
    stdins = []

    def patch_stdio():
        read_fd, write_fd = os.pipe()
        stdin = io.TextIOWrapper(open(read_fd, 'rb'))
        stdins.append(stdin)
        stdout = io.BytesIO()
        monkeypatch.setattr(sys, 'stdin', stdin)
        monkeypatch.setattr(sys, 'stdout', io.TextIOWrapper(stdout))
        return write_fd, stdout

    yield patch_stdio

    for stdin in stdins:
        stdin.close()


def decode(stdout):
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


@pytest.fixture
def run(patch_stdio):
    """Run a server over stdio until it has read the given messages."""

    def run(server, *messages):
        write_fd, stdout = patch_stdio()

        # NOTE: Written from a thread, as a pipe only holds so much (64 KiB
        #   on Linux) until it is read from.
        def write():
            with open(write_fd, 'wb') as stdin:
                stdin.write(encode(*messages))

        writer = threading.Thread(target=write)
        writer.start()
        server.run_stdio()
        writer.join()

        return decode(stdout)

    return run


async def greet(name: str = 'World') -> str:
    """Greet someone by name."""
    return f'Hello, {name}!'


@pytest.fixture
def server():
    mcp = falcon_mcp_server.MCP()
    mcp.add_tool(greet)
    return mcp


def test_requests(run, server):
    discover, tools, call = run(
        server,
        request(1, 'server/discover'),
        request('two', 'tools/list'),
        request(3, 'tools/call', name='greet', arguments={'name': 'stdio'}),
    )

    assert discover['id'] == 1
    assert discover['result']['supportedVersions'] == ['2026-07-28']
    assert tools['id'] == 'two'
    assert [tool['name'] for tool in tools['result']['tools']] == ['greet']
    assert call['id'] == 3
    assert call['result']['content'] == [
        {'type': 'text', 'text': 'Hello, stdio!'}
    ]


def test_long_message(run, server):
    # NOTE: Longer than asyncio's default limit for a line (64 KiB), and read
    #   from stdin in several chunks.
    name = 'x' * (1024 * 1024)
    responses = run(
        server,
        request(1, 'tools/call', name='greet', arguments={'name': name}),
        # NOTE: The last message lacks a newline before the end of stdin.
        encode(request(2, 'tools/list')).rstrip(b'\n'),
    )
    call, tools = sorted(responses, key=lambda resp: resp['id'])
    assert call['result']['content'][0]['text'] == f'Hello, {name}!'
    assert [tool['name'] for tool in tools['result']['tools']] == ['greet']


def test_utf8(patch_stdio, server):
    message = request(
        1, 'tools/call', name='greet', arguments={'name': 'Šiauliai'}
    )
    write_fd, stdout = patch_stdio()
    os.write(
        write_fd, json.dumps(message, ensure_ascii=False).encode() + b'\n'
    )
    os.close(write_fd)

    server.run_stdio()
    assert '"text": "Hello, Šiauliai!"'.encode() in stdout.getvalue()


async def describe_request(context: Context) -> dict:
    return {
        'host': context.req.host,
        'headers': context.req.get_header('Authorization'),
        'path_params': context.path_params,
        'version': context.meta[META_VERSION],
    }


def test_context(run, server):
    server.add_tool(describe_request)

    (resp,) = run(server, request(1, 'tools/call', name='describe_request'))
    assert resp['result']['structuredContent'] == {
        'host': 'localhost',
        'headers': None,
        'path_params': {},
        'version': '2026-07-28',
    }


async def chatty() -> str:
    print('Talking to stdout...')
    return 'Done.'


def test_print_goes_to_stderr(capsys, run, server):
    server.add_tool(chatty)

    (resp,) = run(server, request(1, 'tools/call', name='chatty'))
    assert resp['result']['content'][0]['text'] == 'Done.'
    assert 'Talking to stdout...' in capsys.readouterr().err


@pytest.mark.parametrize(
    'message,req_id,code',
    [
        (b'{"jsonrpc": "2.0", "id": 1\n', None, PARSE_ERROR),
        (b'["tools/list"]\n', None, INVALID_REQUEST),
        ({'jsonrpc': '2.0', 'method': 'tools/list'}, None, INVALID_REQUEST),
        ({**request(1, 'tools/list'), 'id': None}, None, INVALID_REQUEST),
        ({**request(1, 'tools/list'), 'id': True}, None, INVALID_REQUEST),
        ({**request(1, 'tools/list'), 'jsonrpc': '1.0'}, 1, INVALID_REQUEST),
        ({**request(1, 'tools/list'), 'params': {}}, 1, INVALID_PARAMS),
        (request(1.5, 'resources/subscribe'), 1.5, METHOD_NOT_FOUND),
        (
            request(1, 'tools/list', _meta={**META, META_VERSION: '2025'}),
            1,
            UNSUPPORTED_PROTOCOL_VERSION,
        ),
    ],
)
def test_errors(run, server, message, req_id, code):
    (resp,) = run(server, message)
    assert resp['jsonrpc'] == '2.0'
    assert resp['id'] == req_id
    assert resp['error']['code'] == code


def test_legacy_initialize(run, server):
    (resp,) = run(
        server,
        {
            'jsonrpc': '2.0',
            'id': 0,
            'method': 'initialize',
            'params': {
                'protocolVersion': '2025-11-25',
                'capabilities': {},
                'clientInfo': {'name': 'legacy', 'version': '1.0.0'},
            },
        },
    )
    assert resp['id'] == 0
    assert resp['error']['code'] == INVALID_PARAMS
    assert '2026-07-28' in resp['error']['message']


def test_ignored_messages(run, server):
    assert (
        run(
            server,
            b'\n',
            b'  \r\n',
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 1, 'result': {}},
            cancel(404),
            cancel([404]),
        )
        == []
    )


async def deny_all(context: Context) -> bool:
    return False


def test_authenticator_refused(run):
    server = falcon_mcp_server.MCP(authenticator=deny_all)
    server.add_tool(greet)

    with pytest.raises(RuntimeError, match='only applies to HTTP'):
        run(server, request(1, 'tools/call', name='greet'))


async def not_found(city: str) -> str:
    raise falcon.HTTPNotFound(description=f'No weather for {city}.')


def test_http_error(run, server):
    server.add_tool(not_found)

    (resp,) = run(
        server,
        request(1, 'tools/call', name='not_found', arguments={'city': 'Oz'}),
    )
    assert resp['error'] == {
        'code': INTERNAL_ERROR,
        'message': '404 Not Found: No weather for Oz.',
    }


def faulty_filter(context, tool):
    raise RuntimeError('Oops!')


def test_unexpected_error(caplog, run):
    server = falcon_mcp_server.MCP(tool_filter=faulty_filter)
    server.add_tool(greet)

    with caplog.at_level(logging.ERROR, logger='falcon_mcp_server'):
        (resp,) = run(server, request(1, 'tools/list'))

    assert resp['error'] == {
        'code': INTERNAL_ERROR,
        'message': 'Internal error',
    }
    assert 'Error handling tools/list' in caplog.text
    assert 'Oops!' in caplog.text


def test_concurrent_requests(run, server):
    released = asyncio.Event()

    async def wait() -> str:
        await released.wait()
        return 'Waited.'

    async def release() -> str:
        released.set()
        return 'Released.'

    server.add_tool(wait)
    server.add_tool(release)

    # NOTE: stdin is closed before the first request completes; it is still
    #   answered, after the second one.
    responses = run(
        server,
        request(1, 'tools/call', name='wait'),
        request(2, 'tools/call', name='release'),
    )
    assert [resp['id'] for resp in responses] == [2, 1]
    assert responses[1]['result']['content'][0]['text'] == 'Waited.'


def test_cancel(patch_stdio, server):
    started = asyncio.Event()
    cancelled = []

    async def wait_forever() -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return 'Unreachable.'

    server.add_tool(wait_forever)

    write_fd, stdout = patch_stdio()

    async def main():
        serving = asyncio.create_task(server.serve_stdio())

        os.write(
            write_fd, encode(request(1, 'tools/call', name='wait_forever'))
        )
        await asyncio.wait_for(started.wait(), timeout=5)

        os.write(write_fd, encode(cancel(1), request(2, 'tools/list')))
        os.close(write_fd)
        await asyncio.wait_for(serving, timeout=5)

    asyncio.run(main())

    assert cancelled == [True]
    (resp,) = decode(stdout)
    assert resp['id'] == 2
    assert 'result' in resp
