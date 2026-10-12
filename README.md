# falcon-mcp-server

[![PyPI - Version](https://img.shields.io/pypi/v/falcon-mcp-server.svg)](https://pypi.org/project/falcon-mcp-server)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/falcon-mcp-server.svg)](https://pypi.org/project/falcon-mcp-server)

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

See the example in ``example/example.py``, which exposes a tool, a prompt
and a resource.

Change dir to ``example/``, and run with Uvicorn:
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

Requests from web browsers (i.e., the ones carrying an `Origin` header) are
rejected with HTTP 403 unless their origin is explicitly allowed. Optionally,
the same origins can also be granted access via CORS:
```python
mcp = falcon_mcp_server.MCP(allowed_origins=['http://localhost:6274'])
app = mcp.create_app(cors_enable=True)
```

## License

`falcon-mcp-server` is distributed under the terms of the [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) license.
