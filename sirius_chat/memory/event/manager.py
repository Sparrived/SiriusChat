from __future__ import annotations


class EventMemoryEntry:
    def __init__(self, *args, **kwargs) -> None:
        pass


class EventMemoryManager:
    def __init__(self, *args, **kwargs) -> None:
        self.entries = []

    def to_dict(self):
        return {}

    @classmethod
    def from_dict(cls, data):
        return cls()

    def buffer_message(self, *args, **kwargs):
        pass
