# C2/C3 完整优化实现方案

## 架构设计

### 当前发现
- AsyncRolePlayEngine管理各类tasks
- 没有SessionManager，session由外部管理
- Task系统通过config.orchestration控制

### 新增组件

#### 1. 后台任务框架 (BackgroundTaskManager)

```python
class BackgroundTaskManager:
    """轻量级后台任务管理器（不依赖APScheduler）"""
    
    def __init__(self, memory_manager: UserMemoryManager):
        self.memory_manager = memory_manager
        self.tasks: dict[str, asyncio.Task] = {}
        
    async def start_memory_compressor(self, interval_seconds: int = 3600):
        """启动内存压缩定时任务（默认1小时）"""
        
    async def start_transient_cleanup(self, interval_seconds: int = 1800):
        """启动临时数据清理定时任务（默认30分钟）"""
        
    async def stop_all(self):
        """停止所有后台任务"""
```

#### 2. C2: RESIDENT vs TRANSIENT 分离存储

```python
@dataclass
class MemoryFact:
    ...
    is_transient: bool = False  # 标记是否是临时事实
    created_at: datetime | None = None  # 创建时间（用于过期判断）

# 在UserMemoryManager中：
def get_resident_facts(user_id: str) -> list[MemoryFact]:
    """获取resident facts（confidence > 0.85）"""
    
def cleanup_expired_transient_facts(user_id: str, max_age_minutes: int = 30):
    """清理过期的transient事实"""
```

#### 3. C3: 动态压缩（后台任务）

```python
def compress_memory_facts(user_id: str, similarity_threshold: float = 0.8):
    """
    压缩用户的memory facts
    - 聚类相似的facts
    - 合并高相似度的facts为一个
    - 保留代表性facts
    """
```

## 实现步骤

### Phase 1: 基础设施 (2-3小时)
1. 设计BackgroundTaskManager基类
2. 实现轻量级任务调度器（不使用APScheduler）
3. 集成到AsyncRolePlayEngine

### Phase 2: C2实现 (2小时)
1. 在MemoryFact添加is_transient标记
2. 在UserMemoryManager中添加resident/transient分离逻辑
3. 修改序列化（to_dict/from_dict）
4. 添加cleanup_expired_transient_facts()

### Phase 3: C3实现 (3小时)
1. 实现compress_memory_facts()压缩算法
2. 集成到BackgroundTaskManager
3. 可选：添加LLM-based相似度计算

### Phase 4: 测试和优化 (1-2小时)
1. 单元测试各个组件
2. 集成测试
3. 性能测试

## 预期整体耗时

约 8-10小时 = 1-1.5天开发时间

## 关键特性

✅ 轻量级（不引入新依赖）  
✅ 异步非阻塞（基于asyncio）  
✅ 可配置（interval、阈值等）  
✅ 可选的（可以关闭）  
✅ 向后兼容（无破坏性变更）

