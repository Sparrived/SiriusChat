"""
演示 memory_extract 频率控制的效果。

场景：用户连续发送多条短消息。
- 不配置频率控制时：每条消息都执行 memory_extract（LLM 调用频繁）
- 配置 batch_size=3 时：每3条消息执行一次（成本降低67%）
- 配置 batch_size=3 + min_length=50 时：既降频又过滤零碎内容
"""

import asyncio
from pathlib import Path

from sirius_chat.api import (
    AsyncRolePlayEngine,
    OpenAICompatibleProvider,
    create_session_config_from_selected_agent,
    OrchestrationPolicy,
)
from sirius_chat.models import Message


async def demo_frequency_control() -> None:
    """演示频率控制如何减少 LLM 调用。"""
    
    work_path = Path("data/frequency_demo")
    work_path.mkdir(parents=True, exist_ok=True)
    
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com",
        api_key="sk-test",  # 演示用，实际使用真实 key
    )
    
    engine = AsyncRolePlayEngine(provider=provider)
    
    # 场景1：不配置频率控制（默认 batch_size=1，每条消息都提取）
    print("\n=== 场景1：默认频率（batch_size=1）===")
    print("每条消息都会执行 memory_extract 任务")
    print("消息序列：短消息1 -> 短消息2 -> 短消息3")
    print("预期：3 次 LLM 调用")
    print()
    
    # 场景2：配置 batch_size=3（每3条消息执行一次）
    print("=== 场景2：降频（batch_size=3）===")
    print("每3条消息执行一次 memory_extract 任务")
    print("消息序列：短消息1 -> 短消息2 -> 短消息3（执行）-> 短消息4 -> 短消息5 -> 短消息6（执行）")
    print("预期：只有第3、6条消息时执行 LLM 调用")
    print()
    
    # 场景3：配置 batch_size=3 + min_length=50（既降频又过滤）
    print("=== 场景3：降频+内容过滤（batch_size=3, min_length=50）===")
    print("既要求消息计数达到3，也要求内容≥50字符")
    print("消息序列：")
    print("  1. '你好' (9字) -> 跳过（太短）")
    print("  2. '今天天气真好' (9字) -> 跳过（太短）")
    print("  3. '我想和你讨论一下最近的项目进展情况' (50字) -> 跳过（计数=1）")
    print("  4. '最近工作很忙' (7字) -> 跳过（太短）")
    print("  5. '这周完成了需求分析和系统设计两个重要阶段' (50字) -> 跳过（计数=2）")
    print("  6. '下周需要开始代码实现' (11字) -> 跳过（太短）")
    print("  7. '代码实现预计需要两周左右的时间来完成整个功能模块' (60字) -> 执行（计数=3 & 长度足）")
    print("预期：只有第7条消息时执行 LLM 调用")
    print()
    
    # 配置使用示例
    print("=== 配置使用示例 ===")
    print("""
config = create_session_config_from_selected_agent(
    work_path=Path("data/session"),
    agent_key="main_agent",
    orchestration=OrchestrationPolicy(
        task_models={"memory_extract": "doubao-lite"},
        # 频率控制参数
        memory_extract_batch_size=3,           # 每3条消息执行一次
        memory_extract_min_content_length=50,  # 内容至少50字符
    ),
)
""")
    
    print("\n=== 成本对比分析 ===")
    print("假设每个 LLM 调用成本为 $0.001")
    print()
    print("方案1（batch_size=1）：")
    print("  - 100条消息 × 1 次/条 = 100 次调用 = $0.1")
    print()
    print("方案2（batch_size=3）：")
    print("  - 100条消息 ÷ 3 ≈ 33 次调用 = $0.033（节省67%）")
    print()
    print("方案3（batch_size=3 + min_length=50）：")
    print("  - 仅高质量消息提取 ≈ 15-20 次調用 = $0.02（节省80%）")
    print("  - 同时提取质量更高、内容更充分")
    print()
    
    print("=== 推荐配置 ===")
    print("- 快速迭代开发：batch_size=1（默认，完整收集信息）")
    print("- 长期运营应用：batch_size=3, min_length=0（平衡频率と成本）")
    print("- 大规模部署：batch_size=5, min_length=80（极度降本，关注高价值信息）")
    print()


if __name__ == "__main__":
    asyncio.run(demo_frequency_control())
