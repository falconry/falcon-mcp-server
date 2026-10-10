from typing import Any

from falcon_mcp_server.constants import SUPPORTED_MCP_PROTOCOL_VERSIONS


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
