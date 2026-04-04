"""Test configuration and fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

# 为了能够导入项目根目录的 main.py，需要添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
