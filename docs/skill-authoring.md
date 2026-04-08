# SKILL 编写指南

本文档面向 AI 或开发者，提供编写 Sirius Chat SKILL 所需的全部规范。
读完本文档后，你应该能直接生成一个可加载、可执行的 SKILL 文件。

## 最小可用 SKILL

```python
"""一句话描述这个 SKILL 的用途。"""
from __future__ import annotations
from typing import Any

SKILL_META = {
    "name": "hello",
    "description": "向指定用户打招呼",
    "version": "1.0.0",
    "parameters": {
        "username": {
            "type": "str",
            "description": "用户名",
            "required": True,
        },
    },
}

def run(username: str, data_store: Any = None, **kwargs: Any) -> dict[str, Any]:
    return {"greeting": f"你好，{username}！"}
```

## 文件约定

| 约定 | 说明 |
|------|------|
| 存放位置 | `{work_path}/skills/` 目录下的 `.py` 文件 |
| 必须导出 | `SKILL_META` 字典 + `run()` 函数 |
| 命名规则 | 文件名建议与 `SKILL_META["name"]` 一致（如 `hello.py`） |
| 编码 | UTF-8 |
| 跳过规则 | 以 `_` 或 `.` 开头的文件会被自动跳过 |

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
    **kwargs: Any,          # 吸收未知参数，保持前向兼容
) -> Any:
    """执行函数。返回值会被封装为 SkillResult.data。"""
    ...
```

**关键规则**：
- 参数名必须与 `SKILL_META["parameters"]` 中的 `name` 完全匹配
- `data_store` 由框架自动注入，无需在 `parameters` 中声明
- `**kwargs` 建议始终保留
- 返回值推荐使用 `dict`，便于 AI 理解结构化结果
- 若返回 `None`，AI 会收到"执行完成（无返回数据）"
- 抛出异常会被捕获，AI 会收到 `[SKILL执行失败] {异常信息}`
- **执行超时**：框架有最大执行时间限制（默认 30 秒），超时后 SKILL 会被终止，AI 和用户都会收到超时提示。避免在 `run()` 中执行长时间阻塞操作

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

## AI 如何调用 SKILL

AI 在回复中使用标记符调用 SKILL，框架自动检测并执行：

```
[SKILL_CALL: skill_name | {"param1": "value1", "param2": 42}]
```

无参数调用：

```
[SKILL_CALL: skill_name]
```

调用结果会作为系统消息注入上下文，AI 随后根据结果生成最终回复。

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
2. 扫描文件顶层 `import` / `from ... import` 语句（补充推断）。
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

在 `SessionConfig` 中配置：

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

将 SKILL 文件放入 `{work_path}/skills/` 目录即可自动加载。

## 检查清单

编写完成后，对照以下清单确认：

- [ ] 文件放在 `{work_path}/skills/` 目录下
- [ ] `SKILL_META` 包含 `name` 和 `description`
- [ ] `name` 仅包含字母、数字、下划线
- [ ] `description` 足够清晰，AI 能根据它判断何时调用
- [ ] `run()` 函数存在且参数名与 `parameters` 定义匹配
- [ ] `run()` 包含 `data_store=None` 和 `**kwargs` 参数
- [ ] 返回值为 `dict` 或可序列化对象
- [ ] 不依赖未安装的第三方库（或在 `dependencies` 中显式声明）
- [ ] 不包含长时间阻塞操作（注意 30 秒超时限制）
