#!/usr/bin/env python3
"""
批量修改测试文件中多行 OrchestrationPolicy 调用，添加 message_debounce_seconds=0.0。

安全策略：
- 只修改跨行的 OrchestrationPolicy(...) 调用
- 检查括号匹配，完整搜集一个调用
- 仅在末尾参数后添加逗号和新参数
"""

import re
from pathlib import Path

TEST_FILES = [
    "tests/test_api_integrity.py",
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


def fix_multiline_orchestration_policy(content: str) -> tuple[str, int]:
    """
    处理多行 OrchestrationPolicy 调用。
    
    算法：
    1. 按行分割
    2. 找到包含 "OrchestrationPolicy(" 的行
    3. 从该行开始，统计括号深度，收集完整的调用
    4. 检查是否已有 message_debounce_seconds
    5. 如果没有，在倒数第二行末尾添加逗号，新行添加参数
    """
    lines = content.split('\n')
    modifications = 0
    i = 0
    
    while i < len(lines):
        if 'OrchestrationPolicy(' in lines[i]:
            # 检查是否已有参数
            if 'message_debounce_seconds' in lines[i]:
                # 已有参数，跳过
                i += 1
                continue
            
            # 收集完整的 OrchestrationPolicy 调用块
            start_idx = i
            parm_count = lines[i].count('(') - lines[i].count(')')
            j = i + 1
            
            # 收集所有行直到括号匹配
            while parm_count > 0 and j < len(lines):
                parm_count += lines[j].count('(') - lines[j].count(')')
                j += 1
            
            # 现在 lines[start_idx:j] 是完整的调用块
            block_lines = lines[start_idx:j]
            block_text = '\n'.join(block_lines)
            
            # 再次检查整个块中是否已有 message_debounce_seconds
            if 'message_debounce_seconds' in block_text:
                i = j
                continue
            
            # 需要添加参数
            if len(block_lines) == 1:
                # 单行情况（不应该走到这，但保险起见）
                if block_lines[0].endswith(')'):
                    block_lines[0] = block_lines[0][:-1] + ', message_debounce_seconds=0.0)'
                    modifications += 1
            else:
                # 多行情况：在倒数第二行末尾处理
                second_last_idx = len(block_lines) - 2
                last_line = block_lines[-1]
                
                # 取倒数第二行，去掉尾部空格
                second_last = block_lines[second_last_idx].rstrip()
                
                # 检查是否以逗号结尾
                if second_last.endswith(','):
                    # 已有逗号，直接在其后添加新参数行
                    pass
                else:
                    # 没有逗号，添加逗号
                    second_last += ','
                
                block_lines[second_last_idx] = second_last
                
                # 在最后一行前插入新参数行
                indent_match = re.match(r'^(\s*)', last_line)
                indent = indent_match.group(1) if indent_match else '    '
                
                block_lines.insert(-1, f'{indent}message_debounce_seconds=0.0,')
                modifications += 1
            
            # 替换原始内容
            lines[start_idx:j] = block_lines
            i = start_idx + len(block_lines)
        else:
            i += 1
    
    return '\n'.join(lines), modifications


def main():
    root = Path(__file__).parent.parent
    total_mods = 0
    
    for file_path_str in TEST_FILES:
        full_path = root / file_path_str
        
        if not full_path.exists():
            print(f"SKIP {file_path_str} - 文件不存在")
            continue
        
        print(f"处理：{file_path_str}...", end=' ')
        
        content = full_path.read_text(encoding='utf-8')
        new_content, mods = fix_multiline_orchestration_policy(content)
        
        if mods > 0:
            full_path.write_text(new_content, encoding='utf-8')
            print(f"OK {mods} 处修改")
            total_mods += mods
        else:
            print("OK (无需修改)")
    
    print(f"\n总计修改：{total_mods} 处")
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
