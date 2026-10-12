"""Integration tests using the client from the official MCP Python SDK."""

import pytest

import falcon_mcp_server

httpx2 = pytest.importorskip('httpx2')
mcp = pytest.importorskip('mcp')

from mcp.client.streamable_http import streamable_http_client  # noqa: E402


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def connect(app, mode='auto'):
    http_client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
    transport = streamable_http_client(
        'http://localhost/mcp', http_client=http_client
    )
    return mcp.Client(transport, mode=mode)


# NOTE(vytas): "auto" probes the server via server/discover first, whereas a
#   pinned protocol version skips straight to the actual requests.
@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['auto', '2026-07-28'])
async def test_sdk_client(app, mode):
    async with connect(app, mode) as client:
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

        prompts = await client.list_prompts()
        assert [prompt.name for prompt in prompts.prompts] == ['summarize']
        assert [arg.name for arg in prompts.prompts[0].arguments] == [
            'topic',
            'style',
        ]

        result = await client.get_prompt('summarize', {'topic': 'the SDK'})
        assert result.messages[0].role == 'user'
        assert result.messages[0].content.text == (
            'Write a brief summary of the SDK.'
        )

        with pytest.raises(mcp.MCPError) as excinfo:
            await client.call_tool('nope')
        assert excinfo.value.code == -32602


@pytest.mark.anyio
async def test_sdk_client_discovery():
    server = falcon_mcp_server.MCP(
        name='acme',
        title='ACME Tools',
        version='2.3.4',
        instructions='Search before fetching.',
    )

    async with connect(server.create_app()) as client:
        assert client.server_info.name == 'acme'
        assert client.server_info.title == 'ACME Tools'
        assert client.server_info.version == '2.3.4'
        assert client.instructions == 'Search before fetching.'
