"""Persistent MCP session storage."""

import uuid

from .session import Session


class SessionStorage:
    """
    MCP session and event stream storage.

    At the time of this writing, this is a simple in-memory MVP skeleton.

    .. todo::
        Support pluggable storage that can be used from different worker
        processes.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def create(self) -> Session:
        session = Session(str(uuid.uuid4()))
        self._sessions[session.session_id] = session
        return session

    async def load(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def delete(self, session: Session) -> None:
        self._sessions.pop(session.session_id, None)
