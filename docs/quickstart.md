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

### 方式二：手写最小会话配置

```json
{
  "generated_agent_key": "main_agent",
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com",
      "api_key": "${OPENAI_API_KEY}",
      "healthcheck_model": "gpt-4o-mini"
    }
  ],
  "history_max_messages": 24,
  "history_max_chars": 6000,
  "max_recent_participant_messages": 5,
  "enable_auto_compression": true,
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "event_extract": true,
      "intent_analysis": true,
      "memory_manager": true
    },
    "task_models": {
      "memory_extract": "gpt-4o-mini",
      "event_extract": "gpt-4o-mini",
      "intent_analysis": "gpt-4o-mini",
      "memory_manager": "gpt-4o-mini"
    },
    "enable_prompt_driven_splitting": true
  }
}
```

说明：

- `generated_agent_key` 必须指向 `roleplay/generated_agents.json` 中已存在的人格资产
- `providers` 是推荐入口；CLI 和 `main.py` 都会优先读取这个字段
- 如果你把配置根和运行根分开，真正被热刷新的文件都在配置根下

## 3. 启动方式

### 方式一：库内 CLI

```bash
sirius-chat --config session.jsonc --work-path data/runtime --config-root data/config
```

### 方式二：仓库入口 main.py

```bash
python main.py --config session.jsonc --work-path data/runtime --config-root data/config --store sqlite
```

说明：

- `--config-root`：保存 `workspace.json`、`config/`、`providers/`、`roleplay/`、`skills/`
- `--work-path`：保存 `sessions/`、`memory/`、`token/`、`skill_data/`、`primary_user.json`
- 不传 `--config-root` 时，默认退化为单根模式

## 4. Python API 最小示例

### 推荐入口：WorkspaceRuntime

```python
import asyncio
from pathlib import Path

from sirius_chat.api import Message, UserProfile, open_workspace_runtime
from sirius_chat.providers.mock import MockProvider


async def main() -> None:
    runtime = open_workspace_runtime(
        Path("./data/runtime"),
        config_path=Path("./data/config"),
        provider=MockProvider(responses=["我理解你的意思。"]),
    )

    transcript = await runtime.run_live_message(
        session_id="group:demo",
        turn=Message(role="user", speaker="小王", content="帮我整理一下方案"),
        user_profile=UserProfile(user_id="u_xiaowang", name="小王"),
    )

    print(transcript.as_chat_history())
    await runtime.close()


asyncio.run(main())
```

### 高级入口：完整 SessionConfig

```python
import asyncio

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.config import ConfigManager
from sirius_chat.models import Message
from sirius_chat.providers.openai_compatible import OpenAICompatibleProvider


async def main() -> None:
    manager = ConfigManager()
    config = manager.load_from_json("full-session.jsonc")
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com",
        api_key="your-key",
    )
    engine = AsyncRolePlayEngine(provider=provider)

    transcript = await engine.run_live_session(config=config)
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=Message(role="user", speaker="小王", content="你好"),
    )
    print(transcript.as_chat_history())


asyncio.run(main())
```

## 5. 目录布局

推荐的双根 layout：

```text
project/
├── session.jsonc                  # 用户可编辑的轻量会话配置
├── data/
│   └── runtime/
│       ├── sessions/
│       ├── memory/
│       ├── token/
│       └── skill_data/
├── config/
│   ├── workspace.json
│   ├── config/
│   │   └── session_config.json
│   ├── providers/
│   │   └── provider_keys.json
│   ├── roleplay/
│   │   └── generated_agents.json
│   └── skills/
└── examples/
    ├── session.json
    ├── session_multimodel.json
    ├── session_prompt_splitting.json
    └── external_api_usage.py
```

## 6. 热刷新与配置编辑

从 v0.24.0 开始：

- `workspace.json`
- `config/session_config.json`
- `providers/provider_keys.json`
- `roleplay/generated_agents.json`

会被 `WorkspaceRuntime` 通过文件监听自动刷新。文件写坏时会暂时保留旧配置，修复后会再次生效。

## 7. 常见任务

### 打开帮助

```bash
sirius-chat --help
python main.py --help
```

### 输出单轮 transcript

```bash
sirius-chat --config session.jsonc --work-path data/runtime --output transcript.json
```

### 启用提示词驱动分割

```json
{
  "orchestration": {
    "enable_prompt_driven_splitting": true
  }
}
```

### 配置多模态升级

多模态升级不再使用 `multimodal_parse` 辅助任务，而是通过人格资产中的 `agent.metadata["multimodal_model"]` 指定。

## 8. 下一步

1. 若你还在手写 `agent` 和 `global_system_prompt` 到 `--config` 文件，先阅读 [docs/configuration.md](docs/configuration.md)。
2. 若你是从 v0.23.x 升级，请阅读 [docs/migration-v0.24.md](docs/migration-v0.24.md)。
3. 若你要接外部系统，优先阅读 [docs/external-usage.md](docs/external-usage.md)。
