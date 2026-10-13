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


async def compare_temperatures(city: str, other_city: str = 'London') -> str:
    """Ask which of two cities is currently warmer.

    Args:
        city (str): The name of the first city.
        other_city (str): The name of the second city (default 'London').

    Returns:
        The text of the prompt.
    """
    return (
        f'Look up the current temperatures in {city} and {other_city}, and '
        f'tell me which of the two cities is warmer.'
    )


mcp = falcon_mcp_server.MCP(
    name='weather-example',
    title='Weather Example',
    description='Example MCP server providing made-up weather information.',
    instructions='Temperatures are only known for New York and London.',
)

mcp.add_tool(
    get_temperature,
    title='City weather information provider',
    description='Get the current temperature for a city.',
    input_schema={
        'type': 'object',
        'properties': {
            'city': {
                'type': 'string',
                'description': 'name of the city',
                # NOTE: Clients mirror the city into the Mcp-Param-City HTTP
                #   header, e.g., for a load balancer to route requests on.
                'x-mcp-header': 'City',
            }
        },
        'required': ['city'],
    },
)

mcp.add_prompt(
    compare_temperatures,
    title='Compare the temperature in two cities',
    description='Ask which of two cities is currently warmer.',
    arguments=[
        {
            'name': 'city',
            'description': 'name of the first city',
            'required': True,
        },
        {'name': 'other_city', 'description': 'name of the second city'},
    ],
)

mcp.add_simple_resource(
    'file:///project/README.md',
    'text/markdown',
    README.read_text(),
    title='Project Documentation',
)
