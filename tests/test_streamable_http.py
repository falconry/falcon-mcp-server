import falcon.testing
import pytest

import falcon_mcp_server

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


def rpc(client, method, params=None, headers=None, meta=None):
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
        '/mcp',
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


def test_prompt_error(client, server):
    server.add_prompt(broken)

    resp = rpc(client, 'prompts/get', {'name': 'broken'})
    assert resp.status_code == 200
    assert resp.json['error']['code'] == INTERNAL_ERROR
    assert 'out of ideas' in resp.json['error']['message']


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
