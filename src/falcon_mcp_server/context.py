"""Context of an MCP request."""

import dataclasses
from typing import Any

from falcon.asgi import Request


@dataclasses.dataclass(frozen=True)
class Context:
    """Context of the MCP request being handled.

    A tool or prompt callable receives the context via the parameter that it
    annotates with this class, if any.

    Attributes:
        req: HTTP request carrying the MCP request (a placeholder on stdio).
        path_params: Field values of the URI template matching the request.
        meta: The ``_meta`` object of the MCP request.
    """

    req: Request
    path_params: dict[str, Any]
    meta: dict[str, Any]
