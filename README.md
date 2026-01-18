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

See the example in ``example/example.py``.

Change dir to ``example/``, and run with Uvicorn:
```console
uvicorn --log-config logging.yaml --factory example:mcp.create_app
```

Connect with any AI agent or MCP inspector supporting the latest
[Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
transport version.

For instance, assuming you have both
[MCPHost](https://github.com/mark3labs/mcphost) and
[Ollama](https://ollama.com/) installed on your system, you can run it against
the previously started server (see above) as:
```console
mcphost --config mcphost.yaml -m ollama:granite4:3b --prompt "What is the current temperature in London?"
```

Omit `--prompt` to run interactively.
Feel free to `pull` and use a different `ollama` model!

## License

`falcon-mcp-server` is distributed under the terms of the [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) license.
