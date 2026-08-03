# 外部 Tools 目录

把用户自定义 Tool 放在此目录。框架会在运行时扫描该目录，并与内置 Tool 一起注册。

```python
from sirius_pulse.tools.models import ToolResult

TOOL_META = {
    "name": "my_tool",
    "description": "说明模型什么时候应该调用这个工具。",
    "parameters": [],
}

async def run(**kwargs):
    return ToolResult.ok(text="完成")
```

不要在 Tool 源码中硬编码 API Key；文件、网络、系统和群管理能力默认不要开放给所有人。
