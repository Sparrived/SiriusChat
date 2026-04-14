"""Unified roleplay-asset ↔ workspace-config bootstrap manager."""

from __future__ import annotations

from pathlib import Path

from sirius_chat.config.manager import ConfigManager
from sirius_chat.config.models import SessionDefaults, WorkspaceConfig
from sirius_chat.workspace.layout import WorkspaceLayout


class RoleplayWorkspaceManager:
    """Unify agent selection, session defaults and workspace persistence.

    The host calls *bootstrap_active_agent* after a wizard flow completes;
    this class takes care of selecting the agent in the library, updating
    the workspace defaults, and persisting everything — so the host never
    touches workspace files directly.
    """

    def __init__(self, layout: WorkspaceLayout) -> None:
        self._layout = layout
        self._config_manager = ConfigManager(base_path=layout.config_root)

    def bootstrap_active_agent(
        self,
        *,
        agent_key: str,
        session_defaults: SessionDefaults | None = None,
        orchestration_defaults: dict[str, object] | None = None,
    ) -> WorkspaceConfig:
        """Select an agent and optionally update workspace defaults in one call.

        1. Validates the agent key exists in the generated agent library.
        2. Marks the agent as *selected* in the library file.
        3. Merges optional session/orchestration defaults into workspace config.
        4. Persists workspace config files.
        """
        from sirius_chat.roleplay_prompting import select_generated_agent_profile

        select_generated_agent_profile(self._layout.config_root, agent_key)

        workspace_config = self._config_manager.load_workspace_config(
            self._layout.config_root,
            data_path=self._layout.data_root,
        )
        workspace_config.active_agent_key = agent_key.strip()
        if session_defaults is not None:
            workspace_config.session_defaults = session_defaults
        if orchestration_defaults is not None:
            workspace_config.orchestration_defaults = dict(orchestration_defaults)

        self._config_manager.save_workspace_config(
            self._layout.config_root,
            workspace_config,
            data_path=self._layout.data_root,
        )
        return workspace_config

    def bootstrap_from_legacy_session_config(
        self,
        *,
        source: Path,
        agent_key: str | None = None,
    ) -> WorkspaceConfig:
        """Bootstrap workspace config from a legacy ``session.json`` file.

        Reads the legacy file, extracts defaults and provider list, and
        writes workspace config files.  If *agent_key* is not given the
        ``generated_agent_key`` from the file is used.
        """
        workspace_config, _ = self._config_manager.bootstrap_workspace_from_legacy_session_json(
            source,
            work_path=self._layout.config_root,
            data_path=self._layout.data_root,
        )
        if agent_key:
            workspace_config.active_agent_key = agent_key.strip()
            self._config_manager.save_workspace_config(
                self._layout.config_root,
                workspace_config,
                data_path=self._layout.data_root,
            )
        return workspace_config
