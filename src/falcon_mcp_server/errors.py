class RPCError(Exception):
    pass


class RPCInvalidParam(RPCError):
    pass


class RPCInternalError(RPCError):
    pass
