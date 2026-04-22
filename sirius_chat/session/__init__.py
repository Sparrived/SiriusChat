"""Session management module - handles session persistence and runner operations."""

__all__ = [
    "JsonSessionStore",
    "SqliteSessionStore",
    "SessionStore",
    "SessionStoreFactory",
]


def __getattr__(name: str):
    if name in {"JsonSessionStore", "SessionStore", "SessionStoreFactory", "SqliteSessionStore"}:
        from sirius_chat.session.store import (
            JsonSessionStore,
            SessionStore,
            SessionStoreFactory,
            SqliteSessionStore,
        )

        return {
            "JsonSessionStore": JsonSessionStore,
            "SessionStore": SessionStore,
            "SessionStoreFactory": SessionStoreFactory,
            "SqliteSessionStore": SqliteSessionStore,
        }[name]
    raise AttributeError(name)
