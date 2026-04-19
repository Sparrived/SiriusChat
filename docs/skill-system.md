# SKILL 系统

> **可扩展能力层** — 让 AI 角色能"动手"而不只是"动嘴"。

## 一句话定位

SKILL 系统是 Sirius Chat 的**插件机制**。它允许你写一段 Python 代码，让 AI 角色在对话中调用外部能力——查系统信息、截屏幕、调 API、读写文件，等等。

## 核心设计哲学

**文件即插件**：一个 SKILL 就是一个 `.py` 文件，不需要注册表、不需要装饰器、不需要复杂的包结构。把文件丢进 `skills/` 目录，引擎启动时自动发现、自动加载、自动安装依赖。

**AI 自主调用**：SKILL 不是人手动触发的，而是 AI 在生成回复时**自己决定**要不要调用。引擎把可用的 SKILL 列表注入系统提示词，AI 在需要时会输出 `[SKILL_CALL: skill_name | {...}]` 标记，引擎解析并执行。

**安全隔离**：每个 SKILL 有自己的 JSON 数据存储、自己的依赖、自己的错误边界。一个 SKILL 崩溃不会拖垮引擎。

## SKILL 文件格式

一个最小可用的 SKILL：

```python
SKILL_META = {
    "name": "hello_world",
    "description": "打一个招呼，返回当前时间",
    "version": "1.0.0",
    "parameters": {
        "name": {
            "type": "str",
            "description": "要问候的名字",
            "required": True,
        }
    },
}


def run(name: str, **kwargs):
    from datetime import datetime
    return {"greeting": f"你好 {name}，现在是 {datetime.now():%H:%M}"}
```

### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | SKILL 唯一标识，字母下划线 |
| `description` | ✅ | 给 AI 看的功能描述，决定 AI 会不会调用它 |
| `version` | ❌ | 语义化版本 |
| `developer_only` | ❌ | `True` 时只有开发者身份的用户能调用 |
| `silent` | ❌ | `True` 时技能结果不追加到回复文本，默认 `False` |
| `dependencies` | ❌ | 第三方依赖列表，自动安装 |
| `parameters` | ❌ | 参数 Schema（dict 或 list） |

### 参数类型

| 声明类型 | Python 类型 | 说明 |
|---------|------------|------|
| `str` | `str` | 字符串 |
| `int` | `int` | 整数 |
| `float` | `float` | 浮点数 |
| `bool` | `bool` | 布尔，支持 `"true"` / `"1"` / `"yes"` 字符串 |
| `list[str]` | `list[str]` | 字符串列表，JSON 或逗号分隔 |
| `dict` | `dict` | 字典，JSON 解析 |

### 依赖注入

`run()` 函数可以通过参数名自动接收以下注入：

```python
def run(query: str, data_store=None, invocation_context=None, **kwargs):
    ...
```

- `data_store`：`SkillDataStore` 实例，每个 SKILL 独立的 JSON KV 存储
- `invocation_context`：`SkillInvocationContext`，包含调用者身份（用于 `developer_only` 校验）
- `**kwargs`：接收所有未声明的参数（兜底）

## AI 调用语法

AI 在生成回复时，如果需要调用 SKILL，会在回复文本中嵌入标记：

```
[SKILL_CALL: system_info | {}]
```

带参数的调用：

```
[SKILL_CALL: desktop_screenshot | {"region": "full"}]
```

引擎在收到回复后：
1. 用正则解析所有 `[SKILL_CALL: ...]` 标记
2. 把标记从回复文本中**剥离**，得到干净的自然语言回复
3. 对每个 SKILL 调用：
   - 校验参数类型和必填项
   - 校验开发者权限（`developer_only`）
   - 在 `asyncio.to_thread()` 中执行（防止阻塞事件循环）
4. 把执行结果追加到回复末尾：
   ```
   [SKILL 'system_info' 结果] CPU: 12%, 内存: 8GB/16GB...
   ```

## 数据存储（SkillDataStore）

每个 SKILL 有一个独立的 JSON 文件：

```
{work_path}/skill_data/{skill_name}.json
```

API：
- `data_store.get(key, default=None)`
- `data_store.set(key, value)`
- `data_store.delete(key)`
- `data_store.keys()` / `data_store.all()`

**特性**：
- 懒加载（第一次访问时才读文件）
- 脏检测（只有修改过才写回磁盘）
- 原子写入（temp file + replace）

**Artifact 目录**：二进制文件（如截图）存到 `{work_path}/skill_data/artifacts/{skill_name}/`

## 内置 SKILL

框架自带以下内置 SKILL：

| SKILL | 权限 | 功能 | 备注 |
|-------|------|------|------|
| `system_info` | 所有人 | 返回 CPU、内存、磁盘、网络、OS 信息（依赖 `psutil`） | |
| `desktop_screenshot` | **仅开发者** | 截取桌面屏幕（依赖 `Pillow`），返回图片 + 文字摘要 | |
| `learn_term` | 所有人 | 将术语、俚语、黑话记录到自传体记忆 glossary | `silent=True`，结果不追加到回复 |
| `url_content_reader` | 所有人 | 读取指定网页的文本内容 | |
| `bing_search` | 所有人 | 通过 Bing 搜索网络内容 | |

内置 SKILL 存放在 `sirius_chat/skills/builtin/`，会被自动加载。如果 workspace 的 `skills/` 目录下有同名文件，**workspace 版本会覆盖内置版本**。

## 依赖自动安装

SKILL 加载时，系统会：
1. 读取 `SKILL_META["dependencies"]`
2. 如果没有显式声明，用 AST 静态分析 `run()` 文件中的所有 `import`，推断依赖
3. 过滤掉标准库和已安装的包
4. 调用 `uv pip install`（优先）或 `pip install` 安装缺失依赖
5. 刷新 `importlib` 缓存

常见 import-name 到 package-name 的映射已内置：
- `PIL` → `Pillow`
- `bs4` → `beautifulsoup4`
- `cv2` → `opencv-python`

## 开发者权限

`developer_only=True` 的 SKILL 有两道防线：
1. **注册时过滤**：非开发者用户看不到该 SKILL 的工具描述（不会被注入系统提示词）
2. **执行时校验**：即使 AI 输出了调用标记，执行前会检查 `invocation_context.caller_is_developer`，未授权返回中文错误信息

开发者身份由 `UserProfile.is_developer` 决定，通常在 `primary_user.json` 中配置。

## 与引擎的集成

### Legacy 引擎

`AsyncRolePlayEngine` 在 `_build_system_prompt()` 阶段把 SKILL 描述注入 `<available_skills>` 区块，包含调用格式和可用技能列表。AI 在生成回复时自行决定调用。

### Emotional 引擎

`EmotionalGroupChatEngine` 通过 `set_skill_runtime(registry, executor)` 注入 SKILL 运行时。执行流程：

```
process_message() → _execution() → _generate() → 拿到回复
                                            ↓
                                    _process_skill_calls()
                                            ↓
                                    解析标记 → 剥离 → 执行 → 追加结果
```

### WorkspaceRuntime

`WorkspaceRuntime` 在初始化时自动创建 `SkillRegistry` + `SkillExecutor`，从 `layout.skills_dir()` 加载 SKILL（包含内置），并在创建引擎时注入。配置热刷新时会重新加载 SKILL 目录。

## 写一个 SKILL 的完整流程

1. 在 workspace 的 `skills/` 目录创建 `my_skill.py`
2. 写 `SKILL_META` + `run()` 函数
3. 如果需要持久化，用 `data_store` 参数
4. 如果需要开发者权限，加 `"developer_only": True`
5. 重启引擎或等待配置热刷新
6. 在对话中引导 AI 调用（或直接测试）

**最佳实践**：
- `description` 要写清楚使用场景，AI 靠它判断什么时候调用
- 返回 dict 时尽量包含 `text_blocks` 和 `multimodal_blocks`（图片等），引擎会自动处理
- 耗时的操作（网络请求、文件读写）在 `run()` 里做，引擎会自动放到线程池执行
- 出错时返回 `{"error": "..."}`，引擎会格式化失败信息
