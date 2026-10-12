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
    mcp.add_tool(greet_all, annotations={'destructiveHint': True})
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


def break_resource(server, exception):
    async def fetch():
        raise exception

    # NOTE: There is no public way to add a resource that can fail yet.
    resources = server._rpc_server.resources._resources
    resources['file:///hello.txt'].fetch = fetch


def test_resource_error_details_concealed(client, server, caplog):
    break_resource(server, RuntimeError('out of ideas'))

    resp = rpc(client, 'resources/read', {'uri': 'file:///hello.txt'})
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
    break_resource(server, falcon.HTTPForbidden())

    resp = rpc(client, 'resources/read', {'uri': 'file:///hello.txt'})
    assert resp.status_code == 403
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
