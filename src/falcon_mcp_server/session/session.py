"""MCP client-server session."""


class Session:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def __repr__(self) -> str:
        return f'Session<{self._session_id}>'

    @property
    def session_id(self) -> str:
        return self._session_id
