"""
日志系统配置模块

提供结构化日志配置，支持以下功能：
- 日志级别可配置 (DEBUG/INFO/WARNING/ERROR)
- 两种输出格式：Console（易读）和JSON（易解析）
- 异步日志处理（可选）
- 日志文件循环（可选）
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

# 日志级别类型
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# 日志格式类型
LogFormat = Literal["console", "json"]


class JSONFormatter(logging.Formatter):
    """JSON格式化器，将日志转换为JSON结构化输出"""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 添加额外的上下文信息（extra字段）
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in (
                    "name",
                    "msg",
                    "args",
                    "created",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "message",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "asctime",
                ):
                    if not key.startswith("_"):
                        log_data[key] = value

        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # 添加堆栈信息（若启用）
        if record.stack_info:
            log_data["stack"] = record.stack_info

        return json.dumps(log_data, ensure_ascii=False)


class FlushingFileHandler(logging.FileHandler):
    """实时刷新的文件处理器 - 每条日志立即写入硬盘"""

    def emit(self, record: logging.LogRecord) -> None:
        """发射日志记录后立即刷新"""
        try:
            super().emit(record)
            self.flush()  # 立即刷新到磁盘
        except Exception:
            self.handleError(record)


class FlushingTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """实时刷新的定时轮换文件处理器"""

    def emit(self, record: logging.LogRecord) -> None:
        """发射日志记录后立即刷新"""
        try:
            super().emit(record)
            self.flush()  # 立即刷新到磁盘
        except Exception:
            self.handleError(record)


def _archive_old_logs(log_file: Path) -> None:
    """
    将已存在的日志文件归档到 archive 目录下
    需在日志处理器创建前调用，确保文件未被锁定
    
    Args:
        log_file: 日志文件路径
    """
    if not log_file.exists():
        return
    
    # 创建归档目录
    archive_dir = log_file.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    
    # 使用时间戳为旧日志重命名，避免冲突
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_file = archive_dir / f"{log_file.stem}_{timestamp}{log_file.suffix}"
    
    try:
        # 先复制，再删除原文件（比直接移动更安全）
        shutil.copy2(str(log_file), str(archive_file))
        log_file.unlink()
    except Exception:
        # 如果失败（如权限问题），忽略错误，继续创建新日志
        pass


def setup_log_archival(log_file: Path) -> None:
    """
    在应用启动时调用，在创建日志处理器之前执行
    将旧日志文件归档
    
    Args:
        log_file: 主日志文件路径
    """
    # 确保日志文件的父目录存在
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # 执行归档
    _archive_old_logs(log_file)


class ColoredFormatter(logging.Formatter):
    """带颜色的Console格式化器，提高可读性（仅用于控制台）"""

    # ANSI颜色代码
    COLOR_CODES = {
        "DEBUG": "\033[36m",  # 青色
        "INFO": "\033[32m",  # 绿色
        "WARNING": "\033[33m",  # 黄色
        "ERROR": "\033[31m",  # 红色
        "CRITICAL": "\033[41m",  # 红色背景
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        # 创建基础日志
        fmt = "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        
        # 仅在终端中添加颜色（不修改原record）
        original_levelname = record.levelname
        if sys.stdout.isatty():
            levelname = record.levelname
            color = self.COLOR_CODES.get(levelname, self.RESET)
            record.levelname = f"{color}{levelname}{self.RESET}"
        
        result = formatter.format(record)
        record.levelname = original_levelname  # 恢复原值，防止影响其他处理器

        # 添加额外信息（如果有）
        if hasattr(record, "task") or hasattr(record, "user_id"):
            extra_parts = []
            if hasattr(record, "task"):
                extra_parts.append(f"task={record.task}")
            if hasattr(record, "user_id"):
                extra_parts.append(f"user={record.user_id}")
            if extra_parts:
                result += f" ({', '.join(extra_parts)})"

        return result


class PlainFormatter(logging.Formatter):
    """纯文本格式化器，用于日志文件（无颜色代码）"""

    def format(self, record: logging.LogRecord) -> str:
        fmt = "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        result = formatter.format(record)

        # 添加额外信息（如果有）
        if hasattr(record, "task") or hasattr(record, "user_id"):
            extra_parts = []
            if hasattr(record, "task"):
                extra_parts.append(f"task={record.task}")
            if hasattr(record, "user_id"):
                extra_parts.append(f"user={record.user_id}")
            if extra_parts:
                result += f" ({', '.join(extra_parts)})"

        return result



def configure_logging(
    *,
    level: LogLevel = "INFO",
    format_type: LogFormat = "console",
    log_file: Path | str | None = None,
    enable_file_rotation: bool = False,
    model_calls_log_file: Path | str | None = None,
) -> None:
    """
    配置全局日志系统

    Args:
        level: 日志级别，可选值：DEBUG/INFO/WARNING/ERROR/CRITICAL
        format_type: 输出格式，可选值：console/json
        log_file: 可选的日志文件路径（若指定则同时输出到文件）
        enable_file_rotation: 是否启用日志文件循环（每日轮换）
        model_calls_log_file: 可选的模型调用日志文件路径（独立的专用日志）

    Example:
        ```python
        # 控制台输出（开发环境）
        configure_logging(level="DEBUG", format_type="console")

        # JSON输出到文件（生产环境）
        configure_logging(
            level="INFO",
            format_type="json",
            log_file="logs/app.log",
            enable_file_rotation=True,
            model_calls_log_file="logs/model_calls.log"
        )
        ```
    """
    # 获取根logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level))

    # 清除已有的处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level))

    if format_type == "json":
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(ColoredFormatter())

    root_logger.addHandler(console_handler)

    # 主日志文件处理器（可选）
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if enable_file_rotation:
            # 每日轮换，保留7个备份，实时刷新
            file_handler = FlushingTimedRotatingFileHandler(
                log_path,
                when="midnight",
                interval=1,
                backupCount=7,
                encoding="utf-8",
            )
        else:
            # 实时刷新的文件处理器
            file_handler = FlushingFileHandler(log_path, encoding="utf-8")

        file_handler.setLevel(getattr(logging, level))
        # 文件处理器使用纯文本格式化器（无颜色代码）
        if format_type == "json":
            file_handler.setFormatter(JSONFormatter())
        else:
            file_handler.setFormatter(PlainFormatter())
        root_logger.addHandler(file_handler)

    # 模型调用日志处理器（可选，专用日志文件）
    if model_calls_log_file:
        model_log_path = Path(model_calls_log_file)
        model_log_path.parent.mkdir(parents=True, exist_ok=True)

        # 为模型调用日志创建独立的处理器
        model_handler = FlushingFileHandler(model_log_path, encoding="utf-8")
        model_handler.setLevel(getattr(logging, "INFO"))
        # 文件处理器使用纯文本格式化器（无颜色代码）
        if format_type == "json":
            model_handler.setFormatter(JSONFormatter())
        else:
            model_handler.setFormatter(PlainFormatter())
        
        # 只处理 provider 相关的日志
        model_logger = logging.getLogger("sirius_chat.providers")
        model_logger.addHandler(model_handler)
        model_logger.setLevel(getattr(logging, "INFO"))


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的logger实例"""
    return logging.getLogger(name)


# 便捷导出
__all__ = [
    "configure_logging",
    "setup_log_archival",
    "get_logger",
    "JSONFormatter",
    "PlainFormatter",
    "ColoredFormatter",
    "LogLevel",
    "LogFormat",
]
