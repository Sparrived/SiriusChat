# v0.24.0 迁移指南

## 背景

`v0.24.0` 在 `v0.23.0` 的双根 workspace 基础上，补齐了两项直接影响外部接入的能力：

1. workspace 配置改为支持 JSONC 风格注释，可直接人工编辑
2. `WorkspaceRuntime` 改为通过文件监听热刷新配置，而不是仅在下一次消息调用前做签名比较

如果你已经升级到 `v0.23.0`，这一版主要需要检查配置文件格式和热刷新预期；如果你还在更老版本，建议先阅读 `docs/migration-v0.23.md`。

## 变化摘要

- `main.py --config` 与 `sirius-chat --config` 现在明确支持 JSON 和 JSONC
- `--init-config <path>` 生成的模板会带 `//` 注释
- workspace 自动写出的 `config/session_config.json` 也会保留注释说明
- `WorkspaceRuntime` 会监听：
  - `workspace.json`
  - `config/session_config.json`
  - `providers/provider_keys.json`
  - `roleplay/generated_agents.json`
- 监听触发后会异步刷新配置并重建 engine 上下文；每次 `run_live_message(...)` 前仍保留签名校验兜底

## 推荐的配置文件形态

从 `v0.24.0` 开始，推荐 `--config` 文件保持为轻量配置：

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
      "intent_analysis": true
    }
  }
}
```

### 不再推荐的写法

以下字段不应继续放在 `main.py --config` / `sirius-chat --config` 所读取的轻量配置中：

- `agent`
- `global_system_prompt`
- `participants`
- `work_path`

这些字段属于低层完整 `SessionConfig`，应改为：

- 使用 Python API 手写 `SessionConfig`
- 或先生成 / 选择 `generated_agent_key` 对应的人格资产，再让 workspace 派生完整配置

## 热刷新语义变化

### 旧行为（v0.23.0）

- 修改配置文件后，要等到下一次 `run_live_message(...)` 进入前才会检测到变化

### 新行为（v0.24.0）

- 配置文件一旦被外部修改，watcher 会尽快调度刷新任务
- 运行中的 transcript / session store 会保留
- engine 的 live context 会被重建，以便新配置立即参与后续处理

### 需要注意的点

- 若写入的是非法 JSON/JSONC，runtime 会保留旧配置并记录 warning，直到文件被修正
- 如果宿主环境没有可用的 `watchdog`，每轮消息前的签名校验仍然能兜底，但不再是首选路径

## 示例配置迁移

如果你之前复制了旧示例文件，请至少检查以下两类问题：

1. 删除所有 `multimodal_parse` 任务配置
2. 删除 `session_prompt_splitting.json` 里旧的 `global_system_prompt` / `participants` / `work_path` 顶层字段

多模态升级现在应通过 agent 资产中的 `metadata.multimodal_model` 配置，而不是通过 `multimodal_parse` 辅助任务。

## 升级检查清单

- 把用户可编辑的会话配置迁移到 `generated_agent_key + providers + orchestration` 形态
- 若需要注释，直接把文件改为 JSONC，无需改扩展名
- 确认 `config/session_config.json` 可以被人工修改并被 runtime 热刷新接收
- 删除所有 `multimodal_parse` 相关任务配置
- 若使用独立配置根，确认实际修改的是 `config_root` 下的文件，而不是 data root

## 相关文档

- `README.md`
- `docs/architecture.md`
- `docs/configuration.md`
- `docs/external-usage.md`
- `docs/migration-v0.23.md`