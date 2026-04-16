# v0.27.11 迁移说明

本版本主要引入 developer 档案驱动的 SKILL 安全模型，并新增一个 developer-only 的桌面截图内置 SKILL。

## 变更摘要

- `UserProfile` 与 `Participant` 现在支持通过 `metadata["is_developer"] = True` 显式声明 developer。
- engine 每轮会根据当前发言者和会话内已登记用户构建 `SkillInvocationContext`，用于控制 developer-only SKILL 的可见性与执行权限。
- 新增内置 `desktop_screenshot` SKILL，仅 developer 可调用；返回结果会以 `multimodal_blocks` 图片块的形式注入模型内部上下文。
- 内置 `system_info` 现在显式声明 `dependencies = ["psutil"]`，新增的 `desktop_screenshot` 显式声明 `dependencies = ["Pillow"]`；内置与 workspace SKILL 会共同参与统一的依赖自动安装流程。
- CLI 首次引导会显式询问 primary user 是否为 developer，并将结果持久化到 `primary_user.json`。

## 对外影响

### 1. 受限 SKILL 需要显式 developer

如果你的外部接入需要使用 developer-only 内置 SKILL，至少要为一名可信用户显式设置 developer 元数据。推荐写法：

```python
from sirius_chat.api import UserProfile

profile = UserProfile(
    user_id="admin_1",
    name="平台管理员",
    metadata={"is_developer": True},
)
```

说明：

- 非 developer 当前轮次不会在提示词中看到 developer-only 技能。
- 即使模型强行伪造 `[SKILL_CALL: desktop_screenshot]`，runtime 也会返回权限错误，而不是静默放行。
- 当前实现不会因为“整个会话没有 developer”而阻止普通对话继续进行，但 developer-only SKILL 在这种情况下必然无法执行。

### 2. `desktop_screenshot` 是新的受限内置 SKILL

- 技能名：`desktop_screenshot`
- 权限：`developer_only = True`
- 依赖：`Pillow`
- 返回：包含图片文件路径的 `multimodal_blocks`

推荐注意点：

- 该技能会把截图保存到当前 skill 的 artifact 目录，再把本地路径交给 provider 侧转换为模型可消费的图片输入。
- 如果你在 workspace 中提供同名 `skills/desktop_screenshot.py`，workspace 版本会覆盖内置实现。

### 3. 自定义 SKILL 可以接收 `invocation_context`

现在 `run()` 可以按需显式声明：

```python
from typing import Any

from sirius_chat import SkillInvocationContext


def run(
    data_store: Any = None,
    invocation_context: SkillInvocationContext | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "caller_user_id": invocation_context.caller_user_id if invocation_context else "",
    }
```

补充说明：

- `data_store` 与 `invocation_context` 都改为按函数签名自动注入。
- 如果你的 `run()` 不接收这些参数，框架不会强行传入。
- 若 SKILL 会生成本地文件，优先使用 `data_store.artifact_dir` 保存工件。

## 升级检查清单

升级到 v0.27.11 后，建议至少验证以下场景：

1. 外部接入传入 `UserProfile.metadata={"is_developer": True}` 后，developer 当前轮次能看到并调用 `desktop_screenshot`。
2. 普通用户当前轮次看不到 developer-only 技能；即使模型强行调用，runtime 也会返回明确的权限错误。
3. 在未预装 `psutil` / `Pillow` 的环境里，确认内置 SKILL 加载前会参与统一的依赖解析路径。
4. 若你使用 CLI 首次引导，确认生成的 `primary_user.json` 已包含 `metadata.is_developer`。