from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - fallback for environments without watchdog
    FileSystemEvent = object  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]


def _normalize_path(path: Path | str) -> str:
    return str(Path(path).resolve(strict=False)).lower()


class _WorkspaceConfigEventHandler(FileSystemEventHandler):
    def __init__(self, *, watched_paths: set[str], on_change: Callable[[Path], None]) -> None:
        super().__init__()
        self._watched_paths = watched_paths
        self._on_change = on_change

    def on_any_event(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        for path in self._iter_event_paths(event):
            if _normalize_path(path) in self._watched_paths:
                self._on_change(path)
                return

    def _iter_event_paths(self, event: FileSystemEvent) -> list[Path]:
        paths: list[Path] = []
        src_path = getattr(event, "src_path", "")
        if src_path:
            paths.append(Path(src_path))
        dest_path = getattr(event, "dest_path", "")
        if dest_path:
            paths.append(Path(dest_path))
        return paths


class WorkspaceConfigWatcher:
    """Watch config files and notify the runtime when they change."""

    def __init__(self, *, watched_paths: list[Path], on_change: Callable[[Path], None]) -> None:
        self._watched_paths = {_normalize_path(path) for path in watched_paths}
        self._watch_roots = sorted(
            {Path(path).resolve(strict=False).parent for path in watched_paths},
            key=lambda item: str(item),
        )
        self._on_change = on_change
        self._observer = None

    @property
    def is_available(self) -> bool:
        return Observer is not None

    def start(self) -> bool:
        if Observer is None:
            return False
        if self._observer is not None:
            return True

        observer = Observer()
        handler = _WorkspaceConfigEventHandler(
            watched_paths=self._watched_paths,
            on_change=self._on_change,
        )
        for root in self._watch_roots:
            root.mkdir(parents=True, exist_ok=True)
            observer.schedule(handler, str(root), recursive=False)
        observer.start()
        self._observer = observer
        return True

    def stop(self) -> None:
        observer = self._observer
        if observer is None:
            return
        self._observer = None
        observer.stop()
        observer.join(timeout=1.0)