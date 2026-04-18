# Sirius Chat 快速启动指南

## 1. 安装

```bash
# 克隆仓库
git clone https://github.com/Sparrived/SiriusChat.git
cd sirius_chat

# 创建虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 安装包
python -m pip install -e .

# 安装测试依赖
python -m pip install -e .[test]
```

## 2. 准备最小配置

### 方式一：直接生成带注释模板

```bash
python main.py --init-config session.jsonc
```

这会生成一个可直接编辑的 JSONC 模板，支持 `//` 注释。

### 方式二：手写 Emotional Engine 配置

```json
{
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com",
      "api_key": "${OPENAI_API_KEY}",
      "healthcheck_model": "gpt-4o-mini"
    }
  ],
  "persona": "warm_friend",
  "emotional_engine": {
    "sensitivity": 0.5,
    "proactive_silence_minutes": 30,
    "task_model_overrides": {
      "response_generate": { "model": "gpt-4o", "max_tokens": 512 },
      "cognition_analyze": { "model": "gpt-4o-mini", "max_tokens": 384 }
    }
  }
}
```

说明：

- `persona` 支持模板名（`warm_friend`、`sarcastic_techie` 等）或 `"generated"`（从 roleplay 资产加载）
- `emotional_engine` 下的字段全部可选，缺失时使用默认值
- 完整示例见 `examples/session_emotional.json`

## 3. 启动方式

### 方式一：库内 CLI

```bash
sirius-chat --config session.jsonc --work-path data/runtime --config-root data/config
```

### 方式二：仓库入口 main.py

```bash
# 使用 Emotional Engine（v0.28+ 默认）
python main.py --config session.jsonc --work-path data/runtime --config-root data/config --engine emotional

# 指定人格模板
python main.py --config session.jsonc --engine emotional --persona sarcastic_techie
```

说明：

- `--config-root`：保存 `workspace.json`、`config/`、`providers/`、`roleplay/`、`skills/`
- `--work-path`：保存 `engine_state/`、`memory/`、`token/`、`skill_data/`
- 不传 `--config-root` 时，默认退化为单根模式

## 4. Python API 最小示例

### 推荐入口：create_emotional_engine

```python
import asyncio
from pathlib import Path

from sirius_chat.api import create_emotional_engine, Message, Participant
from sirius_chat.providers.mock import MockProvider


async def main() -> None:
    provider = MockProvider(responses=[
        "<think>用户说你好，我也友好回应</think><say>你好呀！</say>"
    ])

    engine = create_emotional_engine(
        work_path=Path("./data/runtime"),
        provider=provider,
        persona="warm_friend",
        config={"sensitivity": 0.6},
    )
    engine.start_background_tasks()

    msg = Message(role="human", content="你好", speaker="user_1")
    result = await engine.process_message(
        message=msg,
        participants=[Participant(name="user_1", user_id="u1")],
        group_id="default",
    )

    print(f"策略: {result['strategy']}")
    print(f"回复: {result.get('reply')}")
    print(f"内心: {result.get('thought')}")

    engine.save_state()


asyncio.run(main())
```

### 使用 WorkspaceRuntime

```python
import asyncio
from pathlib import Path

from sirius_chat.api import open_workspace_runtime, Message, Participant
from sirius_chat.providers.mock import MockProvider


async def main() -> None:
    runtime = open_workspace_runtime(
        Path("./data/runtime"),
        config_path=Path("./data/config"),
        provider=MockProvider(responses=["<say>你好！</say>"]),
    )

    # 创建 emotional engine
    engine = runtime.create_emotional_engine(
        persona="warm_friend",
        config={"sensitivity": 0.6},
    )
    engine.start_background_tasks()

    msg = Message(role="human", content="你好", speaker="user_1")
    result = await engine.process_message(
        message=msg,
        participants=[Participant(name="user_1", user_id="u1")],
        group_id="default",
    )

    print(f"回复: {result.get('reply')}")

    await runtime.close()


asyncio.run(main())
```

## 5. 目录布局

推荐的双根 layout：

```text
project/
├── session.jsonc                  # 用户可编辑的轻量会话配置
├── data/
│   └── runtime/
│       ├── engine_state/          # 引擎状态持久化
│       ├── memory/                # 记忆系统数据
│       ├── token/                 # Token 用量统计
│       └── skill_data/            # SKILL 数据存储
├── config/
│   ├── workspace.json
│   ├── providers/
│   │   └── provider_keys.json
│   ├── roleplay/
│   │   └── generated_agents.json
│   └── skills/
└── examples/
    ├── session_emotional.json     # Emotional Engine 配置示例
    └── external_api_usage.py
```

## 6. 热刷新与配置编辑

以下文件会被 `WorkspaceRuntime` 通过文件监听自动刷新：

- `workspace.json`
- `providers/provider_keys.json`
- `roleplay/generated_agents.json`

文件写坏时会暂时保留旧配置，修复后会再次生效。

## 7. 常见任务

### 打开帮助

```bash
sirius-chat --help
python main.py --help
```

### 启用提示词驱动分割（legacy）

Legacy 引擎支持提示词驱动消息分割。Emotional Engine 不使用此功能。

### 配置多模态升级

多模态升级不再使用 `multimodal_parse` 辅助任务，而是通过人格资产中的 `agent.metadata["multimodal_model"]` 指定。

## 8. 下一步

1. 阅读 [docs/engine-emotional.md](engine-emotional.md) 了解 Emotional Engine 的四层认知架构。
2. 阅读 [docs/configuration.md](configuration.md) 了解所有支持的配置字段。
3. 若你要接外部系统，优先阅读 [docs/external-usage.md](external-usage.md)。
