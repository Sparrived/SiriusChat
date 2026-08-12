# 内置 Tools

本目录保存随框架发布的 Tool。它们会被 Tool 注册器扫描，并可在 WebUI 中按人格启停。

当前文件包括：`bash.py`、`group_file_exec.py`、`group_management.py`、`interaction_with_master.py`、`desktop_screenshot.py`、`github_monitor.py`、`web_lookup.py`、`qq_member_info.py`。定时任务由 `bash.py` 的项目级 `crontab` 兼容入口统一管理。

新增、删除或重命名 Tool 后同步 `docs/extensions/tool-builtin.md`。高风险能力必须设置权限限制。
