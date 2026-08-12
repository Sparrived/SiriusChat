# 跑团（TRPG）扩展设计

**状态：** Proposed  
**日期：** 2026-08-08  
**范围：** Sirius Pulse 的群聊跑团、人格 KP、剧本编辑与战役管理。

## 决策

采用“TRPG MCP 领域服务 + Sirius 原生 `trpg` Tool 适配器 + 插件/WebUI 管理入口”的组合架构。

- MCP 服务是战役状态、骰点、剧本版本、权限与事件审计的唯一权威。
- Sirius 原生 Tool 从可信调用上下文取得人格、群号和消息来源，再代表该人格调用 MCP 服务。
- 群调度器在模型生成前决定当前跑团回合允许哪个人格发言。
- 插件只承载显式管理命令；WebUI 提供剧本与战役的可视化管理面。

不采用仅插件方案：人格 worker 相互独立，插件无法单独保证跨人格状态一致性。也不让模型直接调用未绑定运行时身份的 MCP Tool，以免模型伪造群号、人格或角色权限。

## 目标与非目标

目标：

1. 支持一个群绑定一个进行中的战役、一个指定人格担任 KP。
2. 支持 `kp_only` 与 `kp_plus_actor` 两种模式。
3. 支持剧本的草稿、校验、发布、版本锁定和战役引用。
4. 保证骰点、状态改变、回合推进可复核、幂等且可从事件日志恢复。
5. 在多人格群聊中，保证游戏回合仅由被授权人格公开输出。

非目标：

1. 一期不实现通用可编程规则引擎、地图战棋或多群共享战役。
2. `kp_plus_actor` 不宣称信息盲隔离；同一模型已经见过 KP 秘密。
3. 不将整本剧本或所有秘密写入人格系统提示词。

## 架构

```mermaid
flowchart LR
    User[群成员消息] --> Adapter[NapCat 适配器]
    Adapter --> Coordinator[TRPG 回合协调器]
    Coordinator --> Dispatcher[GroupDispatcher 租约]
    Dispatcher --> Persona[获准人格]
    Persona --> Tool[Sirius 原生 trpg Tool]
    Tool --> MCP[TRPG MCP 服务]
    MCP --> Store[战役状态与事件库]
    Plugin[/trpg 管理插件] --> Admin[TRPG 管理 API]
    WebUI[剧本编辑器与战役面板] --> Admin
    Admin --> Store
```

TRPG 服务在一期与 Sirius 部署在同一主机或同一 Docker 网络。使用 Streamable HTTP 以服务多个 persona worker；端口只暴露在内部网络。若始终同主机，也可先以 Python 标准库 SQLite 文件作为存储，使用 WAL、`busy_timeout`、`BEGIN IMMEDIATE` 与乐观版本号。

## 人格模式

| 模式 | 公开输出者 | 角色行为 | 保密边界 |
| --- | --- | --- | --- |
| `kp_only` | KP 人格 | KP 叙事、判定、NPC | 玩家只读取公开信息；推荐一期实现 |
| `kp_plus_actor` | 同一 KP 人格，按回合切换 KP/托管角色 | KP 可兼任一个托管角色或 NPC | 软约束，不能阻止模型利用已知秘密 |
| `split_kp_and_player`（后续） | 独立 KP 与玩家人格 | 分别扮演 KP 与角色 | MCP 按身份返回不同视图，具备硬隔离 |

## MCP 与原生 Tool 边界

MCP 服务应提供固定、小规模且结构化的工具接口：

| Tool | 副作用 | 用途 |
| --- | --- | --- |
| `trpg_get_context` | 只读 | 返回调用身份可见的紧凑场景、轮次和待办 |
| `trpg_submit_intent` | 写入 | 提交玩家行动意图，不允许任意修改状态 |
| `trpg_roll` | 写入 | 服务端执行受限骰式并记录结果与种子 |
| `trpg_apply_kp_resolution` | 写入 | 提交 KP 的判定、状态变化与下一回合 |
| `trpg_lookup` | 只读 | 按权限检索剧本、NPC、线索与规则 |
| `trpg_record_note` | 写入 | 写公开或授权范围内的战役笔记 |
| `trpg_session_status` | 只读 | 返回战役状态，供插件、WebUI 和模型使用 |

原生 `trpg` Tool 接收 `invocation_context` 与 `chat_context`，并只把框架计算出的 `persona_id`、`group_id`、`message_id`、调用者身份和服务凭证传给 MCP。模型参数只能表达行动、选择和叙事结果，不能表达权限、所属群或当前角色。

所有写请求必须包含：

- `campaign_id`
- `turn_token`
- `expected_revision`
- `idempotency_key`

服务端必须校验战役-群绑定、人格角色、当前回合、版本号和操作权限。禁止提供通用 SQL、原始对象覆盖或由模型指定可见范围的接口。

## 领域数据

### 剧本

- `scenario`：剧本逻辑实体。
- `scenario_revision`：不可变发布版本。
- `scene`、`location`、`npc`、`clue`、`encounter`、`rulepack`：归属于特定剧本版本的结构化资产。
- `secret` / `fact`：带 `public`、`kp`、`actor:<id>` 等可见范围。

### 战役

- `campaign`：`group_id`、固定 `scenario_revision_id`、模式、KP 人格和生命周期状态。
- `campaign_member`：人类、人格或 NPC 的角色卡、权限和可见域。
- `campaign_state`：当前场景、当前回合、状态版本和快照。
- `campaign_event`：只追加的动作、判定与回合事件。
- `dice_roll`：骰式、随机种子、原始值、修正、最终值与关联事件。

战役状态采用事件追加加快照。写入事务先锁定战役，验证 `expected_revision` 和 `turn_token`，追加事件，更新快照和 revision，再提交。相同 `idempotency_key` 返回已完成结果而不重复掷骰或推进。

## 回合与群聊流程

1. NapCat 收到群消息，TRPG 协调器按 `group_id` 查询是否存在运行中的战役。
2. 协调器识别当前阶段与合法发言人格，向现有 `GroupDispatcher` 提供 `preferred_worker_id`。
3. 群调度器发放公开输出租约，其他人格只观察该消息。
4. 获准人格得到最小化战役上下文；需要时经 `trpg` Tool 获取授权视图并提交判定。
5. MCP 服务原子写入事件，返回结构化状态差异和下一回合指令。
6. 人格生成公开叙事并经既有发送路径输出；发送完成或失败后释放调度租约。

工具链中显式点名 KP 的新消息可以进入下一轮，但不得绕开战役的 `turn_token`。不在本回合的输入应记录为观察事件或待处理行动，而不是直接改写状态。

## 剧本编辑与战役面板

新增 WebUI 路由和页面，而非复用通用插件配置表单：

- 剧本列表：创建、复制、删除草稿、版本历史。
- 编辑页：章节/场景/NPC/地点/线索树，Markdown 内容、结构化字段和引用校验。
- 双预览：玩家公开视图与 KP 视图。
- 发布：校验引用、必填字段和可见范围后创建不可变版本。
- 战役面板：当前场景、回合、成员、骰点审计、事件时间线、暂停/恢复/结束和恢复点。

插件提供 `/trpg 开团`、`/trpg 暂停`、`/trpg 恢复`、`/trpg 结束`、`/trpg 状态`、`/trpg 加入` 等人工管理入口。插件与 WebUI 都调用同一领域服务，不能直接写数据库。

## 上下文策略

每轮只注入：当前场景摘要、近期事件、当前目标、当前回合、可选行动和调用人格有权看到的事实。完整剧本、未激活章节与其他身份秘密按需检索。

剧本文本、玩家输入和 NPC 台词一律作为数据块，不得成为系统级指令。参与人格应维持相同的允许 Tool 集与顺序；由服务端拒绝无权限调用，而非按角色动态隐藏 Tool。

## 实施阶段

### 第一阶段：可玩的最小闭环

1. 建立领域数据层、迁移、事件日志、骰式解析与幂等写入。
2. 实现 MCP 服务和 Sirius 原生 `trpg` Tool 适配器。
3. 增加单群单战役的 `kp_only` 回合协调器。
4. 实现剧本草稿/发布和战役启动的最小 WebUI。
5. 支持规则无关的 `NdM+K` 骰式与剧本声明的技能字段。

### 第二阶段：人格参与

1. 实现 `kp_plus_actor` 的分阶段行为与显式风险提示。
2. 增加角色卡、NPC 状态、公开/私有笔记和战役控制台。
3. 增加规则包扩展点，但不在核心中实现通用脚本执行器。

### 第三阶段：扩展能力

1. 独立 KP/玩家人格的硬信息隔离。
2. 多群、旁观者、导入导出和剧本分支。
3. 地图、战斗格与特定规则系统集成。

## 验收与测试

- 两个 persona worker 同时处理同一玩家消息时，只允许指定 KP 公开回复。
- 并发写同一战役时，只有一个 revision 成功；冲突调用返回可恢复错误。
- 相同幂等键不会重复掷骰、重复扣减属性或重复推进回合。
- 玩家、旁观者与其他人格不能读取 KP 秘密。
- 服务重启后可由快照和事件日志恢复相同状态。
- 发布新剧本版本不改变进行中战役固定的旧版本。
- NapCat 模拟端到端测试覆盖开团、行动、骰点、KP 叙事、暂停、恢复和结束。

## 框架改动清单

### 必须修改的既有模块

| 模块 | 改动 | 原因 |
| --- | --- | --- |
| `sirius_pulse/core/group_dispatcher.py` | 新增 `required_worker_id`，写入 `dispatcher_candidates` 并在最终选择前严格过滤；新增 `required_worker_unavailable` 等可观测原因 | 现有 `preferred_worker_id` 只是分数偏好，目标 worker 不可用时会回退给其他人格，不能保证 KP 独占回合 |
| `sirius_pulse/platforms/onebot_v11/napcat/adapter.py` | 在 `preview_dispatch()` 后、`dispatcher.coordinate()` 前应用跑团回合指令，并传递 `required_worker_id` | 这是所有 worker 争夺群聊租约前的唯一正确接入位置 |
| `sirius_pulse/core/engine_core.py` 与 `sirius_pulse/core/tool_engine_context.py` | 增加窄的异步前置分发策略注册能力，例如 `register_dispatch_override()` | 让 `trpg` Tool 在生命周期中提供只读回合决策，避免把 TRPG 逻辑硬编码进 NapCat 或通用认知评分 |
| `sirius_pulse/tools/models.py`、`executor.py`、Tool 遥测/会话展示路径 | 为 ToolResult 增加敏感度/可见性元数据，并在遥测、会话链和 WebUI 中采用脱敏摘要 | KP 秘密、线索和私有角色卡不能被泛用 Tool 历史或对话链直接展示 |
| `sirius_pulse/webui/routes.py`、`server.py`、`static/app.js` | 注册 TRPG API handler、导航项和独立静态页面 | 通用插件配置页只适合配置，不能承载剧本树、版本差异与战役控制台 |
| Docker Compose / 部署脚本 | 增加内部 TRPG 服务、健康检查和持久化目录，不对公网暴露 MCP 端口 | 多人格 worker 必须连接同一权威服务，不能各自启动独立状态库 |

`GroupDispatcher` 的 SQLite schema 现有部署已存在，新增列必须带兼容迁移（检查 `PRAGMA table_info` 后 `ALTER TABLE`），不能只修改 `CREATE TABLE IF NOT EXISTS`。

### 新增模块

| 模块 | 职责 |
| --- | --- |
| `sirius_pulse/trpg/` | 领域模型、SQLite migration、事务仓储、状态机、骰式 AST 解析、事件快照和权限投影 |
| `sirius_pulse/trpg/mcp_server.py` | Streamable HTTP MCP 服务，向多个 persona worker 提供固定工具集 |
| `sirius_pulse/trpg/turn_coordinator.py` | 只读查询当前群的战役与回合，生成强制/静默/观察分发指令 |
| `sirius_pulse/tools/builtin/trpg.py` | 受信任的模型可见 Tool；从 `chat_context`、`invocation_context` 获得真实来源，再调用 TRPG MCP 服务 |
| `plugins/trpg_control/` | `/trpg` 的开团、暂停、恢复、结束、状态等显式管理命令 |
| `sirius_pulse/webui/trpg_api.py` 与 `static/pages/trpg.*` | 剧本编辑、发布、战役控制台和骰点审计页面 |
| `tests/test_trpg_*.py` | 领域事务、权限、调度、Tool、WebUI 与端到端回归测试 |

一期不修改通用 `MCPClientManager`。它当前把远程工具直接映射为模型可见 Tool，且不会注入可信运行时上下文。TRPG 通过原生 `trpg` Tool 建立受信任桥接，避免为一个特例改变所有第三方 MCP 的参数和安全模型。只有未来出现第二个同类“受信任 MCP 桥接”需求时，才抽取通用能力。

### 前置分发指令

新增的前置策略只返回一个小型值对象，不执行写入：

```text
DispatchOverride(
  should_reply: bool | None,
  score: float | None,
  strategy: immediate | delayed | silent | None,
  required_worker_id: str,
  reason: str,
)
```

TRPG 协调器只在战役运行且消息是当前阶段的合法输入时，强制 `should_reply=True` 并设置 `required_worker_id=kp_worker_id`。普通闲聊继续使用现有认知评分；KP 不在线时记录 `required_worker_unavailable`，不降级为其他人格代答。

### 可信上下文与密钥

原生 `trpg` Tool 应接收框架自动注入的 `chat_context` 与 `invocation_context`。前者已有 `group_id`、`user_id`、`chat_type`、`chat_id`，后者绑定真实调用者。该 Tool 再附加当前 persona 标识和消息 ID，构成服务端可验证的主体。

服务 URL 可以保存在 `tool_data/trpg.json` 的私有配置字段；令牌只允许由环境变量名称引用，不写入仓库、模型可见 schema、日志或 Tool 结果。战役角色、群绑定和剧本权限均放在领域服务，不放进 `experience.json` 或某个人格的普通配置。

### 提示词、记忆与隐私

动态跑团上下文应在构建当前 ChatRequest 时添加独立的 `【跑团会话】` 段，不改写人格的 `【身份锚定】`。该段只含调用人格有权读取的摘要、回合指令和当前行动；完整剧本和秘密通过 Tool 读取。

`trpg` Tool 的结果应标记 `visibility` 与 `redacted_summary`。模型收到授权的完整数据，普通 Tool 遥测、会话链默认视图和跨人格资料只保存脱敏摘要。运行中的战役事件以领域库为准，不依赖通用语义记忆或对话摘要恢复状态。

### 明确不改的部分

- 不把战役状态存入 `PersonaExperienceConfig`、人格记忆或插件 `_config.json`。
- 不修改 PromptFactory 的身份锚定来存储剧本；跑团属于每次会话的动态上下文。
- 不动态按角色增删模型可见 Tool；保持稳定工具集合，权限由服务端裁决。
- 不通过提高 worker priority、群 @ 或发送后删除消息实现 KP 独占回合。
- 不在一期引入 ORM、消息队列、规则脚本沙箱或独立前端框架；标准库 SQLite、现有 aiohttp WebUI 和已有 MCP 依赖足够。
