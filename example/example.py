import pathlib

import falcon_mcp_server

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / 'README.md'


async def get_temperature(city: str) -> str:
    """Get the current temperature for a city.

    Args:
        city (str): The name of the city.

    Returns:
        The current temperature for the city.
    """
    temperatures = {
        'New York': '22°C',
        'London': '15°C',
    }
    temperature = temperatures.get(city, 'unknown')
    return f'The current temperature in {city} is {temperature}.'


mcp = falcon_mcp_server.MCP()

mcp.add_tool(
    get_temperature,
    title='City weather information provider',
    input_schema={
        'type': 'object',
        'properties': {
            'city': {'type': 'string', 'description': 'name of the city'}
        },
        'required': ['city'],
    },
)

mcp.add_simple_resource(
    'file:///project/README.md',
    'text/markdown',
    README.read_text(),
    title='Project Documentation',
)
