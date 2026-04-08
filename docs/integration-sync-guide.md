# 外部调用同步指南

> 供 AI 编码助手在修改框架接口后同步更新外部调用代码。

## 核心公共接口清单

所有外部调用统一从 `sirius_chat.api` 导入。下表列出最常修改的接口及其签名：

| 接口 | 模块位置 | 说明 |
|------|----------|------|
| `arun_live_message()` | `api/engine.py` | 异步门面：单条消息处理 |
| `run_live_message()` | `core/engine.py` → `AsyncRolePlayEngine` | 引擎实例方法 |
| `run_live_session()` | `core/engine.py` → `AsyncRolePlayEngine` | 会话初始化 |
| `create_session_config_from_selected_agent()` | `api/prompting.py` | 按 agent_key 创建配置 |
| `OrchestrationPolicy` | `config/models.py` | 编排策略数据类 |
| `SessionConfig` | `models/models.py` | 会话配置主模型 |
| `build_system_prompt()` | `async_engine/prompts.py` | 系统提示词构建 |

## 参数传递链路

当新增参数（如 `environment_context`）需要从公共 API 传递到底层时，完整链路为：

```
arun_live_message()          # api/engine.py — 公共门面
  └─ engine.run_live_message()   # core/engine.py — 引擎入口
       └─ _process_live_turn()       # 单轮处理
            └─ _generate_assistant_message()  # 生成回复
                 └─ _build_chat_main_request_context()  # 构建请求上下文
                      └─ _build_system_prompt()              # 内部转发
                           └─ build_system_prompt()              # prompts.py — 真正构建
```

**同步原则**：在链路任一层新增参数时，必须逐层向下传递直至实际消费点。

## run_live_message 当前签名

```python
async def run_live_message(
    self,
    config: SessionConfig,
    turn: Message,
    transcript: Transcript | None = None,
    session_reply_mode: str | None = None,
    finalize_and_persist: bool = True,
    environment_context: str = "",       # v0.8.0 新增
) -> Transcript:
```

> v0.9.0 破坏性变更：`on_message` 参数已移除。使用 `engine.subscribe(transcript)` 事件流替代。

## arun_live_message 当前签名

```python
async def arun_live_message(
    engine: AsyncRolePlayEngine,
    config: SessionConfig,
    turn: Message,
    transcript: Transcript | None = None,
    environment_context: str = "",       # v0.8.0 新增
) -> Transcript:
```

## subscribe 事件流签名 (v0.9.0 新增)

```python
async def subscribe(
    self,
    transcript: Transcript,
    *,
    max_queue_size: int = 256,
) -> AsyncIterator[SessionEvent]:
```

## asubscribe facade 签名 (v0.9.0 新增)

```python
async def asubscribe(
    engine: AsyncRolePlayEngine,
    transcript: Transcript,
    *,
    max_queue_size: int = 256,
) -> AsyncIterator[SessionEvent]:
```

## OrchestrationPolicy 关键字段

```python
@dataclass
class OrchestrationPolicy:
    unified_model: str = ""
    enable_skills: bool = True
    skill_call_marker: str = "SKILL_CALL"
    max_skill_rounds: int = 3
    skill_execution_timeout: float = 30.0   # v0.8.0 新增，0=不限制
    auto_install_skill_deps: bool = True    # v0.8.0 新增，自动安装 SKILL 依赖
    enable_prompt_driven_splitting: bool = False
    split_marker: str = "<MSG_SPLIT>"
    session_reply_mode: str = "always"
    memory_extract_batch_size: int = 5
    memory_extract_min_content_length: int = 30
    # ... 更多字段见 config/models.py
```

## 新增 / 变更字段时的同步检查清单

1. **内部链路**：按上方"参数传递链路"逐层添加参数并转发。
2. **公共门面** (`api/engine.py`)：确保 `arun_live_message` 等门面函数签名与引擎一致。
3. **`api/__init__.py`**：若新增了公共类/函数，加入 `__all__`。
4. **文档**：
   - `docs/external-usage.md`：更新代码示例与参数表。
   - `docs/architecture.md`：更新模块描述。
   - `.github/skills/framework-quickstart/SKILL.md`：更新架构速查。
   - `.github/skills/external-integration/SKILL.md`：更新接入指南。
5. **测试**：在 `tests/` 下验证签名兼容性（`inspect.signature` 断言）。
6. **CHANGELOG**：在 `[Unreleased]` 下记录变更。

## 外部调用方迁移要点

当外部项目升级 Sirius Chat 版本后，需检查：

- 新增的可选参数（如 `environment_context`）使用默认值即可无缝兼容。
- 若 `OrchestrationPolicy` 新增了字段，现有 JSON/dict 配置无需修改（dataclass 默认值兜底）。
- 破坏性变更会在 CHANGELOG 中的 `Breaking Changes` 部分注明，需按指引调整。

## 引用文件索引

| 文件 | 作用 |
|------|------|
| `sirius_chat/api/__init__.py` | 公共 API 出口 |
| `sirius_chat/api/engine.py` | 门面函数 |
| `sirius_chat/core/engine.py` | 引擎核心实现 |
| `sirius_chat/async_engine/prompts.py` | 系统提示词构建 |
| `sirius_chat/config/models.py` | 配置数据类 |
| `sirius_chat/models/models.py` | 会话/消息模型 |
| `docs/external-usage.md` | 外部使用文档 |
| `docs/architecture.md` | 架构文档 |
