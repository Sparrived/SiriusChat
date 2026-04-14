# SiriusChat Engine 文件接管规格

## 目标

目标不是“插件尽量少写文件”，而是更严格的语义：

- 插件只提供工作目录和业务参数
- workspace 布局、文件名、迁移、回滚、清理策略全部由 sirius-chat 决定
- 插件不再实现 copy/move/backup 型文件管理逻辑
- 插件不再手工决定 `provider_keys.json`、`session_config.json`、`generated_agents.json`、`session_state.db` 等文件的写入时机

理想调用形态应收敛到：

```python
runtime = open_workspace_runtime(work_path, bootstrap=bootstrap)

await runtime.run_live_message(
	session_id=session_id,
	turn=turn,
	environment_context=environment_context,
	user_profile=user_profile,
	on_reply=on_reply,
	timeout=timeout,
)
```

插件只负责：

1. QQ 事件接入
2. 向 engine 传递业务输入
3. 向用户展示结果

## 当前插件侧已收缩到的边界

当前插件已经去掉了自动 copy/move/backup legacy 文件的运行时逻辑，不再在启动时充当“第二个文件管理器”。

当前仍临时保留在插件侧的职责，仅限 engine 还没有提供对应能力的部分：

1. 将宿主配置默认值桥接为 workspace 默认配置
2. 从宿主 `providers` 配置构建运行期 provider 对象
3. QQ 向导交互流程
4. 少量只读型运维展示，例如记忆和 token 汇总渲染

当前明确不再由插件自动处理的部分：

1. 不再自动把 `_global_session` / `_provider_registry` / `_global_user_memory` 拷贝进新 workspace
2. 不再自动把旧文件移动到 `backup`
3. 不再自动导入 legacy transcript
4. 不再自动把 host provider 配置写成 workspace 的 `provider_keys.json`

也就是说，engine 当前做不到的外部 legacy 文件接管，先保持现状，不再由插件补一个自定义迁移器。

## 当前仍阻止“插件零文件管理”的缺口

下面这些缺口一日不补，插件就仍然需要做少量桥接：

### 1. 缺少 host bootstrap 入口

当前 `WorkspaceRuntime` 能从 workspace 文件恢复配置，但缺少一个稳定入口，让宿主把“初始默认值”直接交给 runtime，再由 runtime 自己决定是否写入 workspace。

结果就是：

- 插件仍要自己调用 `ConfigManager.save_workspace_config(...)`
- 插件仍要自己决定首次初始化何时把宿主默认值投影到 workspace

### 2. 缺少 provider 的 host 注入与托管协议

当前 runtime 有两种模式：

1. 从 workspace 的 `providers/provider_keys.json` 加载 provider
2. 由外部直接传入 provider 对象

这还不够，因为插件宿主往往掌握的是“Provider 配置”，不是最终 provider 实例，也不希望自己负责把配置文件写进 workspace。

结果就是：

- 插件仍要自己 `merge_provider_sources(...)`
- 插件仍要自己构造 `AutoRoutingProvider`
- 若想把宿主配置沉淀进 workspace，插件还得自己写 provider 文件

### 3. 缺少外部 legacy 根目录导入能力

`WorkspaceMigrationManager` 目前只能处理“workspace 根目录内部的旧平铺布局”。

但像本插件这样的真实接入层，legacy 文件可能分散在：

- `_global_session/`
- `_provider_registry/`
- `_global_user_memory/`

这些路径在 workspace 根目录之外。engine 目前没有标准 API 让宿主声明这些 legacy source 并交给 engine 统一导入。

结果就是：

- 想接旧数据，只能插件自己 copy/move
- 不想让插件管文件，就只能暂时维持 legacy 现状

### 4. 缺少 roleplay 资产与 workspace 默认配置的一体化 bootstrap

目前 roleplay 资产、active agent、workspace config、`SessionConfig` 之间仍缺少一个高层统一装配入口。

结果就是：

- 插件向导完成后，仍要自己把人格资产选择结果投影到 workspace 默认配置
- 旧 `session_config.json` 若要转成新 workspace 资产，也只能插件自己桥接

### 5. 缺少面向向导/宿主的 workspace 读写 API

插件向导并不是想直接改文件，它想做的是：

- 读取当前 workspace 默认值
- 让用户改几个字段
- 让 engine 自己完成持久化与热刷新

当前缺少一个清晰的高层 API 来表达这个过程。

结果就是：

- 插件只能读/写 `plugin.config`
- 然后再把这些值二次投影到 workspace 文件

## sirius-chat 侧需要补的改动

## 1. 引入 WorkspaceBootstrap

需要新增一个专门描述“宿主提供的默认值/覆盖项”的对象，而不是逼宿主自己写 workspace 文件。

建议：

```python
@dataclass
class WorkspaceBootstrap:
	active_agent_key: str | None = None
	session_defaults: SessionDefaults | None = None
	orchestration_defaults: dict[str, object] | None = None
	provider_entries: list[dict[str, object]] | None = None
	provider_policy: dict[str, object] | None = None
	generated_agent_key: str | None = None
```

然后扩展：

```python
def open_workspace_runtime(
	work_path: Path,
	*,
	bootstrap: WorkspaceBootstrap | None = None,
	persist_bootstrap: bool = True,
) -> WorkspaceRuntime: ...
```

要求：

1. runtime 自己决定 bootstrap 如何合并到当前 workspace
2. runtime 自己决定哪些字段持久化到 `workspace.json` / `config/session_config.json`
3. 宿主不再直接调用 `ConfigManager.save_workspace_config(...)`

## 2. 引入 host provider bootstrap 协议

需要让 runtime 接收“provider 配置项”，而不是只接受 provider 实例或 provider 文件。

建议二选一：

### 方案 A

```python
class WorkspaceRuntime:
	def set_provider_entries(self, entries: list[dict[str, object]]) -> None: ...
```

### 方案 B

```python
@dataclass
class WorkspaceBootstrap:
	provider_entries: list[dict[str, object]] | None = None
```

由 runtime 在内部完成：

1. provider entry 校验
2. 与 workspace provider registry 的合并策略
3. `AutoRoutingProvider` 构造
4. 按 policy 决定是否持久化到 workspace

这样插件就不需要：

1. `merge_provider_sources(...)`
2. `AutoRoutingProvider(...)`
3. `WorkspaceProviderManager.save_from_entries(...)`

## 3. 扩展 WorkspaceMigrationManager 支持 external sources

这是最关键的一条。必须让 engine 能接手“workspace 根目录之外的 legacy 数据”。

建议新增：

```python
@dataclass
class ExternalLegacySource:
	name: str
	root: Path
	mappings: list[LegacyMapping]


@dataclass
class LegacyMapping:
	source: Path
	target_kind: str
	session_id: str | None = None
```

以及：

```python
class WorkspaceMigrationManager:
	def migrate_external_sources(
		self,
		*,
		sources: list[ExternalLegacySource],
		cleanup_policy: Literal["keep", "backup", "delete"] = "keep",
		dry_run: bool = False,
	) -> MigrationReport: ...
```

至少需要覆盖这些 target kind：

1. `provider_registry`
2. `user_memory_dir`
3. `event_memory_dir`
4. `self_memory`
5. `token_usage_db`
6. `generated_agents`
7. `generated_agent_traces`
8. `session_store`
9. `participants`
10. `skills_dir`
11. `skill_data_dir`

这样插件就只需要声明：

```python
sources = [
	ExternalLegacySource(name="legacy-provider", root=... ),
	ExternalLegacySource(name="legacy-user-memory", root=... ),
	ExternalLegacySource(name="legacy-session", root=... ),
]
```

然后完全由 engine 决定：

1. copy 还是 move
2. 是否 backup
3. 锁文件怎么处理
4. 迁移报告如何返回

## 4. 引入 workspace 级 roleplay bootstrap API

需要把“角色资产选择”和“workspace 默认配置”统一收口。

建议新增：

```python
class RoleplayWorkspaceManager:
	def bootstrap_active_agent(
		self,
		*,
		agent_key: str,
		session_defaults: SessionDefaults | None = None,
		orchestration_defaults: dict[str, object] | None = None,
	) -> WorkspaceConfig: ...

	def bootstrap_from_legacy_session_config(
		self,
		*,
		source: Path,
		agent_key: str | None = None,
	) -> WorkspaceConfig: ...
```

目标：

1. 插件不再自己把生成的人格资产投影到 workspace config
2. 插件不再自己处理旧 `session_config.json` 到 workspace 的转换
3. active agent、session defaults、orchestration defaults 的最终写入都由 engine 完成

## 5. 引入面向宿主/向导的 workspace 读写 API

建议新增：

```python
class WorkspaceRuntime:
	def export_workspace_defaults(self) -> dict[str, object]: ...
	async def apply_workspace_updates(self, patch: dict[str, object]) -> WorkspaceConfig: ...
```

用于这种宿主场景：

1. 读出当前默认值
2. 用户在向导里修改少数字段
3. 由 engine 校验并持久化
4. runtime 自动触发热刷新

这样插件就不需要理解 `workspace.json` / `session_config.json` 的最终文件结构。

## 6. 引入显式的 engine 维护命令

建议提供：

```python
class WorkspaceRuntime:
	async def migrate_legacy(
		self,
		*,
		external_sources: list[ExternalLegacySource] | None = None,
		cleanup_policy: Literal["keep", "backup", "delete"] = "keep",
		dry_run: bool = False,
	) -> MigrationReport: ...
```

用于：

1. 迁移前 dry-run
2. 返回完整报告
3. 明确由 engine 负责 cleanup/backups

这样迁移就不再散落在每个插件的启动逻辑里。

## 插件侧在 engine 补齐前暂时保留的现状

在上述能力补齐前，插件侧只保留下面这些最小桥接，且不再新增任何 copy/move/backup 逻辑：

1. 首次运行时把宿主默认值同步到 workspace 默认配置
2. 从宿主配置构建运行期 provider 对象
3. 向导层读写宿主配置，再触发 runtime 重建

下面这些 legacy 文件先维持现状，不再由插件自动搬运：

1. `_global_session/` 内旧 transcript
2. `_provider_registry/provider_keys.json`
3. `_global_user_memory/users/*.json`
4. 任何需要 backup/move 才能完成的旧数据

## 最终验收标准

当 sirius-chat 补齐后，插件侧应该能收缩到下面这个边界：

### 插件允许做的事

1. `open_workspace_runtime(...)`
2. 提供 `session_id`、`turn`、`environment_context`、`user_profile`、`on_reply`、`timeout`
3. 调用 workspace 级更新 API
4. 展示 engine 返回的迁移报告或错误

### 插件不再做的事

1. 不再 `save_workspace_config(...)`
2. 不再 `save_from_entries(...)`
3. 不再 `merge_provider_sources(...)`
4. 不再 `store.load()` / `store.save()` 管理持久化边界
5. 不再 `copy2` / `move` / `rmtree` / backup legacy 文件
6. 不再决定 workspace 文件名和目录结构

## 结论

如果目标是“插件侧不需要管理文件”，那关键不是继续精简插件里的迁移代码，而是让 sirius-chat 提供：

1. host bootstrap
2. host provider bootstrap
3. external legacy source migration
4. roleplay/workspace bootstrap
5. 面向向导的 workspace 读写 API

在这些能力到位前，插件最多只能做到“停止新增自定义文件管理逻辑，并把 engine 管不了的部分维持现状”。
