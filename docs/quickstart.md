# Sirius Chat 快速启动指南

## 环境配置

### 1. 安装依赖

```bash
# 克隆仓库
git clone https://github.com/your-org/sirius_chat.git
cd sirius_chat

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 安装包（开发模式）
python -m pip install -e .

# 安装测试依赖
python -m pip install -e .[test]
```

### 2. 配置环境变量

创建 `.env` 文件在项目根目录：

```bash
# LLM Provider 配置
SIRIUS_MODEL=gpt-4-turbo
SIRIUS_API_KEY=your-api-key-here

# 数据存储路径
SIRIUS_DATA_PATH=./sirius_data

# 任务模型
TASK_MEMORY_EXTRACT_MODEL=gpt-4-mini
TASK_EVENT_EXTRACT_MODEL=gpt-4-mini
```

## 基础会话创建

### 方式 1：使用 JSON 配置文件

创建 `config.json`：

```json
{
  "work_path": "./sirius_data",
  "global_system_prompt": "你是一个有帮助的 AI 助手。",
  "agent": {
    "name": "MyAI",
    "persona": "友好、专业的 AI 助手",
    "model": "gpt-4-turbo",
    "temperature": 0.7,
    "max_tokens": 512,
    "metadata": {
      "alias": "小助手"
    }
  },
  "history_max_messages": 24,
  "enable_auto_compression": true,
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "multimodal_parse": true,
      "event_extract": true
    },
    "task_models": {
      "memory_extract": "gpt-4-mini",
      "event_extract": "gpt-4-mini"
    }
  }
}
```

然后在 Python 中加载：

```python
from pathlib import Path
from sirius_chat.config import ConfigManager
from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.providers.openai import OpenAIProvider

# 加载配置
config_manager = ConfigManager()
session_config = config_manager.load_from_json("config.json")

# 创建 Provider
provider = OpenAIProvider(api_key="your-key")

# 创建引擎
engine = AsyncRolePlayEngine(provider=provider)

# 运行会话
import asyncio

async def main():
    transcript = await engine.run_session(session_config)
    print(f"会话已初始化: {transcript.messages}")

asyncio.run(main())
```

### 方式 2：按环境加载配置

```python
from sirius_chat.config import ConfigManager

config_manager = ConfigManager()

# 加载开发环境配置
config = config_manager.load_from_env("dev")

# 加载生产环境配置
config = config_manager.load_from_env("prod")
```

支持的环境：
- `dev`：开发环境（禁用压缩，更详细的日志）
- `test`：测试环境（使用 mock provider）
- `prod`：生产环境（使用环境变量）

## 会话与对话

### 运行多轮对话

```python
import asyncio
from sirius_chat.models import Message, Participant
from sirius_chat.config import ConfigManager
from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.providers.openai import OpenAIProvider

async def main():
    # 配置和引擎初始化
    config_manager = ConfigManager()
    session_config = config_manager.load_from_json("config.json")
    provider = OpenAIProvider(api_key="your-key")
    engine = AsyncRolePlayEngine(provider=provider)
    
    # 创建用户参与者
    user = Participant(
        name="张三",
        user_id="user_001",
        persona="学生，对 AI 感兴趣",
        aliases=["小三", "张老三"]
    )
    
    # 创建消息列表
    human_turns = [
        Message(
            role="user",
            content="你好，今天天气如何？",
            speaker=user.name,
            channel="chat",
            channel_user_id=user.user_id,
        ),
        Message(
            role="user",
            content="我对机器学习很感兴趣，你能推荐学习资源吗？",
            speaker=user.name,
            channel="chat",
            channel_user_id=user.user_id,
        ),
    ]
    
    # 运行实时会话
    transcript = await engine.run_live_session(
        config=session_config,
        human_turns=human_turns,
    )
    
    # 访问结果
    print(f"总消息数: {len(transcript.messages)}")
    for msg in transcript.messages:
        print(f"[{msg.role}] {msg.content[:50]}...")

asyncio.run(main())
```

## 保存和恢复会话

### 保存会话到文件

```python
import json
from pathlib import Path

# 假设已有 transcript 对象
work_path = Path(session_config.work_path)
work_path.mkdir(parents=True, exist_ok=True)

# 保存用户记忆
from sirius_chat.memory import UserMemoryFileStore

memory_store = UserMemoryFileStore(work_path)
memory_store.save_all(transcript.user_memory)

# 保存会话消息
transcript_data = {
    "messages": [
        {
            "role": msg.role,
            "content": msg.content,
            "speaker": msg.speaker,
        }
        for msg in transcript.messages
    ],
    "session_summary": transcript.session_summary,
}

with open(work_path / "transcript.json", "w", encoding="utf-8") as f:
    json.dump(transcript_data, f, ensure_ascii=False, indent=2)

print(f"会话已保存到: {work_path}")
```

### 恢复会话

```python
import json
from pathlib import Path
from sirius_chat.memory import UserMemoryFileStore, UserMemoryManager
from sirius_chat.models import Message, Transcript

work_path = Path(session_config.work_path)

# 加载用户记忆
memory_store = UserMemoryFileStore(work_path)
user_memory = memory_store.load_all()

# 加载会话消息
transcript = Transcript()
transcript.user_memory.merge_from(user_memory)

with open(work_path / "transcript.json", "r", encoding="utf-8") as f:
    transcript_data = json.load(f)

for msg_data in transcript_data["messages"]:
    msg = Message(
        role=msg_data["role"],
        content=msg_data["content"],
        speaker=msg_data.get("speaker"),
    )
    transcript.add(msg)

transcript.session_summary = transcript_data.get("session_summary", "")

print(f"会话已恢复，共 {len(transcript.messages)} 条消息")
```

## 文件位置说明

```
project/
├── config.json                 # 会话配置文件
├── sirius_data/                # 默认数据存储目录
│   ├── user_profiles/          # 用户配置文件
│   ├── event_memory/           # 事件记忆存储
│   └── transcript.json         # 会话记录
├── .env                        # 环境变量（git ignore）
└── examples/                   # 示例代码
    ├── session_config.json
    ├── memory_extraction.py
    └── multi_turn_conversation.py
```

## 常见任务

### 1. 启用缓存

```python
from sirius_chat.cache import MemoryCache
from sirius_chat.providers.cached import CachedLLMProvider

# 创建缓存（可选）
cache = MemoryCache(max_size=500)

# 包装 Provider 加缓存
base_provider = OpenAIProvider(api_key="key")
cached_provider = CachedLLMProvider(base_provider, cache=cache)

engine = AsyncRolePlayEngine(provider=cached_provider)
```

### 2. 启用多任务编排

```python
config.orchestration.task_enabled = {
    "memory_extract": True,
    "event_extract": True,
    "multimodal_parse": True,
}
config.orchestration.task_models = {
    "memory_extract": "gpt-4-mini",
    "event_extract": "gpt-4-mini",
    "multimodal_parse": "gpt-4-vision",
}
```

### 3. 自定义 Agent 提示词

```python
from sirius_chat.config import Agent, AgentPreset

agent = Agent(
    name="专业助手",
    persona="我是一个专业的技术顾问，具有 10 年的经验...",
    model="gpt-4-turbo",
    temperature=0.7,
    max_tokens=1024,
)

preset = AgentPreset(
    agent=agent,
    global_system_prompt="你是一个专业的技术顾问。优先使用中文回答，保持专业态度。"
)

# 在 SessionConfig 中使用
session_config.preset = preset
```

## 获取帮助

- 📖 查看 [架构文档](architecture.md) 了解系统设计
- 🔧 查看 [配置文档](configuration.md) 了解所有配置选项
- 📚 查看 [完整 API 文档](api.md)
- 💡 查看 [最佳实践](best-practices.md)
- 🐛 查看 [故障排查指南](troubleshooting.md)
