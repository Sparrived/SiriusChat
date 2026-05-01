import warnings

__all__ = [
    "RoleplayWorkspaceManager",
    "WorkspaceLayout",
    "WorkspaceRuntime",
]

def __getattr__(name: str):
    if name in {"WorkspaceRuntime", "RoleplayWorkspaceManager"}:
        warnings.warn(
            f"{name} is deprecated and will be removed in v1.1. "
            "Use PersonaManager / EngineRuntime instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    if name == "WorkspaceLayout":
        from sirius_chat.workspace.layout import WorkspaceLayout
        return WorkspaceLayout
    if name == "WorkspaceRuntime":
        from sirius_chat.workspace.runtime import WorkspaceRuntime
        return WorkspaceRuntime
    if name == "RoleplayWorkspaceManager":
        from sirius_chat.workspace.roleplay_manager import RoleplayWorkspaceManager
        return RoleplayWorkspaceManager
    raise AttributeError(name)
