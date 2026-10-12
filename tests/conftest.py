import falcon.asgi
import pytest

import falcon_mcp_server


async def greet(name: str = 'World') -> str:
    """Greet someone by name."""
    return f'Hello, {name}!'


async def summarize(topic: str, style: str = 'brief') -> str:
    """Ask for a summary of a topic."""
    return f'Write a {style} summary of {topic}.'


@pytest.fixture
def server() -> falcon_mcp_server.MCP:
    mcp = falcon_mcp_server.MCP()
    mcp.add_tool(
        greet,
        input_schema={
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
        },
    )
    mcp.add_prompt(summarize)
    mcp.add_simple_resource('file:///hello.txt', 'text/plain', 'Hello!')
    return mcp


@pytest.fixture
def app(server) -> falcon.asgi.App:
    return server.create_app()
