__all__ = [
    "WorkspaceLayout",
    "LegacyLayoutReport",
    "MigrationReport",
    "WorkspaceMigrationManager",
    "WorkspaceRuntime",
]


def __getattr__(name: str):
    if name == "WorkspaceLayout":
        from sirius_chat.workspace.layout import WorkspaceLayout

        return WorkspaceLayout
    if name in {"LegacyLayoutReport", "MigrationReport", "WorkspaceMigrationManager"}:
        from sirius_chat.workspace.migration import (
            LegacyLayoutReport,
            MigrationReport,
            WorkspaceMigrationManager,
        )

        return {
            "LegacyLayoutReport": LegacyLayoutReport,
            "MigrationReport": MigrationReport,
            "WorkspaceMigrationManager": WorkspaceMigrationManager,
        }[name]
    if name == "WorkspaceRuntime":
        from sirius_chat.workspace.runtime import WorkspaceRuntime

        return WorkspaceRuntime
    raise AttributeError(name)