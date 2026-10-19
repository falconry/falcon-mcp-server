from typing import Any

from falcon_mcp_server.constants import SUPPORTED_MCP_PROTOCOL_VERSIONS


def name_supported_versions(message: str) -> str:
    """Append the supported protocol versions to an error message."""
    # NOTE(vytas): MCP Versioning, 4. Backward Compatibility with
    #   Initialization-Based Versions.
    #   A server that supports only modern versions SHOULD name the protocol
    #   versions it supports in any error it returns to an initialize
    #   request, on any transport.
    supported = ', '.join(sorted(SUPPORTED_MCP_PROTOCOL_VERSIONS))
    return f'{message} (supported protocol versions: {supported})'


class ToolError(Exception):
    """Tool execution error that the model can act upon.

    Raise it from a tool to report, e.g., invalid input or a failed upstream
    request. Unlike the details of any other exception, the message is sent
    to the client, which normally passes it on to the model.

    Args:
        message: Description of the error, and ideally of how to recover.
    """


class RPCError(Exception):
    code = -32603

    def __init__(self, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data

    def marshal(self) -> dict[str, Any]:
        error: dict[str, Any] = {'code': self.code, 'message': self.message}
        if self.data is not None:
            error['data'] = self.data
        return error


class RPCParseError(RPCError):
    code = -32700


class RPCInvalidRequest(RPCError):
    code = -32600


class RPCMethodNotFound(RPCError):
    code = -32601


class RPCInvalidParam(RPCError):
    code = -32602


class RPCInternalError(RPCError):
    code = -32603


class RPCHeaderMismatch(RPCError):
    code = -32020


class RPCUnsupportedProtocolVersion(RPCError):
    code = -32022

    def __init__(self, requested: str) -> None:
        supported = sorted(SUPPORTED_MCP_PROTOCOL_VERSIONS)
        super().__init__(
            'Unsupported protocol version',
            data={'supported': supported, 'requested': requested},
        )
