# 迁移指南：v0.13.x → v0.14.x

本文档说明从 v0.13.x 升级到 v0.14.x 所需的变更。

## 概述

v0.14.0 完全重写了回复决策系统，将旧的"意愿分"多参数体系替换为三级参与决策架构（热度 → 意图 → 参与决策）。v0.14.1 移除了所有向后兼容代码。

**核心变化**：
- ~15 个 `auto_reply_*` 配置参数 → 2 个参数（`engagement_sensitivity` + `heat_window_seconds`）
- `core/intent.py`（含 `willingness_modifier`）→ `core/intent_v2.py`（含 `target` 字段）
- `ReplyWillingnessDecision` → `EngagementDecision`
- 新增 `HeatAnalyzer`、`EngagementCoordinator` 子系统

---

## 1. 配置迁移

### 已删除的参数

以下 `OrchestrationPolicy` 参数已**完全移除**，不再接受：

```python
# ❌ 以下参数已删除，传入会引发 TypeError
auto_reply_base_score
auto_reply_threshold
auto_reply_threshold_min
auto_reply_threshold_max
auto_reply_threshold_boost_start_count
auto_reply_probability_coefficient
auto_reply_probability_floor
auto_reply_user_cadence_seconds
auto_reply_group_window_seconds
auto_reply_group_penalty_start_count
auto_reply_assistant_cooldown_seconds
```

### 新增的参数

```python
OrchestrationPolicy(
    # 参与决策灵敏度：0.0(极度克制) - 1.0(积极参与)，默认 0.5
    engagement_sensitivity=0.5,
    # 热度分析滑动窗口（秒），默认 60.0
    heat_window_seconds=60.0,
)
```

### 迁移对照表

| 旧参数 | 新参数 | 说明 |
|--------|--------|------|
| `auto_reply_threshold` (0.58) | `engagement_sensitivity` (0.5) | 旧阈值越低越容易回复；新灵敏度越高越容易回复 |
| `auto_reply_group_window_seconds` (8.0) | `heat_window_seconds` (60.0) | 热度统计窗口，新默认值更大以获取更稳定的热度信号 |
| `auto_reply_probability_coefficient` | — | 已移除，参与决策不再使用概率骰子 |
| `auto_reply_base_score` | — | 已移除，基础分由 `engagement_sensitivity` 控制 |
| `auto_reply_user_cadence_seconds` | — | 已移除，节奏控制由热度分析自动处理 |
| `auto_reply_assistant_cooldown_seconds` | — | 已移除，由回复频率限制器替代 |
| 其它 `auto_reply_*` | — | 已移除 |

### 配置文件示例

**旧版 (v0.13.x)**：
```json
{
  "orchestration": {
    "session_reply_mode": "auto",
    "auto_reply_threshold": 0.58,
    "auto_reply_probability_coefficient": 0.35,
    "auto_reply_group_window_seconds": 8.0,
    "auto_reply_user_cadence_seconds": 7.0,
    "auto_reply_assistant_cooldown_seconds": 12.0
  }
}
```

**新版 (v0.14.x)**：
```json
{
  "orchestration": {
    "session_reply_mode": "auto",
    "engagement_sensitivity": 0.5,
    "heat_window_seconds": 60.0
  }
}
```

---

## 2. 代码迁移

### 意图分析模块

```python
# ❌ 旧版（已删除）
from sirius_chat.core.intent import IntentAnalysis, IntentAnalyzer
result = IntentAnalyzer.fallback_analysis(content, agent_name, alias)
print(result.willingness_modifier)  # 不再存在

# ✅ 新版
from sirius_chat.core.intent_v2 import IntentAnalysis, IntentAnalyzer
result = IntentAnalyzer.fallback_analysis(content, agent_name, alias, participant_names=["小王"])
print(result.target)       # "ai" | "others" | "everyone" | "unknown"
print(result.importance)   # 0.0 - 1.0
print(result.directed_at_ai)  # 属性，等价于 target == "ai"
```

### 参与决策

```python
# ❌ 旧版（已删除）
from sirius_chat.core.engine import ReplyWillingnessDecision

# ✅ 新版
from sirius_chat.core.engagement import EngagementDecision, EngagementCoordinator
from sirius_chat.core.heat import HeatAnalysis, HeatAnalyzer

# 热度分析
heat = HeatAnalyzer.analyze(
    recent_messages=messages,
    window_seconds=60.0,
    agent_name="助手",
)

# 意图分析
intent = IntentAnalyzer.fallback_analysis(content, "助手", "", participant_names=["小王"])

# 综合决策
decision = EngagementCoordinator.decide(
    heat=heat,
    intent=intent,
    sensitivity=0.5,
)
print(decision.should_reply)       # bool
print(decision.engagement_score)   # 0.0 - 1.0
print(decision.reason)             # 人类可读的决策理由
```

### 回复频率限制

```python
# ❌ 旧版（已删除）
engine._check_reply_frequency_limit(transcript, config, turn)

# ✅ 新版
from sirius_chat.core.engagement import EngagementCoordinator

exceeded = EngagementCoordinator.check_reply_frequency_limit(
    assistant_reply_timestamps=timestamps,
    now=datetime.now(timezone.utc),
    window_seconds=60.0,
    max_replies=8,
    exempt_on_mention=True,
    is_mentioned=True,
)
```

### 引擎内部方法

以下引擎内部方法已**完全删除**：

| 已删除方法 | 替代方案 |
|-----------|---------|
| `_evaluate_reply_willingness()` | `EngagementCoordinator.decide()` |
| `_run_intent_analysis()` | `_run_engagement_intent_analysis()`（内部） |
| `_compute_intent_score()` | `IntentAnalyzer.analyze()` / `.fallback_analysis()` |
| `_compute_addressing_score()` | `IntentAnalysis.target` 字段 |
| `_compute_event_relevance_score()` | 已移除，事件相关性由热度和意图隐式处理 |
| `_compute_richness_score()` | 已移除 |
| `_deterministic_probability_roll()` | 已移除，不再使用概率骰子 |

`_should_reply_for_turn()` 签名已简化：

```python
# ❌ 旧版
should_reply, decision = engine._should_reply_for_turn(
    turn=msg, config=config, event_hit_payload=...,
    user_interval_seconds=..., group_recent_count=...,
    assistant_interval_seconds=...,
)  # -> tuple[bool, ReplyWillingnessDecision | None]

# ✅ 新版
should_reply = engine._should_reply_for_turn(msg)  # -> bool
# auto/smart 的实际决策由 _process_live_turn 内部的参与决策系统完成
```

---

## 3. 已删除文件

| 文件 | 说明 |
|-----|------|
| `sirius_chat/core/intent.py` | 旧意图分析模块，由 `core/intent_v2.py` 完全替代 |

---

## 4. 不受影响的功能

以下功能与旧意愿分系统无关，升级后行为不变：

- `session_reply_mode`（`auto`/`always`/`never`）仍然有效
- `reply_frequency_window_seconds` / `reply_frequency_max_replies` / `reply_frequency_exempt_on_mention` 仍然有效
- `enable_intent_analysis` 仍然控制是否使用 LLM 意图分析（否则使用关键词回退）
- `Message.reply_mode` 消息级覆盖仍然有效
- 用户记忆、事件系统、SKILL 系统等均无变化

---

## 5. 快速检查清单

- [ ] 从 `OrchestrationPolicy` 构造中移除所有 `auto_reply_*` 参数
- [ ] 如需调整回复积极性，使用 `engagement_sensitivity`（0-1）
- [ ] 如需调整热度统计窗口，使用 `heat_window_seconds`
- [ ] 将 `from sirius_chat.core.intent import ...` 改为 `from sirius_chat.core.intent_v2 import ...`
- [ ] 将 `willingness_modifier` 字段访问改为 `target` + `importance`
- [ ] 将 `ReplyWillingnessDecision` 引用改为 `EngagementDecision`
- [ ] 运行 `pytest -q` 确认所有测试通过
