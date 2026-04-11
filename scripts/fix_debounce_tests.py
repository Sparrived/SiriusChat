#!/usr/bin/env python3
"""
安全批量修改测试文件中 OrchestrationPolicy 实例。
仅修改单行无参数的情况：OrchestrationPolicy() → OrchestrationPolicy(message_debounce_seconds=0.0)

复杂多行情况需要手动修改。详见：.github/skills/write-tests/SKILL.md
"""

import re
import sys
from pathlib import Path

# 需要修改的测试文件
TEST_FILES = [
    "tests/test_async_engine.py",
    "tests/test_engine.py",
    "tests/test_memory_system_v2.py",
    "tests/test_orchestration_config.py",
    "tests/test_public_api.py",
    "tests/test_roleplay_prompting.py",
    "tests/test_self_memory.py",
    "tests/test_session_events.py",
    "tests/test_session_runner.py",
    "tests/test_skill_system.py",
]

DEBOUNCE_PARAM = "message_debounce_seconds=0.0"


def fix_simple_cases(content: str, filename: str) -> tuple[str, int]:
    """
    仅修改最简单的情况，保持安全性。
    1. 单行 OrchestrationPolicy() - 无参数
    """
    modifications = 0
    
    # 仅修改模式：OrchestrationPolicy() → OrchestrationPolicy(message_debounce_seconds=0.0)
    lines = content.split('\n')
    new_lines = []
    
    for line in lines:
        # 简单检查：行中是否有 OrchestrationPolicy() 且没有参数
        if re.search(r'OrchestrationPolicy\s*\(\s*\)', line):
            # 检查行中是否已有 message_debounce_seconds
            if 'message_debounce_seconds' not in line:
                # 替换空的 OrchestrationPolicy()
                new_line = re.sub(
                    r'OrchestrationPolicy\s*\(\s*\)',
                    f'OrchestrationPolicy({DEBOUNCE_PARAM})',
                    line
                )
                if new_line != line:
                    modifications += 1
                    line = new_line
        
        new_lines.append(line)
    
    return '\n'.join(new_lines), modifications


def main():
    root = Path(__file__).parent.parent
    total_mods = 0
    
    for file_path in TEST_FILES:
        full_path = root / file_path
        
        if not full_path.exists():
            print(f"SKIP {file_path} - 文件不存在")
            continue
        
        print(f"处理：{file_path}...", end=' ')
        
        content = full_path.read_text(encoding='utf-8')
        new_content, mods = fix_simple_cases(content, file_path)
        
        if mods > 0:
            full_path.write_text(new_content, encoding='utf-8')
            print(f"OK {mods} 处修改")
            total_mods += mods
        else:
            print("OK (无需修改)")
    
    print(f"\n总计修改：{total_mods} 处")
    print("\n注意：多行 OrchestrationPolicy(...) 调用需要手动修改。")
    print("详见 .github/skills/write-tests/SKILL.md 中的标准模板。")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
