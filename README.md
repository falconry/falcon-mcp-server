# falcon-mcp-server

[![PyPI - Version](https://img.shields.io/pypi/v/falcon-mcp-server.svg)](https://pypi.org/project/falcon-mcp-server)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/falcon-mcp-server.svg)](https://pypi.org/project/falcon-mcp-server)

`falcon-mcp-server` is a lightweight library for building MCP server endpoints
on top of your [Falcon](https://github.com/falconry/falcon) application.

> **Warning:** `falcon-mcp-server` is a fast-moving project in its early
> stages. *Caveat emptor!*

> **Note:** `falcon-mcp-server` was upgraded to the current version of the MCP
> standard with substantial help from LLMs, under human supervision.

-----

## Table of Contents

- [Installation](#installation)
- [Example Usage](#example-usage)
- [License](#license)

## Installation

Install from PyPI:
```console
pip install falcon-mcp-server[serve]
```

This is a fast-moving project in its early stages where new versions are not
always immediately released to PyPI. To install directly from the `main` branch:
```console
pip install git+https://github.com/falconry/falcon-mcp-server
```

## Example Usage

See the example in ``examples/example.py``, which exposes a tool, a prompt
and a resource.

Change dir to ``examples/``, and run with Uvicorn:
```console
uvicorn --log-config logging.yaml --factory example:mcp.create_app
```

Connect with any AI agent or MCP inspector supporting the
[Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
transport as of the `2026-07-28` revision of MCP. Earlier protocol revisions
(the ones relying on the `initialize` handshake and sessions) are not
supported.

For instance, assuming you have both
[fast-agent](https://github.com/evalstate/fast-agent)
(`pip install fast-agent-mcp`) and [Ollama](https://ollama.com/) installed on
your system, you can run it against the previously started server (see above)
as:
```console
fast-agent go --url http://localhost:8000/mcp --model generic.granite4:3b --message "What is the current temperature in London?"
```

Omit `--message` to run interactively.
Feel free to `pull` and use a different `ollama` model!

The server can introduce itself to clients under your own name, and provide
instructions that clients may pass on to the LLM alongside the tools:
```python
mcp = falcon_mcp_server.MCP(
    name='weather-example',
    title='Weather Example',
    version='1.2.3',
    instructions='Temperatures are only known for New York and London.',
)
```

A tool or prompt receives the context of the request via the parameter that it
annotates with `Context`, if any. The context carries the Falcon request and,
if you add the end point to your app on a templated route, the values of its
fields:
```python
import falcon.asgi

from falcon_mcp_server import Context


async def get_quota(context: Context) -> str:
    account = context.path_params['account']
    token = context.req.auth
    ...


mcp.add_tool(get_quota, annotations={'readOnlyHint': True})

app = falcon.asgi.App()
app.add_route('/{account:int}/mcp', mcp)
```

The `annotations` are hints describing the behavior of a tool to clients. The
dictionary is typed as `ToolAnnotations`, so a type checker catches a misspelt
or non-boolean hint.

The tools exposed to a request can be narrowed down with a filter. A tool that
is filtered out is neither listed nor callable:
```python
def tool_filter(context, tool):
    if context.req.get_param_as_bool('read_only'):
        return bool(tool.annotations.get('readOnlyHint'))
    return True


mcp = falcon_mcp_server.MCP(tool_filter=tool_filter)
```

A tool result that is not a string is rendered as JSON for the model. Pass a
serializer to render it otherwise, e.g., as [TOON](https://toonformat.dev):
```python
# With toon-format, the official implementation:
mcp = falcon_mcp_server.MCP(result_serializer=toon_format.dumps)

# Or with toons, which is implemented in Rust:
mcp = falcon_mcp_server.MCP(result_serializer=toons.dumps)
```

As with tools, a parameter of the serializer annotated with `Context` receives
the context of the request. This way, the format can also be chosen via the
URL of the end point, such as `/mcp?format=toon`:
```python
def serialize(result, context: Context) -> str:
    if context.req.get_param('format') == 'toon':
        return toon_format.dumps(result)
    return json.dumps(result)


mcp = falcon_mcp_server.MCP(result_serializer=serialize)
```

A tool argument can be mirrored into an HTTP header, e.g., for a reverse proxy
to log or route on without parsing the request body. Annotate the argument
with `x-mcp-header` in the input schema, as the example does for `city`, and
clients send its value in the `Mcp-Param-City` header; a call where the header
and the argument disagree is rejected:
```python
mcp.add_tool(
    get_temperature,
    input_schema={
        'type': 'object',
        'properties': {
            'city': {'type': 'string', 'x-mcp-header': 'City'},
        },
        'required': ['city'],
    },
)
```

An exception raised by a tool is logged, and reported to the client as an
internal error without any details. The exceptions are Falcon's HTTP errors,
such as `falcon.HTTPUnauthorized`, which are rendered as usual. To tell the
model what went wrong, so that it can correct itself, raise `ToolError`:
```python
from falcon_mcp_server import ToolError


async def get_temperature(city: str) -> str:
    if city not in temperatures:
        raise ToolError(f'Unknown city; try one of: {", ".join(temperatures)}')
    ...
```

Besides the fixed contents of `add_simple_resource`, a resource can be read by
a callable, which is awaited on every read. It returns text (`str`) or binary
data (`bytes`), and receives the context in the same way as a tool does:
```python
async def get_usage(context: Context) -> str:
    """Storage and bandwidth used by the account this month."""
    account = context.path_params['account']
    ...


mcp.add_resource('usage://current', 'application/json', get_usage)
```

A tool can be paired with an
[MCP App](https://github.com/modelcontextprotocol/ext-apps), i.e., an HTML
widget that the supporting hosts render in place of the plain tool result.
Serve the widget as a `ui://` resource, and point the tool to it via `meta`.
A `dict` that the tool returns is also sent as structured content, which is
what the widget gets to render:
```python
WIDGET_URI = 'ui://weather-example/temperature.html'

mcp.add_tool(show_temperature, meta={'ui': {'resourceUri': WIDGET_URI}})
mcp.add_resource(WIDGET_URI, 'text/html;profile=mcp-app', read_widget)
```

See ``examples/widget.py`` and ``examples/widget.html`` for the complete
example. It runs in the same way as the first one:
```console
uvicorn --log-config logging.yaml --factory widget:mcp.create_app
```

A host that runs in a web browser needs its origin to be allowed, see below.

Requests from web browsers (i.e., the ones carrying an `Origin` header) are
rejected with HTTP 403 unless their origin is explicitly allowed. Optionally,
the same origins can also be granted access via CORS:
```python
mcp = falcon_mcp_server.MCP(allowed_origins=['http://localhost:6274'])
app = mcp.create_app(cors_enable=True)
```

## License

`falcon-mcp-server` is distributed under the terms of the [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) license.
