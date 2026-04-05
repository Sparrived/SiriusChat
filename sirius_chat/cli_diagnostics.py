"""CLI 诊断与验证模块

提供详细的环境检查和错误诊断能力
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

PrintFunc = Callable[[str], None]


class EnvironmentDiagnostics:
    """环境诊断工具"""

    @staticmethod
    def check_config_file(config_path: Path) -> tuple[bool, str]:
        """检查配置文件是否存在且有效
        
        Returns:
            (is_valid, error_message)
        """
        if not config_path.exists():
            return False, f"配置文件不存在: {config_path}\n修复建议: 使用 --config 明确指定路径或运行 --init-config 创建初始配置"

        try:
            import json
            content = config_path.read_text(encoding="utf-8-sig")
            json.loads(content)
            return True, ""
        except json.JSONDecodeError as e:
            return False, f"配置文件 JSON 格式错误 ({config_path}): {e}"
        except Exception as e:
            return False, f"读取配置文件失败: {e}"

    @staticmethod
    def check_work_path(work_path: Path) -> tuple[bool, str]:
        """检查工作目录是否可访问
        
        Returns:
            (is_valid, error_message)
        """
        try:
            work_path.mkdir(parents=True, exist_ok=True)
            
            # 尝试写入测试文件
            test_file = work_path / ".write_check"
            test_file.write_text("test")
            test_file.unlink()
            
            return True, ""
        except PermissionError:
            return False, f"工作目录无写权限: {work_path}\n修复建议: 检查文件系统权限或使用 --work-path 指定其他目录"
        except Exception as e:
            return False, f"工作目录访问失败: {e}"

    @staticmethod
    def check_python_version() -> tuple[bool, str]:
        """检查 Python 版本
        
        Returns:
            (is_valid, error_message)
        """
        required_version = (3, 11)  # 项目需要 Python 3.11+
        current_version = sys.version_info[:2]
        
        if current_version < required_version:
            return (
                False,
                f"Python 版本过低: {current_version[0]}.{current_version[1]}\n"
                f"需要: {required_version[0]}.{required_version[1]}+ 修复建议: 升级 Python"
            )
        return True, ""

    @staticmethod
    def check_provider_config(config_path: Path) -> tuple[bool, str]:
        """检查 Provider 配置
        
        Returns:
            (is_valid, error_message)
        """
        try:
            import json
            raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
            
            # 统一使用 providers 字段（list format）
            providers_config = list(raw.get("providers", []))
            
            # 向后兼容：若传入 provider 单个对象，则转换为 providers list
            if not providers_config:
                provider_obj = dict(raw.get("provider", {}))
                if provider_obj and "api_key" in provider_obj:
                    providers_config = [provider_obj]
            
            # 从providers列表中提取第一个作为primary provider
            provider_config = {}
            if providers_config and isinstance(providers_config[0], dict):
                provider_config = dict(providers_config[0])
            
            if not provider_config and not providers_config:
                return False, "Provider 配置缺失\n修复建议: 在配置文件中添加 'providers' 字段（推荐）或 'provider' 字段（已废弃，仅用于向后兼容）"
            
            if provider_config:
                api_key = str(provider_config.get("api_key", "")).strip()
                if not api_key:
                    return False, "Provider API Key 为空\n修复建议: 在 'providers' 字段中设置有效的 API Key"
            
            return True, ""
        except Exception as e:
            return False, f"检查 Provider 配置失败: {e}"


def run_preflight_check(
    config_path: Path,
    work_path: Path,
    print_func: PrintFunc = print,
) -> bool:
    """运行启动前检查
    
    Args:
        config_path: 配置文件路径
        work_path: 工作目录
        print_func: 打印函数
        
    Returns:
        True 如果所有检查通过，否则 False
    """
    checks = [
        ("Python 版本", EnvironmentDiagnostics.check_python_version()),
        ("工作目录", EnvironmentDiagnostics.check_work_path(work_path)),
        ("配置文件", EnvironmentDiagnostics.check_config_file(config_path)),
        ("Provider 配置", EnvironmentDiagnostics.check_provider_config(config_path)),
    ]
    
    print_func("=" * 60)
    print_func("Sirius Chat 启动前检查")
    print_func("=" * 60)
    
    all_passed = True
    for check_name, (is_valid, error_msg) in checks:
        status = "✓ 通过" if is_valid else "✗ 失败"
        print_func(f"\n[{status}] {check_name}")
        if not is_valid:
            print_func(f"  错误: {error_msg}")
            all_passed = False
    
    print_func("\n" + "=" * 60)
    if all_passed:
        print_func("所有检查通过，环境已就绪。")
    else:
        print_func("检查失败，请按上述建议修复。")
    print_func("=" * 60)
    
    return all_passed


def generate_default_config(output_path: Path) -> None:
    """生成默认配置文件模板
    
    Args:
        output_path: 输出文件路径
    """
    import json
    
    default_config = {
        "generated_agent_key": "",
        "history_max_messages": 24,
        "history_max_chars": 6000,
        "max_recent_participant_messages": 5,
        "enable_auto_compression": True,
        "provider": {
            "type": "openai-compatible",
            "base_url": "https://api.openai.com",
            "api_key": "your-api-key-here",
        },
        "providers": [],
        "orchestration": {
            "enabled": False,
            "task_models": {},
            "task_budgets": {},
        },
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(default_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
