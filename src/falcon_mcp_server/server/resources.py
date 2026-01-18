"""MCP server resources."""


class Resources:
    def __init__(self) -> None:
        self._resources = {}
        self._resource_templates = {}

    def add_simple_resource(
        self,
        uri: str,
        media_type: str,
        data: str | bytes,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        pass

    async def list_resources(self, cursor: str = None) -> list:
        return list(self._resources.items())

    async def read_resource(
        self,
        uri: str,
    ) -> dict[str, str]:
        pass
