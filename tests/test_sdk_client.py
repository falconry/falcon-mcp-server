"""Integration tests using the client from the official MCP Python SDK."""

import pathlib
import sys

import pytest

import falcon_mcp_server
from falcon_mcp_server import Context

httpx2 = pytest.importorskip('httpx2')
mcp = pytest.importorskip('mcp')

from mcp.client.stdio import StdioServerParameters  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / 'examples'


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


async def lookup(city: str) -> str:
    raise falcon_mcp_server.ToolError(f'Unknown city: {city}')


@pytest.mark.anyio
async def test_sdk_client_tool_error():
    server = falcon_mcp_server.MCP()
    server.add_tool(lookup)

    async with connect(server.create_app()) as client:
        result = await client.call_tool('lookup', {'city': 'Atlantis'})
        assert result.is_error
        assert result.content[0].text == 'Unknown city: Atlantis'


async def forecast(city: str) -> dict[str, str | int]:
    return {'city': city, 'temperature': 15}


@pytest.mark.anyio
async def test_sdk_client_structured_content():
    meta = {'ui': {'resourceUri': 'ui://weather/forecast.html'}}
    server = falcon_mcp_server.MCP()
    server.add_tool(forecast, meta=meta)

    async with connect(server.create_app()) as client:
        tools = await client.list_tools()
        assert tools.tools[0].meta == meta

        result = await client.call_tool('forecast', {'city': 'London'})
        assert not result.is_error
        assert result.structured_content == {
            'city': 'London',
            'temperature': 15,
        }


async def whereami(context: Context) -> str:
    """Path that the request was sent to."""
    return context.req.path


@pytest.mark.anyio
async def test_sdk_client_resource_callable():
    server = falcon_mcp_server.MCP()
    server.add_resource('server://path', 'text/plain', whereami)

    async with connect(server.create_app()) as client:
        resources = await client.list_resources()
        assert [res.description for res in resources.resources] == [
            'Path that the request was sent to.'
        ]

        result = await client.read_resource('server://path')
        assert result.contents[0].text == '/mcp'


LOCATE_SCHEMA = {
    'type': 'object',
    'properties': {
        'region': {'type': 'string', 'x-mcp-header': 'Region'},
        'limit': {'type': 'integer', 'x-mcp-header': 'Limit'},
        'exact': {'type': 'boolean', 'x-mcp-header': 'Exact'},
        'filter': {
            'type': 'object',
            'properties': {
                'owner': {'type': 'string', 'x-mcp-header': 'Owner'},
            },
        },
    },
}


async def locate(context: Context, **kwargs) -> str:
    """Echo the headers that the client mirrored the arguments into."""
    names = ('Region', 'Limit', 'Exact', 'Owner')
    headers = (context.req.get_header(f'Mcp-Param-{name}') for name in names)
    return ' '.join(header or '-' for header in headers)


# NOTE(vytas): The server rejects the calls where the headers do not mirror
#   the arguments, so a result also means that the client got them right.
@pytest.mark.anyio
@pytest.mark.parametrize('list_tools', [True, False])
async def test_sdk_client_param_headers(list_tools):
    server = falcon_mcp_server.MCP()
    server.add_tool(locate, input_schema=LOCATE_SCHEMA)

    async with connect(server.create_app()) as client:
        if list_tools:
            await client.list_tools()

        result = await client.call_tool(
            'locate', {'region': 'us-west1', 'limit': 5, 'exact': True}
        )
        assert result.content[0].text == 'us-west1 5 true -'

        result = await client.call_tool(
            'locate', {'region': 'Malm\xf6', 'filter': {'owner': 'me'}}
        )
        assert result.content[0].text == '=?base64?TWFsbcO2?= - - me'

        result = await client.call_tool(
            'locate', {'region': None, 'limit': 0, 'exact': False}
        )
        assert result.content[0].text == '- 0 false -'

        result = await client.call_tool('locate', {})
        assert result.content[0].text == '- - - -'


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


@pytest.mark.anyio
async def test_sdk_client_stdio():
    server = StdioServerParameters(
        command=sys.executable, args=[str(EXAMPLES / 'example.py')]
    )

    async with mcp.Client(server) as client:
        assert client.protocol_version == '2026-07-28'

        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ['get_temperature']

        result = await client.call_tool('get_temperature', {'city': 'London'})
        assert not result.is_error
        assert result.content[0].text == (
            'The current temperature in London is 15°C.'
        )
