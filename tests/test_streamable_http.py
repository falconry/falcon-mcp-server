import datetime
import importlib.util
import pathlib
import sys
from typing import TYPE_CHECKING

import falcon.asgi
import falcon.testing
import pytest

import falcon_mcp_server
from falcon_mcp_server import Context
from falcon_mcp_server import errors

if TYPE_CHECKING:
    from decimal import Decimal

VERSION = '2026-07-28'
META_VERSION = 'io.modelcontextprotocol/protocolVersion'
META_CAPABILITIES = 'io.modelcontextprotocol/clientCapabilities'
META_SERVER_INFO = 'io.modelcontextprotocol/serverInfo'

HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

ORIGIN = 'https://app.example'

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / 'examples'


@pytest.fixture
def client(app):
    return falcon.testing.TestClient(app)


def rpc(client, method, params=None, headers=None, meta=None, path='/mcp'):
    """Simulate a well-formed MCP request, unless overridden via kwargs."""
    if meta is None:
        meta = {META_VERSION: VERSION, META_CAPABILITIES: {}}
    params = {**(params or {}), '_meta': meta}

    req_headers = {
        'Accept': 'application/json, text/event-stream',
        'MCP-Protocol-Version': VERSION,
        'Mcp-Method': method,
    }
    if name := params.get('name') or params.get('uri'):
        req_headers['Mcp-Name'] = name
    for header, value in (headers or {}).items():
        if value is None:
            del req_headers[header]
        else:
            req_headers[header] = value

    return client.simulate_post(
        path,
        json={
            'jsonrpc': '2.0',
            'id': 1337,
            'method': method,
            'params': params,
        },
        headers=req_headers,
    )


def test_discover(client):
    resp = rpc(client, 'server/discover')
    assert resp.status_code == 200
    assert resp.json['id'] == 1337

    result = resp.json['result']
    server_info = result.pop('_meta')[META_SERVER_INFO]
    assert server_info['name'] == 'falcon-mcp-server'
    assert server_info['version'] == '1.0.0'
    assert server_info['title']
    assert 'falcon-mcp-server' in server_info['description']
    assert result == {
        'resultType': 'complete',
        'supportedVersions': [VERSION],
        'capabilities': {'prompts': {}, 'resources': {}, 'tools': {}},
        'ttlMs': 0,
        'cacheScope': 'private',
    }


def test_discover_capabilities():
    mcp = falcon_mcp_server.MCP()
    client = falcon.testing.TestClient(mcp.create_app())

    resp = rpc(client, 'server/discover')
    assert resp.status_code == 200
    assert resp.json['result']['capabilities'] == {}

    mcp.add_tool(ping)
    resp = rpc(client, 'server/discover')
    assert resp.status_code == 200
    assert resp.json['result']['capabilities'] == {'tools': {}}


def test_custom_identity():
    server_info = {
        'name': 'acme',
        'title': 'ACME Tools',
        'description': 'Tools for the ACME Corporation.',
        'version': '2.3.4',
    }
    mcp = falcon_mcp_server.MCP(
        instructions='Search before fetching.', **server_info
    )
    client = falcon.testing.TestClient(mcp.create_app())

    resp = rpc(client, 'server/discover')
    assert resp.status_code == 200
    result = resp.json['result']
    assert result['instructions'] == 'Search before fetching.'
    assert result['_meta'][META_SERVER_INFO] == server_info

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200
    result = resp.json['result']
    assert 'instructions' not in result
    assert result['_meta'][META_SERVER_INFO] == server_info


def test_custom_name_only():
    mcp = falcon_mcp_server.MCP(name='acme')
    client = falcon.testing.TestClient(mcp.create_app())

    resp = rpc(client, 'server/discover')
    assert resp.status_code == 200
    assert resp.json['result']['_meta'][META_SERVER_INFO] == {
        'name': 'acme',
        'version': '1.0.0',
    }


def test_tools_list(client):
    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200

    result = resp.json['result']
    assert result['resultType'] == 'complete'
    assert result['ttlMs'] == 0
    assert result['cacheScope'] == 'private'
    assert META_SERVER_INFO in result['_meta']
    assert [tool['name'] for tool in result['tools']] == ['greet']


def test_tools_call(client):
    resp = rpc(
        client, 'tools/call', {'name': 'greet', 'arguments': {'name': 'MCP'}}
    )
    assert resp.status_code == 200

    result = resp.json['result']
    assert result['resultType'] == 'complete'
    assert result['content'] == [{'type': 'text', 'text': 'Hello, MCP!'}]


def test_tools_call_without_arguments(client):
    resp = rpc(client, 'tools/call', {'name': 'greet'})
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'Hello, World!'


def test_resources(client):
    resp = rpc(client, 'resources/list')
    assert resp.status_code == 200
    result = resp.json['result']
    assert result['resultType'] == 'complete'
    assert result['cacheScope'] == 'private'
    assert [res['uri'] for res in result['resources']] == ['file:///hello.txt']

    resp = rpc(client, 'resources/read', {'uri': 'file:///hello.txt'})
    assert resp.status_code == 200
    result = resp.json['result']
    assert result['resultType'] == 'complete'
    assert result['cacheScope'] == 'private'
    assert result['contents'] == [
        {
            'uri': 'file:///hello.txt',
            'mimeType': 'text/plain',
            'text': 'Hello!',
        }
    ]

    resp = rpc(client, 'resources/templates/list')
    assert resp.status_code == 200
    result = resp.json['result']
    assert result['resultType'] == 'complete'
    assert result['cacheScope'] == 'private'
    assert result['resourceTemplates'] == []


async def ping() -> str:
    """Check whether the server is alive."""
    return 'pong'


def test_duplicate_tool(server):
    server.add_tool(ping)

    with pytest.raises(ValueError):
        server.add_tool(ping)
    with pytest.raises(ValueError):
        server.add_tool(ping, name='greet')


def test_tool_without_input_schema(client, server):
    server.add_tool(ping)

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200
    schemas = {
        tool['name']: tool['inputSchema']
        for tool in resp.json['result']['tools']
    }
    assert schemas['ping'] == {'type': 'object'}


async def forecast(city: str) -> str:
    """Get the weather forecast for a city.

    Args:
        city: The name of the city.
    """
    return f'It is always sunny in {city}.'


def test_tool_description(client, server):
    server.add_tool(forecast)
    server.add_tool(forecast, name='verbatim', description='  As given.\n')

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200
    descriptions = {
        tool['name']: tool['description']
        for tool in resp.json['result']['tools']
    }
    assert descriptions['forecast'] == (
        'Get the weather forecast for a city.\n'
        '\n'
        'Args:\n'
        '    city: The name of the city.'
    )
    assert descriptions['verbatim'] == '  As given.\n'


def test_tools_pagination(client, server):
    for index in range(40):
        server.add_tool(ping, name=f'ping{index}')

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200
    result = resp.json['result']
    assert len(result['tools']) == 32

    resp = rpc(client, 'tools/list', {'cursor': result['nextCursor']})
    assert resp.status_code == 200
    result = resp.json['result']
    assert len(result['tools']) == 9
    assert result['tools'][-1]['name'] == 'ping39'
    assert 'nextCursor' not in result


@pytest.mark.parametrize(
    'cursor', ['nope', 'tools', 'toolsX', 'tools-1', 'tools0001', 1337]
)
def test_invalid_cursor(client, cursor):
    resp = rpc(client, 'tools/list', {'cursor': cursor})
    assert resp.status_code == 400
    assert resp.json['error']['code'] == INVALID_PARAMS


def test_prompts_list(client):
    resp = rpc(client, 'prompts/list')
    assert resp.status_code == 200

    result = resp.json['result']
    assert result['resultType'] == 'complete'
    assert result['ttlMs'] == 0
    assert result['cacheScope'] == 'private'
    assert result['prompts'] == [
        {
            'name': 'summarize',
            'title': 'summarize',
            'description': 'Ask for a summary of a topic.',
            'arguments': [
                {'name': 'topic', 'required': True},
                {'name': 'style', 'required': False},
            ],
        }
    ]


def test_prompts_get(client):
    resp = rpc(
        client,
        'prompts/get',
        {'name': 'summarize', 'arguments': {'topic': 'MCP', 'style': 'long'}},
    )
    assert resp.status_code == 200

    result = resp.json['result']
    assert result['resultType'] == 'complete'
    assert result['description'] == 'Ask for a summary of a topic.'
    assert result['messages'] == [
        {
            'role': 'user',
            'content': {
                'type': 'text',
                'text': 'Write a long summary of MCP.',
            },
        }
    ]
    assert 'ttlMs' not in result


def test_prompts_get_default_argument(client):
    resp = rpc(
        client,
        'prompts/get',
        {'name': 'summarize', 'arguments': {'topic': 'X'}},
    )
    assert resp.status_code == 200
    content = resp.json['result']['messages'][0]['content']
    assert content['text'] == 'Write a brief summary of X.'


@pytest.mark.parametrize(
    'arguments',
    [
        None,
        {},
        {'style': 'long'},
        {'topic': 'MCP', 'tone': 'formal'},
        {'topic': 1337},
        ['MCP'],
    ],
)
def test_prompts_get_invalid_arguments(client, arguments):
    params = {'name': 'summarize'}
    if arguments is not None:
        params['arguments'] = arguments

    resp = rpc(client, 'prompts/get', params)
    assert resp.status_code == 400
    assert resp.json['error']['code'] == INVALID_PARAMS


async def whoami(context: Context, greeting: str = 'Hello') -> str:
    """Greet the account that the request is addressed to."""
    account = context.path_params['account']
    language = context.req.get_param('lang', default='en')
    version = context.meta[META_VERSION]
    return f'{greeting}, account {account} ({language}, MCP {version})!'


async def usage_report(period: str, context: Context) -> str:
    """Ask for a usage report of the account."""
    account = context.path_params['account']
    return f'Summarize the {period} usage of account {account}.'


@pytest.fixture
def account_client(server):
    server.add_tool(whoami)
    server.add_prompt(usage_report)

    app = falcon.asgi.App()
    app.add_route('/accounts/{account:int}/mcp', server)
    return falcon.testing.TestClient(app)


def test_tool_context(account_client):
    resp = rpc(
        account_client,
        'tools/call',
        {'name': 'whoami', 'arguments': {'greeting': 'Hej'}},
        path='/accounts/42/mcp?lang=sv',
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == (
        'Hej, account 42 (sv, MCP 2026-07-28)!'
    )

    resp = rpc(
        account_client,
        'tools/call',
        {'name': 'whoami'},
        path='/accounts/7/mcp',
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == (
        'Hello, account 7 (en, MCP 2026-07-28)!'
    )


def test_path_params_without_context(account_client):
    resp = rpc(
        account_client,
        'tools/call',
        {'name': 'greet', 'arguments': {'name': 'MCP'}},
        path='/accounts/42/mcp',
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'Hello, MCP!'

    resp = rpc(account_client, 'tools/list', path='/accounts/nope/mcp')
    assert resp.status_code == 404


@pytest.mark.parametrize(
    'arguments', [{'context': 'mine'}, {'context': None}, ['MCP'], 'MCP']
)
def test_tool_context_not_an_argument(account_client, arguments):
    resp = rpc(
        account_client,
        'tools/call',
        {'name': 'whoami', 'arguments': arguments},
        path='/accounts/42/mcp',
    )
    assert resp.status_code == 400
    assert resp.json['error']['code'] == INVALID_PARAMS


def test_prompt_context(account_client):
    resp = rpc(account_client, 'prompts/list', path='/accounts/42/mcp')
    assert resp.status_code == 200
    arguments = {
        prompt['name']: prompt['arguments']
        for prompt in resp.json['result']['prompts']
    }
    assert arguments['usage_report'] == [{'name': 'period', 'required': True}]

    resp = rpc(
        account_client,
        'prompts/get',
        {'name': 'usage_report', 'arguments': {'period': 'monthly'}},
        path='/accounts/42/mcp',
    )
    assert resp.status_code == 200
    message = resp.json['result']['messages'][0]
    assert message['content']['text'] == (
        'Summarize the monthly usage of account 42.'
    )

    resp = rpc(
        account_client,
        'prompts/get',
        {
            'name': 'usage_report',
            'arguments': {'period': 'monthly', 'context': 'mine'},
        },
        path='/accounts/42/mcp',
    )
    assert resp.status_code == 400
    assert resp.json['error']['code'] == INVALID_PARAMS


async def quoted(ctx: 'Context') -> str:
    return f'account {ctx.path_params["account"]}'


async def unresolvable(
    ctx: 'falcon_mcp_server.Context', amount: 'Decimal | None' = None
) -> str:
    return f'account {ctx.path_params["account"]}'


@pytest.mark.parametrize('tool', [quoted, unresolvable])
def test_context_string_annotation(account_client, server, tool):
    server.add_tool(tool)
    server.add_prompt(tool)

    resp = rpc(
        account_client,
        'tools/call',
        {'name': tool.__name__},
        path='/accounts/42/mcp',
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'account 42'

    resp = rpc(account_client, 'prompts/list', path='/accounts/42/mcp')
    assert resp.status_code == 200
    arguments = {
        prompt['name']: prompt['arguments']
        for prompt in resp.json['result']['prompts']
    }
    assert [arg['name'] for arg in arguments[tool.__name__]] == (
        ['amount'] if tool is unresolvable else []
    )


# NOTE: Kept as source, since annotations referring to undefined names cannot
#   even be compiled into a function before Python 3.14.
DEFERRED = """
async def deferred(ctx: Context, amount: Missing = None) -> Missing:
    return f'account {ctx.path_params["account"]}'
"""


@pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason='annotations are evaluated eagerly before Python 3.14 (PEP 649)',
)
def test_context_deferred_annotation(account_client, server):
    namespace = {'Context': Context}
    exec(DEFERRED, namespace)
    server.add_tool(namespace['deferred'])
    server.add_prompt(namespace['deferred'])

    resp = rpc(
        account_client,
        'tools/call',
        {'name': 'deferred'},
        path='/accounts/42/mcp',
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'account 42'


def read_only_filter(context, tool):
    if context.req.get_param_as_bool('read_only'):
        return bool(tool.annotations.get('readOnlyHint'))
    return True


@pytest.fixture
def filtered_client():
    mcp = falcon_mcp_server.MCP(tool_filter=read_only_filter)
    mcp.add_tool(ping, annotations={'readOnlyHint': True})
    mcp.add_tool(
        greet_all,
        annotations=falcon_mcp_server.ToolAnnotations(destructiveHint=True),
    )
    mcp.add_tool(ping, name='unannotated')
    return falcon.testing.TestClient(mcp.create_app())


async def greet_all() -> str:
    """Greet everyone, which cannot be undone."""
    return 'Hello, everyone!'


def test_tool_annotations(filtered_client):
    resp = rpc(filtered_client, 'tools/list')
    assert resp.status_code == 200
    annotations = {
        tool['name']: tool.get('annotations')
        for tool in resp.json['result']['tools']
    }
    assert annotations == {
        'ping': {'readOnlyHint': True},
        'greet_all': {'destructiveHint': True},
        'unannotated': None,
    }


def test_tool_meta(client, server):
    meta = {'ui': {'resourceUri': 'ui://weather/forecast.html'}}
    server.add_tool(ping, meta=meta)

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200
    listed = {
        tool['name']: tool.get('_meta')
        for tool in resp.json['result']['tools']
    }
    assert listed == {'greet': None, 'ping': meta}


@pytest.mark.parametrize(
    'result', [{'city': 'London', 'temperature': 15}, {}, [1, 2], 'text', None]
)
def test_structured_content(client, server, result):
    async def produce():
        return result

    server.add_tool(produce)

    resp = rpc(client, 'tools/call', {'name': 'produce'})
    assert resp.status_code == 200
    call_result = resp.json['result']
    assert [block['type'] for block in call_result['content']] == ['text']
    if isinstance(result, dict):
        assert call_result['structuredContent'] == result
    else:
        assert 'structuredContent' not in call_result


async def inventory() -> dict[str, list[str]]:
    return {'cities': ['London', 'New York']}


def test_result_serialized_as_json(client, server):
    server.add_tool(inventory)

    resp = rpc(client, 'tools/call', {'name': 'inventory'})
    assert resp.status_code == 200
    assert resp.json['result']['content'] == [
        {'type': 'text', 'text': '{"cities": ["London", "New York"]}'}
    ]


async def stamped() -> dict[str, object]:
    return {'cities': ['London'], 'updated': datetime.date(2026, 10, 13)}


def serialize_cities(result, context: Context):
    if context.req.get_param('format') == 'csv':
        return ','.join(result['cities'])
    if context.req.get_param('format') == 'xml':
        raise falcon.HTTPInvalidParam('XML is not supported.', 'format')
    return f'{len(result["cities"])} cities'


@pytest.fixture
def serializing_client():
    mcp = falcon_mcp_server.MCP(result_serializer=serialize_cities)
    mcp.add_tool(inventory)
    mcp.add_tool(ping)
    mcp.add_tool(stamped)
    return falcon.testing.TestClient(mcp.create_app())


@pytest.mark.parametrize(
    'path,text',
    [('/mcp', '2 cities'), ('/mcp?format=csv', 'London,New York')],
)
def test_result_serializer(serializing_client, path, text):
    resp = rpc(
        serializing_client, 'tools/call', {'name': 'inventory'}, path=path
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'] == [{'type': 'text', 'text': text}]
    assert resp.json['result']['structuredContent'] == {
        'cities': ['London', 'New York']
    }

    # NOTE: A string is passed on as is.
    resp = rpc(serializing_client, 'tools/call', {'name': 'ping'}, path=path)
    assert resp.status_code == 200
    assert resp.json['result'] == {
        **resp.json['result'],
        'content': [{'type': 'text', 'text': 'pong'}],
    }


# NOTE: A serializer need not take the context; str() has no signature
#   to inspect at all.
@pytest.mark.parametrize(
    'serializer,text',
    [
        (str, "{'cities': ['London', 'New York']}"),
        (lambda result: ' & '.join(result['cities']), 'London & New York'),
    ],
)
def test_result_serializer_without_context(serializer, text):
    mcp = falcon_mcp_server.MCP(result_serializer=serializer)
    mcp.add_tool(inventory)
    client = falcon.testing.TestClient(mcp.create_app())

    resp = rpc(client, 'tools/call', {'name': 'inventory'})
    assert resp.status_code == 200
    assert resp.json['result']['content'] == [{'type': 'text', 'text': text}]


def test_result_serializer_http_error(serializing_client, caplog):
    resp = rpc(
        serializing_client,
        'tools/call',
        {'name': 'inventory'},
        path='/mcp?format=xml',
    )
    assert resp.status_code == 400
    assert resp.json['title'] == 'Invalid parameter'
    assert not caplog.records


def test_result_serializer_beyond_json(serializing_client):
    resp = rpc(serializing_client, 'tools/call', {'name': 'stamped'})
    assert resp.status_code == 200
    # NOTE: The serializer copes with the date, but the JSON response would
    #   not, so the result is not repeated as structured content.
    assert resp.json['result']['content'] == [
        {'type': 'text', 'text': '1 cities'}
    ]
    assert 'structuredContent' not in resp.json['result']


async def unserializable() -> dict[str, object]:
    return {'secret': object()}


def test_result_serializer_error_concealed(client, server, caplog):
    server.add_tool(unserializable)

    resp = rpc(client, 'tools/call', {'name': 'unserializable'})
    assert resp.status_code == 200
    assert resp.json['error']['code'] == INTERNAL_ERROR
    assert 'unserializable' in resp.json['error']['message']
    assert 'object' not in resp.json['error']['message']
    assert 'TypeError' in caplog.text


def test_widget_example():
    spec = importlib.util.spec_from_file_location(
        'widget_example', EXAMPLES / 'widget.py'
    )
    widget = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(widget)
    client = falcon.testing.TestClient(widget.mcp.create_app())

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200
    (tool,) = resp.json['result']['tools']
    uri = tool['_meta']['ui']['resourceUri']
    assert uri.startswith('ui://')

    resp = rpc(client, 'resources/read', {'uri': uri})
    assert resp.status_code == 200
    (contents,) = resp.json['result']['contents']
    assert contents['mimeType'] == 'text/html;profile=mcp-app'
    assert 'ui/initialize' in contents['text']

    resp = rpc(
        client,
        'tools/call',
        {'name': tool['name'], 'arguments': {'city': 'London'}},
    )
    assert resp.status_code == 200
    assert resp.json['result']['structuredContent'] == {
        'city': 'London',
        'temperature': 15,
        'unit': '\xb0C',
    }

    resp = rpc(
        client,
        'tools/call',
        {'name': tool['name'], 'arguments': {'city': 'Atlantis'}},
    )
    assert resp.status_code == 200
    assert resp.json['result']['isError'] is True


def test_tool_filter(filtered_client):
    resp = rpc(filtered_client, 'tools/list', path='/mcp?read_only=1')
    assert resp.status_code == 200
    assert [tool['name'] for tool in resp.json['result']['tools']] == ['ping']

    resp = rpc(
        filtered_client,
        'tools/call',
        {'name': 'ping'},
        path='/mcp?read_only=1',
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'pong'

    resp = rpc(filtered_client, 'tools/call', {'name': 'greet_all'})
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'Hello, everyone!'


def test_filtered_tool_not_found(filtered_client):
    hidden = rpc(
        filtered_client,
        'tools/call',
        {'name': 'greet_all'},
        path='/mcp?read_only=1',
    )
    missing = rpc(filtered_client, 'tools/call', {'name': 'nope'})

    assert hidden.status_code == missing.status_code == 400
    assert hidden.json['error'] == missing.json['error']
    assert hidden.json['error']['code'] == INVALID_PARAMS


def test_tool_filter_pagination():
    def tool_filter(context, tool):
        return int(tool.name.removeprefix('ping')) % 4 != 0

    mcp = falcon_mcp_server.MCP(tool_filter=tool_filter)
    for index in range(48):
        mcp.add_tool(ping, name=f'ping{index}')
    client = falcon.testing.TestClient(mcp.create_app())

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200
    result = resp.json['result']
    assert len(result['tools']) == 32
    assert result['tools'][0]['name'] == 'ping1'

    resp = rpc(client, 'tools/list', {'cursor': result['nextCursor']})
    assert resp.status_code == 200
    result = resp.json['result']
    assert [tool['name'] for tool in result['tools']] == [
        'ping43',
        'ping45',
        'ping46',
        'ping47',
    ]
    assert 'nextCursor' not in result


async def dialogue(**kwargs) -> list:
    return [
        {'role': 'user', 'content': {'type': 'text', 'text': 'Knock knock.'}},
        {
            'role': 'assistant',
            'content': {'type': 'text', 'text': "Who's there?"},
        },
    ]


def test_prompt_with_messages(client, server):
    arguments = [{'name': 'mood', 'description': 'Mood of the joke'}]
    server.add_prompt(
        dialogue,
        title='Knock-knock joke',
        description='Start a joke.',
        arguments=arguments,
    )

    resp = rpc(client, 'prompts/list')
    assert resp.status_code == 200
    assert resp.json['result']['prompts'][-1] == {
        'name': 'dialogue',
        'title': 'Knock-knock joke',
        'description': 'Start a joke.',
        'arguments': arguments,
    }

    resp = rpc(
        client,
        'prompts/get',
        {'name': 'dialogue', 'arguments': {'mood': 'dry'}},
    )
    assert resp.status_code == 200
    roles = [message['role'] for message in resp.json['result']['messages']]
    assert roles == ['user', 'assistant']


async def broken() -> str:
    raise RuntimeError('out of ideas')


@pytest.mark.parametrize(
    'method,register',
    [('prompts/get', 'add_prompt'), ('tools/call', 'add_tool')],
)
def test_error_details_concealed(client, server, caplog, method, register):
    getattr(server, register)(broken)

    resp = rpc(client, method, {'name': 'broken'})
    assert resp.status_code == 200
    assert resp.json['error']['code'] == INTERNAL_ERROR
    assert 'broken' in resp.json['error']['message']
    assert 'out of ideas' not in resp.text
    assert 'RuntimeError' not in resp.text

    assert 'RuntimeError: out of ideas' in caplog.text
    assert 'Traceback' in caplog.text


def test_resource_error_details_concealed(client, server, caplog):
    server.add_resource('file:///broken.txt', 'text/plain', broken)

    resp = rpc(client, 'resources/read', {'uri': 'file:///broken.txt'})
    assert resp.status_code == 200
    assert resp.json['error']['code'] == INTERNAL_ERROR
    assert 'out of ideas' not in resp.text
    assert 'RuntimeError: out of ideas' in caplog.text


async def guarded() -> str:
    raise falcon.HTTPUnauthorized(
        description='The token has expired.', challenges=['Bearer']
    )


@pytest.mark.parametrize(
    'method,register',
    [('prompts/get', 'add_prompt'), ('tools/call', 'add_tool')],
)
def test_http_error_passed_through(client, server, caplog, method, register):
    getattr(server, register)(guarded)

    resp = rpc(client, method, {'name': 'guarded'})
    assert resp.status_code == 401
    assert resp.headers['WWW-Authenticate'] == 'Bearer'
    assert resp.json == {
        'title': '401 Unauthorized',
        'description': 'The token has expired.',
    }
    assert not caplog.records


def test_resource_http_error_passed_through(client, server, caplog):
    server.add_resource('file:///guarded.txt', 'text/plain', guarded)

    resp = rpc(client, 'resources/read', {'uri': 'file:///guarded.txt'})
    assert resp.status_code == 401
    assert resp.headers['WWW-Authenticate'] == 'Bearer'
    assert not caplog.records


async def teapot() -> str:
    raise falcon.HTTPStatus(falcon.HTTP_418)


def test_http_status_not_passed_through(client, server, caplog):
    server.add_tool(teapot)

    resp = rpc(client, 'tools/call', {'name': 'teapot'})
    assert resp.status_code == 200
    assert resp.json['error']['code'] == INTERNAL_ERROR
    assert 'HTTPStatus' in caplog.text


async def lookup(city: str = 'Atlantis') -> str:
    raise falcon_mcp_server.ToolError(f'Unknown city: {city}; try London.')


def test_tool_execution_error(client, server, caplog):
    server.add_tool(lookup)

    resp = rpc(client, 'tools/call', {'name': 'lookup'})
    assert resp.status_code == 200
    result = resp.json['result']
    assert result['resultType'] == 'complete'
    assert result['isError'] is True
    assert result['content'] == [
        {'type': 'text', 'text': 'Unknown city: Atlantis; try London.'}
    ]
    assert not caplog.records


async def picky(**kwargs) -> str:
    raise errors.RPCInvalidParam('Not to my taste')


@pytest.mark.parametrize(
    'method,register',
    [('prompts/get', 'add_prompt'), ('tools/call', 'add_tool')],
)
def test_rpc_error_passed_through(client, server, caplog, method, register):
    getattr(server, register)(picky)

    resp = rpc(client, method, {'name': 'picky'})
    assert resp.status_code == 400
    assert resp.json['error'] == {
        'code': INVALID_PARAMS,
        'message': 'Not to my taste',
    }
    assert not caplog.records


def test_duplicate_prompt(server):
    with pytest.raises(ValueError):
        server.add_prompt(dialogue, name='summarize')


def test_prompts_pagination(client, server):
    for index in range(40):
        server.add_prompt(dialogue, name=f'dialogue{index}')

    resp = rpc(client, 'prompts/list')
    assert resp.status_code == 200
    result = resp.json['result']
    assert len(result['prompts']) == 32

    resp = rpc(client, 'prompts/list', {'cursor': result['nextCursor']})
    assert resp.status_code == 200
    result = resp.json['result']
    assert len(result['prompts']) == 9
    assert result['prompts'][-1]['name'] == 'dialogue39'
    assert 'nextCursor' not in result

    resp = rpc(client, 'prompts/list', {'cursor': 'tools0032'})
    assert resp.status_code == 400
    assert resp.json['error']['code'] == INVALID_PARAMS


def test_binary_resource(client, server):
    uri = 'file:///data.bin'
    server.add_simple_resource(uri, 'application/octet-stream', b'\x00\xff')

    resp = rpc(client, 'resources/read', {'uri': uri})
    assert resp.status_code == 200
    assert resp.json['result']['contents'] == [
        {'uri': uri, 'mimeType': 'application/octet-stream', 'blob': 'AP8='}
    ]


def test_resource_callable(client, server):
    reads = []

    async def count_reads() -> str:
        """Number of times that this resource has been read."""
        reads.append(None)
        return f'Read {len(reads)} time(s).'

    uri = 'stats://reads'
    server.add_resource(uri, 'text/plain', count_reads)

    resp = rpc(client, 'resources/list')
    assert resp.status_code == 200
    assert resp.json['result']['resources'][-1] == {
        'uri': uri,
        'name': 'reads',
        'title': 'reads',
        'description': 'Number of times that this resource has been read.',
        'mimeType': 'text/plain',
    }

    for count in (1, 2):
        resp = rpc(client, 'resources/read', {'uri': uri})
        assert resp.status_code == 200
        assert resp.json['result']['contents'] == [
            {
                'uri': uri,
                'mimeType': 'text/plain',
                'text': f'Read {count} time(s).',
            }
        ]


async def report() -> bytes:
    """Latest usage report."""
    return b'\x00\xff'


def test_resource_metadata(client, server):
    latest = 'file:///reports/latest.bin'
    other = 'file:///reports/other.bin'
    media_type = 'application/octet-stream'
    server.add_resource(latest, media_type, report)
    server.add_resource(
        other,
        media_type,
        report,
        name='other',
        title='Other report',
        description='Another usage report.',
    )

    resp = rpc(client, 'resources/list')
    assert resp.status_code == 200
    assert resp.json['result']['resources'][-2:] == [
        {
            'uri': latest,
            'name': 'latest.bin',
            'title': 'latest.bin',
            'description': 'Latest usage report.',
            'mimeType': media_type,
        },
        {
            'uri': other,
            'name': 'other',
            'title': 'Other report',
            'description': 'Another usage report.',
            'mimeType': media_type,
        },
    ]

    resp = rpc(client, 'resources/read', {'uri': latest})
    assert resp.status_code == 200
    assert resp.json['result']['contents'] == [
        {'uri': latest, 'mimeType': media_type, 'blob': 'AP8='}
    ]


def test_resource_meta(client, server):
    uri = 'ui://example/view.html'
    media_type = 'text/html;profile=mcp-app'
    meta = {'ui': {'csp': {'connectDomains': ['https://api.example.com']}}}
    server.add_simple_resource(uri, media_type, '<p>Hi!</p>', meta=meta)

    resp = rpc(client, 'resources/list')
    assert resp.status_code == 200
    (listed,) = [
        res for res in resp.json['result']['resources'] if res['uri'] == uri
    ]
    assert listed['_meta'] == meta

    resp = rpc(client, 'resources/read', {'uri': uri})
    assert resp.status_code == 200
    assert resp.json['result']['contents'] == [
        {
            'uri': uri,
            'mimeType': media_type,
            'text': '<p>Hi!</p>',
            '_meta': meta,
        }
    ]


def test_duplicate_resource(server):
    with pytest.raises(ValueError):
        server.add_resource('file:///hello.txt', 'text/plain', report)
    with pytest.raises(ValueError):
        server.add_simple_resource('file:///hello.txt', 'text/plain', 'Hi!')


@pytest.mark.parametrize(
    'uri',
    [
        '',
        'report',
        '://report',
        '/reports/latest.txt',
        '1st://report',
        'file:///monthly report.txt',
        'file:///m\xe5nadsrapport.txt',
        'file:///report%2.txt',
        'file:///reports/{name}',
        'file:///report.txt\n',
    ],
)
def test_invalid_resource_uri(server, uri):
    with pytest.raises(ValueError, match='valid URI'):
        server.add_resource(uri, 'text/plain', report, name='report')
    with pytest.raises(ValueError, match='valid URI'):
        server.add_simple_resource(uri, 'text/plain', 'Hi!', name='report')


@pytest.mark.parametrize(
    'uri,name',
    [
        ('file:///monthly%20report.txt', 'monthly%20report.txt'),
        ('git://example.com/reports.git', 'reports.git'),
        ('https://example.com/reports/latest', 'latest'),
        ('x-reports+v1.0://latest', 'latest'),
    ],
)
def test_resource_default_name(client, server, uri, name):
    server.add_resource(uri, 'text/plain', report)

    resp = rpc(client, 'resources/list')
    assert resp.status_code == 200
    assert resp.json['result']['resources'][-1]['name'] == name


@pytest.mark.parametrize(
    'uri',
    [
        'urn:example:report',
        'mailto:reports@example.com',
        'reports://',
        'file:///reports/',
    ],
)
def test_resource_name_not_derivable(client, server, uri):
    with pytest.raises(ValueError, match='name'):
        server.add_resource(uri, 'text/plain', report)
    with pytest.raises(ValueError, match='name'):
        server.add_simple_resource(uri, 'text/plain', 'Hi!')

    server.add_resource(uri, 'text/plain', report, name='report')

    resp = rpc(client, 'resources/list')
    assert resp.status_code == 200
    assert resp.json['result']['resources'][-1]['name'] == 'report'


async def account_summary(context: Context) -> str:
    return f'Account {context.path_params["account"]}.'


def test_resource_context(account_client, server):
    server.add_resource('account://summary', 'text/plain', account_summary)

    resp = rpc(
        account_client,
        'resources/read',
        {'uri': 'account://summary'},
        path='/accounts/42/mcp',
    )
    assert resp.status_code == 200
    assert resp.json['result']['contents'][0]['text'] == 'Account 42.'


@pytest.mark.parametrize(
    'method,params',
    [
        ('tools/call', {'name': 'nope'}),
        ('tools/call', {}),
        ('resources/read', {'uri': 'file:///nope.txt'}),
        ('resources/read', {}),
        ('prompts/get', {'name': 'nope'}),
        ('prompts/get', {}),
    ],
)
def test_not_found(client, method, params):
    resp = rpc(client, method, params)
    assert resp.status_code == 400
    assert resp.json['id'] == 1337
    assert resp.json['error']['code'] == INVALID_PARAMS


@pytest.mark.parametrize(
    'method',
    ['completion/complete', 'initialize', 'logging/setLevel', 'ping'],
)
def test_method_not_found(client, method):
    resp = rpc(client, method)
    assert resp.status_code == 404
    assert resp.json['error']['code'] == METHOD_NOT_FOUND


@pytest.mark.parametrize('missing', [META_VERSION, META_CAPABILITIES])
def test_missing_meta(client, missing):
    meta = {META_VERSION: VERSION, META_CAPABILITIES: {}}
    del meta[missing]

    resp = rpc(client, 'tools/list', meta=meta)
    assert resp.status_code == 400
    assert resp.json['error']['code'] == INVALID_PARAMS
    assert missing in resp.json['error']['message']
    assert VERSION in resp.json['error']['message']


def test_unsupported_protocol_version(client):
    resp = rpc(
        client,
        'tools/list',
        headers={'MCP-Protocol-Version': '2025-11-25'},
        meta={META_VERSION: '2025-11-25', META_CAPABILITIES: {}},
    )
    assert resp.status_code == 400
    assert resp.json['error']['code'] == UNSUPPORTED_PROTOCOL_VERSION
    assert resp.json['error']['data'] == {
        'supported': [VERSION],
        'requested': '2025-11-25',
    }


@pytest.mark.parametrize(
    'method,params,headers,meta',
    [
        ('tools/list', {}, {'MCP-Protocol-Version': None}, None),
        (
            'tools/list',
            {},
            {},
            {META_VERSION: '2025-11-25', META_CAPABILITIES: {}},
        ),
        ('tools/list', {}, {'Mcp-Method': None}, None),
        ('tools/list', {}, {'Mcp-Method': 'tools/call'}, None),
        ('tools/call', {'name': 'greet'}, {'Mcp-Name': None}, None),
        ('tools/call', {'name': 'greet'}, {'Mcp-Name': 'nope'}, None),
        ('tools/call', {'name': 'greet'}, {'Mcp-Name': '=?base64?!?='}, None),
        (
            'resources/read',
            {'uri': 'file:///hello.txt'},
            {'Mcp-Name': 'file:///nope.txt'},
            None,
        ),
        ('prompts/get', {'name': 'summarize'}, {'Mcp-Name': 'nope'}, None),
    ],
)
def test_header_mismatch(client, method, params, headers, meta):
    resp = rpc(client, method, params, headers=headers, meta=meta)
    assert resp.status_code == 400
    assert resp.json['id'] == 1337
    assert resp.json['error']['code'] == HEADER_MISMATCH


def test_base64_name_header(client):
    resp = rpc(
        client,
        'tools/call',
        {'name': 'greet'},
        headers={'Mcp-Name': '=?base64?Z3JlZXQ=?='},
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'Hello, World!'


EXECUTE_SCHEMA = {
    'type': 'object',
    'properties': {
        'region': {'type': 'string', 'x-mcp-header': 'Region'},
        'limit': {'type': 'integer', 'x-mcp-header': 'Limit'},
        'dry_run': {'type': 'boolean', 'x-mcp-header': 'Dry-Run'},
        'filter': {
            'type': 'object',
            'properties': {
                'owner': {'type': 'string', 'x-mcp-header': 'Owner'},
            },
        },
        'query': {'type': 'string'},
    },
}


async def execute(**kwargs) -> str:
    return 'done'


# NOTE: The Base64 cases are the encoding examples from the spec.
@pytest.mark.parametrize(
    'arguments,headers',
    [
        ({}, {}),
        ({'query': 'SELECT 1'}, {}),
        ({'region': 'us-west1'}, {'Mcp-Param-Region': 'us-west1'}),
        ({'region': 'us-west1'}, {'mcp-param-region': 'us-west1'}),
        ({'region': None}, {}),
        ({'region': ''}, {'Mcp-Param-Region': ''}),
        ({}, {'Mcp-Param-Unknown': 'ignored'}),
        (
            {'region': 'Hello, 世界'},
            {'Mcp-Param-Region': '=?base64?SGVsbG8sIOS4lueVjA==?='},
        ),
        (
            {'region': ' padded '},
            {'Mcp-Param-Region': '=?base64?IHBhZGRlZCA=?='},
        ),
        (
            {'region': '=?base64?literal?='},
            {'Mcp-Param-Region': '=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?='},
        ),
        ({'limit': 42}, {'Mcp-Param-Limit': '42'}),
        ({'limit': 42}, {'Mcp-Param-Limit': '42.0'}),
        ({'limit': 42.0}, {'Mcp-Param-Limit': '42'}),
        ({'limit': -7}, {'Mcp-Param-Limit': '-7'}),
        ({'limit': 0}, {'Mcp-Param-Limit': '0'}),
        ({'dry_run': True}, {'Mcp-Param-Dry-Run': 'true'}),
        ({'dry_run': False}, {'Mcp-Param-Dry-Run': 'false'}),
        ({'filter': {'owner': 'me'}}, {'Mcp-Param-Owner': 'me'}),
        ({'filter': {}}, {}),
        ({'filter': 'mine'}, {}),
        (
            {'region': 'eu', 'limit': 5, 'dry_run': True, 'query': '1'},
            {
                'Mcp-Param-Region': 'eu',
                'Mcp-Param-Limit': '5',
                'Mcp-Param-Dry-Run': 'true',
            },
        ),
    ],
)
def test_param_headers(client, server, arguments, headers):
    server.add_tool(execute, input_schema=EXECUTE_SCHEMA)

    resp = rpc(
        client,
        'tools/call',
        {'name': 'execute', 'arguments': arguments},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'done'


@pytest.mark.parametrize(
    'arguments,headers',
    [
        ({'region': 'us-west1'}, {}),
        ({'region': 'us-west1'}, {'Mcp-Param-Region': 'eu-north1'}),
        ({'region': 'us-west1'}, {'Mcp-Param-Region': 'US-WEST1'}),
        ({'region': 'us-west1'}, {'Mcp-Param-Region': '=?base64?!?='}),
        ({'region': 'us-west1'}, {'Mcp-Param-Region': '=?base64?/w==?='}),
        ({'region': 'Malm\xf6'}, {'Mcp-Param-Region': 'Malm\xf6'}),
        ({'region': ''}, {}),
        ({'region': ['us-west1']}, {'Mcp-Param-Region': 'us-west1'}),
        ({'region': ['us-west1']}, {}),
        ({}, {'Mcp-Param-Region': 'us-west1'}),
        ({'region': None}, {'Mcp-Param-Region': 'null'}),
        ({'limit': 42}, {}),
        ({'limit': 42}, {'Mcp-Param-Limit': '43'}),
        ({'limit': 42}, {'Mcp-Param-Limit': '42.5'}),
        ({'limit': 42}, {'Mcp-Param-Limit': 'forty-two'}),
        ({'limit': 42}, {'Mcp-Param-Limit': '9' * 5000}),
        ({'limit': 42.5}, {'Mcp-Param-Limit': '42'}),
        ({'limit': 1}, {'Mcp-Param-Limit': 'true'}),
        ({'dry_run': True}, {}),
        ({'dry_run': True}, {'Mcp-Param-Dry-Run': 'True'}),
        ({'dry_run': True}, {'Mcp-Param-Dry-Run': '1'}),
        ({'dry_run': False}, {'Mcp-Param-Dry-Run': 'true'}),
        ({'filter': {'owner': 'me'}}, {}),
        ({'filter': {'owner': 'me'}}, {'Mcp-Param-Owner': 'you'}),
        ({'filter': 'mine'}, {'Mcp-Param-Owner': 'mine'}),
        (
            {'region': 'eu', 'limit': 5},
            {'Mcp-Param-Region': 'eu', 'Mcp-Param-Limit': '6'},
        ),
    ],
)
def test_param_header_mismatch(client, server, arguments, headers):
    server.add_tool(execute, input_schema=EXECUTE_SCHEMA)

    resp = rpc(
        client,
        'tools/call',
        {'name': 'execute', 'arguments': arguments},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json['id'] == 1337
    assert resp.json['error']['code'] == HEADER_MISMATCH
    assert 'Mcp-Param-' in resp.json['error']['message']


def test_param_headers_of_another_tool_ignored(client, server):
    server.add_tool(execute, input_schema=EXECUTE_SCHEMA)

    resp = rpc(
        client,
        'tools/call',
        {'name': 'greet', 'arguments': {'name': 'MCP'}},
        headers={'Mcp-Param-Region': 'us-west1'},
    )
    assert resp.status_code == 200
    assert resp.json['result']['content'][0]['text'] == 'Hello, MCP!'


@pytest.mark.parametrize('name', ['execute', 'nope'])
def test_param_headers_of_hidden_tool(name):
    mcp = falcon_mcp_server.MCP(tool_filter=lambda context, tool: False)
    mcp.add_tool(execute, input_schema=EXECUTE_SCHEMA)
    client = falcon.testing.TestClient(mcp.create_app())

    resp = rpc(
        client, 'tools/call', {'name': name, 'arguments': {'region': 'eu'}}
    )
    assert resp.status_code == 400
    assert resp.json['error'] == {
        'code': INVALID_PARAMS,
        'message': 'Tool not found',
    }


def test_param_header_schema_listed(client, server):
    server.add_tool(execute, input_schema=EXECUTE_SCHEMA)

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 200
    schemas = {
        tool['name']: tool['inputSchema']
        for tool in resp.json['result']['tools']
    }
    assert schemas['execute'] == EXECUTE_SCHEMA


def annotated(**schema):
    return {'type': 'object', 'properties': {'param': schema}}


@pytest.mark.parametrize(
    'input_schema',
    [
        annotated(**{'type': 'string', 'x-mcp-header': ''}),
        annotated(**{'type': 'string', 'x-mcp-header': 'Two Words'}),
        annotated(**{'type': 'string', 'x-mcp-header': 'Colon:'}),
        annotated(**{'type': 'string', 'x-mcp-header': 'R\xe9gion'}),
        annotated(**{'type': 'string', 'x-mcp-header': 'Line\nBreak'}),
        annotated(**{'type': 'string', 'x-mcp-header': 5}),
        annotated(**{'type': 'string', 'x-mcp-header': None}),
        annotated(**{'type': 'number', 'x-mcp-header': 'Param'}),
        annotated(**{'type': 'object', 'x-mcp-header': 'Param'}),
        annotated(**{'type': ['string', 'null'], 'x-mcp-header': 'Param'}),
        annotated(**{'x-mcp-header': 'Param'}),
        annotated(
            type='array', items={'type': 'string', 'x-mcp-header': 'Param'}
        ),
        annotated(oneOf=[{'type': 'string', 'x-mcp-header': 'Param'}]),
        annotated(
            type='array',
            items={
                'type': 'object',
                'properties': {
                    'nested': {'type': 'string', 'x-mcp-header': 'Param'},
                },
            },
        ),
        {
            'type': 'object',
            '$defs': {'param': {'type': 'string', 'x-mcp-header': 'Param'}},
        },
        {'type': 'object', 'x-mcp-header': 'Root'},
        {
            'type': 'object',
            'properties': {
                'one': {'type': 'string', 'x-mcp-header': 'Region'},
                'two': {'type': 'string', 'x-mcp-header': 'region'},
            },
        },
    ],
)
def test_invalid_param_header_annotation(server, input_schema):
    with pytest.raises(ValueError, match='x-mcp-header'):
        server.add_tool(execute, input_schema=input_schema)

    # NOTE: An invalid tool must not have been half-registered.
    server.add_tool(execute)


@pytest.mark.parametrize(
    'input_schema',
    [
        {'type': 'object', 'properties': {'x-mcp-header': {'type': 'string'}}},
        annotated(type='object', default={'x-mcp-header': 'Default'}),
        annotated(type='object', enum=[{'x-mcp-header': 'Enum'}]),
        annotated(type='object', const={'x-mcp-header': 'Const'}),
        annotated(type='object', examples=[{'x-mcp-header': 'Example'}]),
        {'type': 'object', 'properties': 'nope', 'items': 5},
    ],
)
def test_param_header_lookalikes(client, server, input_schema):
    server.add_tool(execute, input_schema=input_schema)

    resp = rpc(client, 'tools/call', {'name': 'execute'})
    assert resp.status_code == 200


def test_origin_not_allowed_by_default(client):
    resp = rpc(client, 'tools/list', headers={'Origin': ORIGIN})
    assert resp.status_code == 403


@pytest.mark.parametrize('allowed_origins', [ORIGIN, [ORIGIN], '*'])
def test_allowed_origin(allowed_origins):
    mcp = falcon_mcp_server.MCP(allowed_origins=allowed_origins)
    client = falcon.testing.TestClient(mcp.create_app())

    resp = rpc(client, 'tools/list', headers={'Origin': ORIGIN})
    assert resp.status_code == 200
    assert 'Access-Control-Allow-Origin' not in resp.headers


def test_other_origin_not_allowed():
    mcp = falcon_mcp_server.MCP(allowed_origins=[ORIGIN])
    client = falcon.testing.TestClient(mcp.create_app(cors_enable=True))

    resp = rpc(client, 'tools/list', headers={'Origin': 'https://evil.test'})
    assert resp.status_code == 403
    assert 'Access-Control-Allow-Origin' not in resp.headers


def test_cors():
    mcp = falcon_mcp_server.MCP(allowed_origins=[ORIGIN])
    client = falcon.testing.TestClient(mcp.create_app(cors_enable=True))

    resp = client.simulate_options(
        '/mcp',
        headers={
            'Origin': ORIGIN,
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type, mcp-method',
        },
    )
    assert resp.status_code == 200
    assert resp.headers['Access-Control-Allow-Origin'] == ORIGIN
    assert 'POST' in resp.headers['Access-Control-Allow-Methods']
    assert resp.headers['Access-Control-Allow-Headers'] == (
        'content-type, mcp-method'
    )

    resp = rpc(client, 'tools/list', headers={'Origin': ORIGIN})
    assert resp.status_code == 200
    assert resp.headers['Access-Control-Allow-Origin'] == ORIGIN


def test_legacy_initialize(client):
    resp = client.simulate_post(
        '/mcp',
        json={
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'initialize',
            'params': {
                'protocolVersion': '2025-11-25',
                'capabilities': {},
                'clientInfo': {'name': 'legacy', 'version': '1.0.0'},
            },
        },
        headers={'Accept': 'application/json, text/event-stream'},
    )
    assert resp.status_code == 400
    assert resp.json['id'] == 1
    assert resp.json['error']['code'] == HEADER_MISMATCH
    assert VERSION in resp.json['error']['message']


@pytest.mark.parametrize('method', ['DELETE', 'GET'])
def test_legacy_http_methods(client, method):
    resp = client.simulate_request(
        method, '/mcp', headers={'MCP-Session-Id': 'legacy'}
    )
    assert resp.status_code == 405


def test_notification(client):
    resp = client.simulate_post(
        '/mcp',
        json={'jsonrpc': '2.0', 'method': 'notifications/cancelled'},
        headers={'Accept': 'application/json, text/event-stream'},
    )
    assert resp.status_code == 202
    assert not resp.content


@pytest.mark.parametrize(
    'body',
    [
        ['not', 'an', 'object'],
        {'jsonrpc': '1.0', 'method': 'notifications/cancelled'},
    ],
)
def test_not_json_rpc(client, body):
    resp = client.simulate_post(
        '/mcp',
        json=body,
        headers={'Accept': 'application/json, text/event-stream'},
    )
    assert resp.status_code == 422


TOKEN = 'Bearer s3cr3t'
RESOURCE_METADATA = {
    'authorization_servers': ['https://auth.example'],
    'scopes_supported': ['mcp:read', 'mcp:write'],
}
METADATA_URL = (
    'http://falconframework.org/.well-known/oauth-protected-resource/mcp'
)


async def authenticate(context: Context) -> bool:
    return context.req.get_header('Authorization') == TOKEN


@pytest.fixture
def auth_client():
    mcp = falcon_mcp_server.MCP(
        authenticator=authenticate, resource_metadata=RESOURCE_METADATA
    )
    return falcon.testing.TestClient(mcp.create_app())


def test_authenticated(auth_client):
    resp = rpc(auth_client, 'tools/list', headers={'Authorization': TOKEN})
    assert resp.status_code == 200
    assert resp.json['result']['tools'] == []


def test_unauthenticated(auth_client):
    resp = rpc(auth_client, 'server/discover')
    assert resp.status_code == 401
    assert resp.headers['WWW-Authenticate'] == (
        f'Bearer resource_metadata="{METADATA_URL}", '
        f'scope="mcp:read mcp:write"'
    )


def test_invalid_token(auth_client):
    resp = rpc(
        auth_client, 'server/discover', headers={'Authorization': 'Bearer x'}
    )
    assert resp.status_code == 401
    assert resp.headers['WWW-Authenticate'] == (
        f'Bearer error="invalid_token", '
        f'resource_metadata="{METADATA_URL}", '
        f'scope="mcp:read mcp:write"'
    )


def test_authentication_before_header_validation(auth_client):
    headers = {'MCP-Protocol-Version': None, 'Mcp-Method': None}
    resp = rpc(auth_client, 'tools/list', headers=headers)
    assert resp.status_code == 401


def test_unauthenticated_notification(auth_client):
    resp = auth_client.simulate_post(
        '/mcp',
        json={'jsonrpc': '2.0', 'method': 'notifications/cancelled'},
        headers={'Accept': 'application/json, text/event-stream'},
    )
    assert resp.status_code == 401


def test_origin_checked_before_authentication(auth_client):
    resp = rpc(auth_client, 'tools/list', headers={'Origin': ORIGIN})
    assert resp.status_code == 403


def test_authenticator_http_error_passed_through():
    async def forbid(context: Context) -> bool:
        raise falcon.HTTPForbidden(description='Account suspended.')

    mcp = falcon_mcp_server.MCP(authenticator=forbid)
    client = falcon.testing.TestClient(mcp.create_app())

    resp = rpc(client, 'tools/list', headers={'Authorization': TOKEN})
    assert resp.status_code == 403
    assert resp.json['description'] == 'Account suspended.'


@pytest.mark.parametrize(
    'authorization, challenge',
    [(None, 'Bearer'), ('Bearer x', 'Bearer error="invalid_token"')],
)
def test_authenticator_without_metadata(authorization, challenge):
    mcp = falcon_mcp_server.MCP(authenticator=authenticate)
    client = falcon.testing.TestClient(mcp.create_app())

    headers = {'Authorization': authorization} if authorization else {}
    resp = rpc(client, 'tools/list', headers=headers)
    assert resp.status_code == 401
    assert resp.headers['WWW-Authenticate'] == challenge

    resp = client.simulate_get('/.well-known/oauth-protected-resource/mcp')
    assert resp.status_code == 404


def test_protected_resource_metadata(auth_client):
    resp = auth_client.simulate_get(
        '/.well-known/oauth-protected-resource/mcp'
    )
    assert resp.status_code == 200
    assert resp.json == {
        'resource': 'http://falconframework.org/mcp',
        'bearer_methods_supported': ['header'],
        **RESOURCE_METADATA,
    }


def test_protected_resource_metadata_overrides():
    metadata = {
        'resource': 'https://mcp.example/mcp',
        'authorization_servers': ['https://auth.example'],
        'bearer_methods_supported': ['header', 'body'],
    }
    mcp = falcon_mcp_server.MCP(resource_metadata=metadata)
    client = falcon.testing.TestClient(mcp.create_app())

    resp = client.simulate_get('/.well-known/oauth-protected-resource/mcp')
    assert resp.json == metadata


def test_protected_resource_metadata_forwarded(auth_client):
    headers = {'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'mcp.example'}

    resp = auth_client.simulate_get(
        '/.well-known/oauth-protected-resource/mcp', headers=headers
    )
    assert resp.json['resource'] == 'https://mcp.example/mcp'

    resp = rpc(auth_client, 'tools/list', headers=headers)
    assert resp.status_code == 401
    assert resp.headers['WWW-Authenticate'].startswith(
        'Bearer resource_metadata="https://mcp.example'
        '/.well-known/oauth-protected-resource/mcp"'
    )


def test_protected_resource_metadata_templated_route():
    contexts = []

    async def authenticate_account(context: Context) -> bool:
        contexts.append(context)
        return False

    mcp = falcon_mcp_server.MCP(
        authenticator=authenticate_account,
        resource_metadata=RESOURCE_METADATA,
    )
    app = falcon.asgi.App()
    app.add_route('/accounts/{account:int}/mcp', mcp)
    app.add_route(
        '/.well-known/oauth-protected-resource/accounts/{account:int}/mcp',
        mcp,
        suffix='metadata',
    )
    client = falcon.testing.TestClient(app)

    resp = rpc(client, 'tools/list', path='/accounts/42/mcp')
    assert resp.status_code == 401
    assert resp.headers['WWW-Authenticate'].startswith(
        'Bearer resource_metadata="http://falconframework.org'
        '/.well-known/oauth-protected-resource/accounts/42/mcp"'
    )
    (context,) = contexts
    assert context.path_params == {'account': 42}
    assert context.meta[META_VERSION] == VERSION

    resp = client.simulate_get(
        '/.well-known/oauth-protected-resource/accounts/42/mcp'
    )
    assert (
        resp.json['resource'] == 'http://falconframework.org/accounts/42/mcp'
    )


def test_metadata_responder_without_metadata(server):
    app = falcon.asgi.App()
    app.add_route(
        '/.well-known/oauth-protected-resource', server, suffix='metadata'
    )
    client = falcon.testing.TestClient(app)

    resp = client.simulate_get('/.well-known/oauth-protected-resource')
    assert resp.status_code == 404


def test_responder_hooks():
    async def forbid(req, resp, resource, params):
        raise falcon.HTTPForbidden()

    @falcon.before(forbid)
    class GuardedMCP(falcon_mcp_server.MCP):
        pass

    client = falcon.testing.TestClient(GuardedMCP().create_app())

    resp = rpc(client, 'tools/list')
    assert resp.status_code == 403
