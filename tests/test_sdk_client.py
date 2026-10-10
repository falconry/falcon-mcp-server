"""Integration tests using the client from the official MCP Python SDK."""

import pytest

httpx2 = pytest.importorskip('httpx2')
mcp = pytest.importorskip('mcp')

from mcp.client.streamable_http import streamable_http_client  # noqa: E402


@pytest.fixture
def anyio_backend():
    return 'asyncio'


# NOTE(vytas): "auto" probes the server via server/discover first, whereas a
#   pinned protocol version skips straight to the actual requests.
@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['auto', '2026-07-28'])
async def test_sdk_client(app, mode):
    http_client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
    transport = streamable_http_client(
        'http://localhost/mcp', http_client=http_client
    )

    async with mcp.Client(transport, mode=mode) as client:
        assert client.protocol_version == '2026-07-28'

        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ['greet']

        result = await client.call_tool('greet', {'name': 'SDK'})
        assert not result.is_error
        assert result.content[0].text == 'Hello, SDK!'

        resources = await client.list_resources()
        assert [str(res.uri) for res in resources.resources] == [
            'file:///hello.txt'
        ]

        result = await client.read_resource('file:///hello.txt')
        assert result.contents[0].text == 'Hello!'

        with pytest.raises(mcp.MCPError) as excinfo:
            await client.call_tool('nope')
        assert excinfo.value.code == -32602
