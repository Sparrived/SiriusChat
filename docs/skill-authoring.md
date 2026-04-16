# SKILL 编写指南

本文档面向 AI 或开发者，提供编写 Sirius Chat SKILL 所需的全部规范。
读完本文档后，你应该能直接生成一个可加载、可执行的 SKILL 文件。

## 最小可用 SKILL

```python
"""一句话描述这个 SKILL 的用途。"""
from __future__ import annotations
from typing import Any

from sirius_chat import SkillInvocationContext

SKILL_META = {
    "name": "hello",
    "description": "向指定用户打招呼",
    "version": "1.0.0",
    "developer_only": False,
    "parameters": {
        "username": {
            "type": "str",
            "description": "用户名",
            "required": True,
        },
    },
}

def run(
    username: str,
    data_store: Any = None,
    invocation_context: SkillInvocationContext | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return {"greeting": f"你好，{username}！"}
```

## 文件约定

| 约定 | 说明 |
|------|------|
| 存放位置 | `skills/` 目录下的 `.py` 文件；默认位于 `{work_path}`，双根布局时位于 `config_root`；目录与 `README.md` 会由框架自动创建 |
| 必须导出 | `SKILL_META` 字典 + `run()` 函数 |
| 命名规则 | 文件名建议与 `SKILL_META["name"]` 一致（如 `hello.py`） |
| 编码 | UTF-8 |
| 跳过规则 | 以 `_` 或 `.` 开头的文件会被自动跳过 |

框架也会预加载包内置 SKILL（当前包含 `system_info` 与 developer-only 的 `desktop_screenshot`）。如果你在 workspace 的 `skills/` 中放置同名文件，例如 `skills/system_info.py`，则 workspace 文件会覆盖内置实现。

## SKILL_META 字段

```python
SKILL_META = {
    # [必填] SKILL 唯一标识，仅限字母、数字和下划线
    "name": "my_skill",

    # [必填] 自然语言描述，AI 根据此描述决定何时调用该 SKILL
    "description": "用一两句话描述功能和使用场景",

    # [可选] 版本号，默认 "1.0.0"
    "version": "1.0.0",

    # [可选] 第三方依赖列表，框架在加载时自动安装缺失的包
    "dependencies": ["requests", "beautifulsoup4"],

    # [可选] 是否仅允许 developer 调用，默认 False
    "developer_only": False,

    # [可选] 参数定义，dict 或 list 格式均可
    "parameters": { ... },
}
```

### parameters 的两种写法

**字典格式**（推荐，更紧凑）：

```python
"parameters": {
    "query": {
        "type": "str",
        "description": "搜索关键词",
        "required": True,
    },
    "limit": {
        "type": "int",
        "description": "最大返回条数",
        "required": False,
        "default": 10,
    },
}
```

**列表格式**：

```python
"parameters": [
    {"name": "query", "type": "str", "description": "搜索关键词", "required": True},
    {"name": "limit", "type": "int", "description": "最大返回条数", "required": False, "default": 10},
]
```

### 支持的参数类型

| type | Python 类型 | 自动转换规则 |
|------|-------------|-------------|
| `str` | `str` | 原样传递 |
| `int` | `int` | `int(value)`，失败则原样传递 |
| `float` | `float` | `float(value)`，失败则原样传递 |
| `bool` | `bool` | `"true"/"1"/"yes"` → `True`，其余 → `False` |
| `list[str]` / `list` | `list` | JSON 数组或逗号分割字符串 |
| `dict` | `dict` | 原样传递 |

## run() 函数规范

```python
def run(
    param1: str,           # 与 parameters 中定义的参数名一一对应
    param2: int = 10,      # 可选参数需有默认值
    data_store: Any = None, # 自动注入的持久化存储
    invocation_context: SkillInvocationContext | None = None, # 可选：自动注入的调用上下文
    **kwargs: Any,          # 吸收未知参数，保持前向兼容
) -> Any:
    """执行函数。返回值会被封装为 SkillResult.data。"""
    ...
```

**关键规则**：
- 参数名必须与 `SKILL_META["parameters"]` 中的 `name` 完全匹配
- `data_store` 与 `invocation_context` 都由框架按函数签名自动注入，无需在 `parameters` 中声明
- 若你的 `run()` 不接收 `data_store` / `invocation_context`，框架不会强行塞入这些关键字参数
- `**kwargs` 建议始终保留
- 返回值推荐使用 `dict`，便于 AI 理解结构化结果
- 若需要向模型内部传递更细的文本或图片结果，可在返回字典中使用 `text_blocks`、`multimodal_blocks` 与 `internal_metadata`
- 若返回 `None`，AI 会收到"执行完成（无返回数据）"
- 抛出异常会被捕获，AI 会收到 `[SKILL执行失败] {异常信息}`
- **执行超时**：框架有最大执行时间限制（默认 30 秒），超时后 SKILL 会被终止，AI 和用户都会收到超时提示。避免在 `run()` 中执行长时间阻塞操作

## developer_only 与调用上下文

如果某个 SKILL 涉及桌面截图、主机级操作或其他受限能力，推荐在 `SKILL_META` 中显式声明 `developer_only=True`：

```python
from sirius_chat import SkillInvocationContext

SKILL_META = {
    "name": "dangerous_tool",
    "description": "执行受限主机操作",
    "developer_only": True,
}

def run(
    invocation_context: SkillInvocationContext | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    caller = invocation_context.caller_name if invocation_context else ""
    return {"caller": caller}
```

语义说明：

- developer-only SKILL 只会在 developer 当前轮次的提示词中暴露给模型。
- 即使模型强行伪造调用，runtime 仍会再次校验当前调用者是否为 developer。
- `SkillInvocationContext` 可用于审计调用者、记录 `caller_user_id`，或在 SKILL 内部做更细粒度的授权逻辑。

## 结构化结果通道（v0.27.9）

当普通 `dict` 不足以表达技能结果时，可返回以下结构化字段：

```python
def run(**kwargs: Any) -> dict[str, Any]:
    return {
        "summary": "可选的普通字段，仍会出现在展示文本里",
        "text_blocks": [
            {"type": "text", "value": "检测到蓝天和少量白云。", "label": "summary"},
        ],
        "multimodal_blocks": [
            {
                "type": "image",
                "value": "https://example.com/sky.png",
                "mime_type": "image/png",
                "label": "source",
            }
        ],
        "internal_metadata": {
            "trace_id": "debug-only",
        },
    }
```

语义说明：

- `text_blocks`：供模型内部推理使用的附加文本块；会优先作为技能展示文本的一部分。
- `multimodal_blocks`：当前主要用于图片输入；框架会把可识别图片隐藏注入下一轮模型请求。`value` 可以是公网 URL、本地文件路径或 `file://` URI。
- `internal_metadata`：仅供内部链路使用，不应面向用户输出；框架会在系统提示词中明确要求模型不要复述这些元信息。
- 若同时提供普通字段和 `text_blocks`，最终展示文本优先采用 `text_blocks`，其余普通字段仍可作为兜底摘要。

若 SKILL 会生成本地图片或其他工件，优先把文件写到 `data_store.artifact_dir`，再把路径写入 `multimodal_blocks`，避免把大体积二进制或 base64 文本直接塞进返回结果。

## data_store 持久化存储

每个 SKILL 拥有独立的 JSON 键值存储（路径：`{work_path}/skill_data/{skill_name}.json`）。

```python
def run(data_store: Any = None, **kwargs: Any) -> dict:
    # 读取
    count = data_store.get("call_count", 0)

    # 写入
    data_store.set("call_count", count + 1)

    # 其他操作
    data_store.delete("old_key")       # 删除键
    all_keys = data_store.keys()       # 所有键名
    all_data = data_store.all()        # 完整字典副本

    return {"call_count": count + 1}
```

> 数据在 SKILL 执行后自动持久化，无需手动调用 `save()`。

附加属性：

- `data_store.store_path`：当前 JSON 存储文件路径。
- `data_store.artifact_dir`：当前 SKILL 推荐使用的工件目录，适合保存截图、临时导出文件等。

## AI 如何调用 SKILL

AI 在回复中使用标记符调用 SKILL，框架自动检测并执行：

```
[SKILL_CALL: skill_name | {"param1": "value1", "param2": 42}]
```

无参数调用：

```
[SKILL_CALL: skill_name]
```

调用结果会先被框架规范化为内部文本/多模态通道，再注入上下文；AI 随后根据这些内部结果生成最终回复。

## 完整示例：天气查询 SKILL

```python
"""查询指定城市的天气信息（示例，使用模拟数据）。"""
from __future__ import annotations
from datetime import datetime
from typing import Any

SKILL_META = {
    "name": "weather",
    "description": "查询指定城市的当前天气信息，包括温度、湿度和天气状况",
    "version": "1.0.0",
    "parameters": {
        "city": {
            "type": "str",
            "description": "城市名称，如 北京、上海",
            "required": True,
        },
    },
}

def run(city: str, data_store: Any = None, **kwargs: Any) -> dict[str, Any]:
    # 实际项目中可替换为真实 API 调用
    weather_data = {
        "city": city,
        "temperature": "22°C",
        "humidity": "65%",
        "condition": "多云",
        "wind": "东南风 3级",
        "updated_at": datetime.now().strftime("%H:%M"),
    }

    # 记录查询历史
    if data_store is not None:
        history = data_store.get("history", [])
        history.append({"city": city, "time": datetime.now().isoformat()})
        data_store.set("history", history[-50:])

    return weather_data
```

## 依赖自动安装

框架在加载 SKILL 时会自动检测并安装缺失的第三方依赖。

### 工作机制

1. 扫描 `SKILL_META["dependencies"]` 中显式声明的包名（优先）。
2. 扫描模块中的 `import` / `from ... import` 语句（补充推断，不限于顶层）。
3. 过滤标准库和已安装的包。
4. 使用 `uv pip install` 安装缺失包（若 `uv` 不可用则回退到 `pip`）。

### 推荐做法

**始终显式声明 `dependencies`**，尤其是 import 名与包名不一致的库：

```python
SKILL_META = {
    "name": "web_scraper",
    "description": "网页内容抓取",
    "dependencies": ["requests", "beautifulsoup4"],  # beautifulsoup4 的 import 名是 bs4
    "parameters": { ... },
}
```

### 关闭自动安装

在受限环境中可通过配置禁用：

```python
OrchestrationPolicy(
    enable_skills=True,
    auto_install_skill_deps=False,
)
```

## 启用 SKILL 系统

在 `SessionConfig` 中配置。`enable_skills` 默认就是开启状态，只有需要关闭时才显式设为 `False`：

```python
from sirius_chat import SessionConfig, OrchestrationPolicy

config = SessionConfig(
    # ...其他配置...
    orchestration=OrchestrationPolicy(
        enable_skills=True,              # 启用 SKILL 系统
        max_skill_rounds=3,              # 每轮最多连续调用次数
        skill_execution_timeout=30,      # SKILL 最大执行秒数
        auto_install_skill_deps=True,    # 自动安装 SKILL 依赖（默认开启）
    ),
)
```

框架会先注册包内置 SKILL，再创建 workspace `skills/` 与 `README.md`，随后自动加载该目录中的 SKILL 文件；即使关闭 SKILL 执行，目录引导结构仍会保留。同名 workspace 文件会覆盖内置实现。内置与 workspace SKILL 共用同一条依赖自动安装路径。

## 检查清单

编写完成后，对照以下清单确认：

- [ ] 文件放在 `{work_path}/skills/` 目录下
- [ ] `SKILL_META` 包含 `name` 和 `description`
- [ ] `name` 仅包含字母、数字、下划线
- [ ] `description` 足够清晰，AI 能根据它判断何时调用
- [ ] `run()` 函数存在且参数名与 `parameters` 定义匹配
- [ ] `run()` 至少保留 `**kwargs`；若需要持久化或审计，显式接收 `data_store` / `invocation_context`
- [ ] 若是受限能力，已显式设置 `developer_only=True`
- [ ] 返回值为 `dict` 或可序列化对象
- [ ] 不依赖未安装的第三方库（或在 `dependencies` 中显式声明）
- [ ] 不包含长时间阻塞操作（注意 30 秒超时限制）
