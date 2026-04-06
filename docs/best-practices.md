# Sirius Chat 最佳实践指南

## 1. 并发会话管理

### 安全的并发执行

```python
import asyncio
from sirius_chat.config import ConfigManager
from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.models import Message, Participant
from sirius_chat.providers.openai import OpenAIProvider

async def run_user_session(engine, config, user_id: str, messages: list[str]):
    """为单个用户运行会话。"""
    user = Participant(
        name=f"用户_{user_id}",
        user_id=user_id
    )
    
    turns = [
        Message(role="user", content=msg, speaker=user.name)
        for msg in messages
    ]

    transcript = await engine.run_live_session(config=config)
    for turn in turns:
        transcript = await engine.run_live_message(
            config=config,
            transcript=transcript,
            turn=turn,
            session_reply_mode=turn.reply_mode,
            finalize_and_persist=False,
        )
    return {"user_id": user_id, "transcript": transcript}

async def main():
    # 初始化（共享 provider 和 engine）
    provider = OpenAIProvider(api_key="key")
    engine = AsyncRolePlayEngine(provider=provider)
    config_manager = ConfigManager()
    config = config_manager.load_from_json("config.json")
    
    # 并发运行多个用户会话
    tasks = [
        run_user_session(engine, config, f"user_{i}", [f"你好，我是用户 {i}"])
        for i in range(10)
    ]
    
    results = await asyncio.gather(*tasks)
    
    for result in results:
        print(f"用户 {result['user_id']} 的会话已完成")
```

### 会话隔离

```python
from pathlib import Path

async def run_isolated_session(session_id: str, user_input: str):
    """为每个会话使用独立的 work_path。"""
    config_manager = ConfigManager()
    baseconfig = config_manager.load_from_json("config.json")
    
    # 为这个会话创建独立目录
    session_work_path = Path(baseconfig.work_path) / session_id
    session_work_path.mkdir(parents=True, exist_ok=True)
    
    # 创建这个会话的专用配置
    from sirius_chat.config import SessionConfig
    session_config = SessionConfig(
        work_path=session_work_path,
        preset=baseconfig.preset,
        history_max_messages=baseconfig.history_max_messages,
        orchestration=baseconfig.orchestration,
    )
    
    # 运行会话
    provider = OpenAIProvider(api_key="key")
    engine = AsyncRolePlayEngine(provider=provider)
    
    transcript = await engine.run_session(session_config)
    # ... 处理输入和交互
```

## 2. 错误处理

### 捕获和处理异常

```python
import asyncio
from sirius_chat.exceptions import SiriusChatException, ProviderError

async def safe_run_session(config, human_turns):
    """使用完整的错误处理运行会话。"""
    try:
        engine = AsyncRolePlayEngine(provider=provider)
        transcript = await engine.run_live_session(config=config)
        for turn in human_turns:
            transcript = await engine.run_live_message(
                config=config,
                transcript=transcript,
                turn=turn,
                session_reply_mode=turn.reply_mode,
                finalize_and_persist=False,
            )
        return transcript
    except ProviderError as e:
        # Provider 相关错误（网络、API 限制等）
        print(f"Provider 错误: {e}")
        print(f"建议: 检查 API 密钥、网络连接和 API 配额")
        raise
    except SiriusChatException as e:
        # Sirius Chat 内部错误
        print(f"系统错误: {e}")
        print(f"错误代码: {e.error_code}")
        raise
    except asyncio.TimeoutError:
        # 超时
        print("会话超时，请检查网络连接或增加超时时间")
        raise
    except Exception as e:
        # 未预期的错误
        print(f"未知错误: {e}")
        raise
```

### 重试逻辑

```python
import asyncio
from functools import wraps

def async_retry(max_attempts: int = 3, backoff_factor: float = 2.0):
    """异步重试装饰器。"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        wait_time = backoff_factor ** attempt
                        print(f"第 {attempt + 1} 次失败，等待 {wait_time} 秒后重试...")
                        await asyncio.sleep(wait_time)
                    else:
                        print(f"第 {max_attempts} 次尝试失败")
            raise last_error
        return wrapper
    return decorator

@async_retry(max_attempts=3, backoff_factor=1.5)
async def run_session_with_retry(config, human_turns):
    """带重试的会话运行。"""
    engine = AsyncRolePlayEngine(provider=provider)
    transcript = await engine.run_live_session(config=config)
    for turn in human_turns:
        transcript = await engine.run_live_message(
            config=config,
            transcript=transcript,
            turn=turn,
            session_reply_mode=turn.reply_mode,
            finalize_and_persist=False,
        )
    return transcript
```

## 3. 资源管理

### 内存管理

```python
import psutil
import asyncio

async def monitor_memory_usage():
    """监控内存使用情况。"""
    while True:
        memory_info = psutil.Process().memory_info()
        memory_mb = memory_info.rss / 1024 / 1024
        print(f"当前内存: {memory_mb:.1f} MB")
        
        # 如果内存过高，触发压缩
        if memory_mb > 500:
            print("内存使用过高，触发压缩...")
            # 清理缓存或进行 GC
            import gc
            gc.collect()
        
        await asyncio.sleep(60)  # 每分钟检查一次

async def main_with_monitoring():
    """带内存监控的主程序。"""
    monitor_task = asyncio.create_task(monitor_memory_usage())
    
    try:
        # 你的主程序
        await run_session(config, human_turns)
    finally:
        monitor_task.cancel()
```

### 文件资源

```python
from pathlib import Path
from contextlib import asynccontextmanager

@asynccontextmanager
async def session_resources(session_id: str):
    """会话资源管理器。"""
    work_path = Path(f"./sessions/{session_id}")
    work_path.mkdir(parents=True, exist_ok=True)
    
    try:
        yield work_path
    finally:
        # 清理临时文件
        import shutil
        # 可选: 删除临时数据
        # shutil.rmtree(work_path)

async def main():
    async with session_resources("session_123") as work_path:
        config.work_path = work_path
        await run_session(config)
        # 自动清理实施
```

## 4. 性能优化

### 启用缓存

```python
from sirius_chat.cache import MemoryCache
from sirius_chat.providers.openai import OpenAIProvider

# 创建有限大小的缓存
cache = MemoryCache(max_size=500)

provider = OpenAIProvider(api_key="key")

# 从缓存中获取值
async def cached_generation(model: str, messages: list):
    from sirius_chat.cache import generate_generation_request_key
    
    cache_key = generate_generation_request_key(model, messages, "system prompt")
    
    # 检查缓存
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached
    
    # 调用 provider
    result = await provider.generate(...)
    
    # 存储到缓存（1 小时 TTL）
    await cache.set(cache_key, result, ttl=3600)
    return result
```

### 压缩会话历史

```python
from sirius_chat.config import SessionConfig
from sirius_chat.models import Transcript

def configure_compression(config: SessionConfig):
    """配置会话压缩策略。"""
    config.enable_auto_compression = True
    config.history_max_messages = 20  # 保留最近 20 条消息
    config.history_max_chars = 4000   # 总字符数不超过 4000
    return config

async def manual_compress(transcript: Transcript):
    """手动压缩会话。"""
    print(f"压缩前: {len(transcript.messages)} 条消息, "
          f"{sum(len(m.content) for m in transcript.messages)} 字符")
    
    transcript.compress_for_budget(
        max_messages=10,
        max_chars=2000,
    )
    
    print(f"压缩后: {len(transcript.messages)} 条消息, "
          f"{sum(len(m.content) for m in transcript.messages)} 字符")
```

### 异步 I/O 优化

```python
async def batch_operations(items: list, batch_size: int = 10):
    """分批处理，避免过载。"""
    import asyncio
    
    results = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        batch_results = await asyncio.gather(
            *[process_item(item) for item in batch]
        )
        results.extend(batch_results)
        await asyncio.sleep(0.1)  # 批次间延迟
    
    return results

async def process_item(item):
    """处理单个项目。"""
    # 异步操作
    pass
```

### 性能监控与分析

```python
from sirius_chat.performance import PerformanceProfiler, Benchmark, MetricsCollector

# 使用上下文管理器追踪执行性能
async def run_monitored_session():
    with PerformanceProfiler("session_execution"):
        # 你的会话逻辑
        transcript = await engine.run_live_session(config=config)
        for turn in human_turns:
            transcript = await engine.run_live_message(
                config=config,
                transcript=transcript,
                turn=turn,
                session_reply_mode=turn.reply_mode,
                finalize_and_persist=False,
            )
    
    # 获取性能指标
    collector = MetricsCollector()
    stats = collector.get_stats("session_execution")
    print(f"平均执行时间: {stats['avg_duration_ms']}ms")
    print(f"内存增长: {stats['avg_memory_delta_kb']}KB")

# 使用装饰器自动追踪函数性能
from sirius_chat.performance import profile_async

@profile_async
async def my_expensive_operation():
    """此函数的执行时间和内存消耗会自动被记录。"""
    # 执行操作
    pass

# 基准测试，对比不同实现的性能
from sirius_chat.performance import Benchmark

def fibonacci(n):
    return 1 if n <= 1 else fibonacci(n-1) + fibonacci(n-2)

result = Benchmark.run_sync(
    fibonacci,
    args=(10,),
    iterations=100,
)

print(f"最小时间: {result.min}ms")
print(f"平均时间: {result.mean}ms")
print(f"最大时间: {result.max}ms")
print(f"标准差: {result.stdev}ms")
```

## 5. 日志和调试

### 启用详细日志

```python
import logging

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 获取特定模块的日志
engine_logger = logging.getLogger("sirius_chat.async_engine")
engine_logger.setLevel(logging.DEBUG)

# 添加文件处理器
file_handler = logging.FileHandler("sirius_chat.log")
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))
engine_logger.addHandler(file_handler)
```

### 性能分析

```python
import cProfile
import pstats
from io import StringIO

def profile_code(func):
    """性能分析装饰器。"""
    def wrapper(*args, **kwargs):
        profiler = cProfile.Profile()
        profiler.enable()
        
        result = func(*args, **kwargs)
        
        profiler.disable()
        stream = StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats('cumulative')
        stats.print_stats(20)  # 显示前 20 个函数
        
        print(stream.getvalue())
        return result
    
    return wrapper

async def main():
    @profile_code
    async def run_session():
        # ... 会话代码
        pass
    
    await run_session()
```

## 6. 部署建议

### 生产环境配置

```python
# 使用环境变量
from sirius_chat.config import ConfigManager
import os

config_manager = ConfigManager()

# 根据环境加载配置
environment = os.getenv("ENVIRONMENT", "prod")
config = config_manager.load_from_env(environment)

# 验证必需的环境变量
required_vars = ["SIRIUS_API_KEY", "SIRIUS_MODEL"]
for var in required_vars:
    if not os.getenv(var):
        raise ValueError(f"缺少必需的环境变量: {var}")
```

### 监控和告警

```python
from datetime import datetime
import json

class SessionMonitor:
    def __init__(self):
        self.sessions = {}
    
    def record_session(self, session_id: str, user_id: str, duration: float, message_count: int):
        """记录会话指标。"""
        self.sessions[session_id] = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "duration": duration,
            "message_count": message_count,
        }
        
        # 检查异常情况
        if duration > 300:  # 超过 5 分钟
            print(f"告警: 会话 {session_id} 耗时过长 ({duration}s)")
    
    def export_metrics(self, path: str):
        """导出指标到文件。"""
        with open(path, "w") as f:
            json.dump(self.sessions, f, indent=2)

monitor = SessionMonitor()
```
