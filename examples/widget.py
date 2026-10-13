"""MCP Apps example: a tool whose result is rendered by a ui:// widget."""

import pathlib

import falcon_mcp_server
from falcon_mcp_server import ToolError

WIDGET = pathlib.Path(__file__).resolve().with_name('widget.html')
WIDGET_URI = 'ui://weather-example/temperature.html'

TEMPERATURES = {
    'New York': 22,
    'London': 15,
}


async def show_temperature(city: str) -> dict[str, str | int]:
    """Show the current temperature for a city.

    Args:
        city (str): The name of the city.

    Returns:
        The city and its current temperature.
    """
    if city not in TEMPERATURES:
        known = ', '.join(TEMPERATURES)
        raise ToolError(f'The temperature in {city} is unknown; try: {known}.')

    # NOTE: A dict is sent both as text for the model, and as structured
    #   content, which is what the widget renders.
    return {'city': city, 'temperature': TEMPERATURES[city], 'unit': '°C'}


async def read_widget() -> str:
    """Widget rendering the temperature that show_temperature returns."""
    # NOTE: The file is read anew every time, so that the widget can be
    #   edited without restarting the server.
    return WIDGET.read_text()


mcp = falcon_mcp_server.MCP(
    name='widget-example',
    title='Widget Example',
    description='Example MCP server showing made-up weather in a widget.',
    instructions='Temperatures are only known for New York and London.',
)

mcp.add_tool(
    show_temperature,
    title='City temperature widget',
    description='Show the current temperature for a city.',
    input_schema={
        'type': 'object',
        'properties': {
            'city': {'type': 'string', 'description': 'name of the city'},
        },
        'required': ['city'],
    },
    annotations={'readOnlyHint': True},
    # NOTE: A host supporting MCP Apps renders the result of the tool in the
    #   widget below; any other host just passes the result to the model.
    meta={'ui': {'resourceUri': WIDGET_URI}},
)

mcp.add_resource(
    WIDGET_URI,
    'text/html;profile=mcp-app',
    read_widget,
    title='Temperature widget',
)
