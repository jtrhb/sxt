# Rolling Deployment 检查清单

## ✅ 已修复的问题

### 1. ✅ 分布式锁机制
- [x] 添加 `CONSUMER_LOCK_KEY` 确保只有一个实例监听 PubSub
- [x] 锁自动过期（30秒）+ 每15秒续期
- [x] 新实例等待获取锁（每5秒重试）

### 2. ✅ 实例生命周期管理
- [x] 生成唯一实例ID: `instance-{pid}-{timestamp}`
- [x] `_register_instance()`: 注册实例到 Redis（60秒TTL）
- [x] `_unregister_instance()`: 关闭时注销实例
- [x] 在 `start_listening()` 获取锁后调用 `_register_instance()`
- [x] 在 `stop_listening()` 中调用 `await _unregister_instance()`
- [x] `main.py` 中 `stop_listening()` 改为 `await`

### 3. ✅ Listener 所有权机制
- [x] 使用 `LISTENER_OWNER_KEY` Hash 存储所有权
- [x] `start_listener()` 启动前检查所有权
- [x] `stop_listener()` 停止前检查所有权
- [x] `auto_recover_listeners()` 恢复前检查所有权 ⭐ **刚修复**
- [x] 停止时释放所有权标记

### 4. ✅ 接管机制
- [x] `_takeover_listeners()`: 新实例接管孤儿 listeners
- [x] 检查原所有者实例是否存活
- [x] 只接管失效实例的 listeners
- [x] `_release_all_listeners()`: 关闭时释放所有所有权

### 5. ✅ 异步操作完整性
- [x] 所有 Redis 操作使用 `await`
- [x] 所有 `asyncio.sleep()` 使用异步版本
- [x] `stop_listening()` 改为异步方法

## ⚠️ 需要注意的问题

### 1. ⚠️ WebSocket 连接超时
**问题**: 30分钟连接延迟
**位置**: `listener.py` 的 `connect()` 方法
**建议**:
```python
import asyncio

# 在 connect() 方法中添加超时控制
async def connect(self):
    try:
        async with asyncio.timeout(30):  # 30秒超时
            await self._do_connect()
    except asyncio.TimeoutError:
        print(f"❌ WebSocket连接超时 ({self.listener_id})")
        raise
```

### 2. ⚠️ Redis 连接池
**当前状态**: 使用全局 `subscriber` 和 `publisher`
**潜在问题**: 
- Rolling deployment 时两个容器共享连接可能有竞争
- 建议验证 `redis.asyncio` 的连接池是线程/进程安全的

**建议检查**: `redis_client.py` 的连接池配置

### 3. ⚠️ app.SXTS 字典的线程安全
**当前状态**: 使用普通 dict 存储 listeners
**潜在问题**: 
- FastAPI 的并发请求可能同时修改 `app.SXTS`
- 虽然 Python dict 在 CPython 中是线程安全的，但复杂操作不是原子的

**建议**: 如果遇到问题，考虑使用锁保护：
```python
import asyncio

class ListenerCommandConsumer:
    def __init__(self, app):
        self.dict_lock = asyncio.Lock()
    
    async def start_listener(self, ...):
        async with self.dict_lock:
            self.app.SXTS[listener_id] = sxt
```

### 4. ⚠️ Railway 健康检查超时
**当前配置**: `healthcheckTimeout=300` (5分钟)
**问题**: 如果 WebSocket 连接真的需要30分钟，健康检查会失败

**建议**:
1. 修复 WebSocket 连接超时问题（优先）
2. 或者让健康检查不依赖 WebSocket 连接（已做到）
3. 确认 Railway 是否在等待 WebSocket 连接完成

### 5. ⚠️ 锁续期任务的异常处理
**当前状态**: `_renew_lock()` 中有 `try-except`
**建议验证**: 
- 如果续期失败，是否会影响正常运行？
- 是否需要在续期失败时主动释放锁并退出？

## 🧪 测试计划

### 本地测试
```bash
# 1. 启动第一个实例（模拟旧容器）
python main.py

# 2. 添加一些 listeners
curl -X POST http://localhost:3333/admin/command \
  -H "Content-Type: application/json" \
  -d '{"command": "start", "listener_id": "test1", "sxtToken": "token1"}'

# 3. 检查 Redis 状态
redis-cli
> GET sxt:consumer_lock
"instance-xxx-xxx"
> HGETALL sxt:listener_owners
1) "test1"
2) "instance-xxx-xxx"
> EXISTS sxt:instances:instance-xxx-xxx
(integer) 1

# 4. 启动第二个实例（模拟新容器）
# 修改端口避免冲突
PORT=3334 python main.py

# 预期: 第二个实例等待锁
# 日志: ⏳ 实例 instance-yyy-yyy 等待监听权限

# 5. 停止第一个实例 (Ctrl+C)
# 预期: 第一个实例释放锁，第二个实例获取锁并接管 listeners

# 6. 检查第二个实例日志
# 预期:
# ✅ 实例 instance-yyy-yyy 获得监听权限
# 🔄 Listener test1 的原所有者 instance-xxx-xxx 已失效，接管
# ✅ 成功接管 Listener test1
```

### Railway 部署测试
```bash
# 1. 提交代码
git add message_queue.py main.py
git commit -m "Add distributed lock and ownership for rolling deployment"
git push

# 2. 监控 Railway 日志
railway logs --follow

# 3. 预期日志流程
# 旧容器:
#   ✅ 实例 instance-old-111 获得监听权限
#   🔗 实例 instance-old-111 已订阅频道
#
# 新容器启动:
#   🆔 实例ID: instance-new-222
#   ⏳ 实例 instance-new-222 等待监听权限
#
# Railway 停止旧容器:
#   🛑 停止监听 (实例 instance-old-111)
#   🔓 实例 instance-old-111 释放监听锁
#   ✅ 实例 instance-old-111 已注销
#
# 新容器接管:
#   ✅ 实例 instance-new-222 获得监听权限
#   ✅ 实例 instance-new-222 已注册
#   🔄 实例 instance-new-222 开始接管 listeners...
#   ✅ 成功接管 Listener xxx

# 4. 验证没有重复消息
# 发送测试消息，确保每条消息只被处理一次
```

## 🚨 紧急情况处理

### 如果新旧容器都卡住
```bash
# 手动释放所有锁
redis-cli -h your-redis-host DEL sxt:consumer_lock
redis-cli -h your-redis-host DEL sxt:listener_owners
redis-cli -h your-redis-host --scan --pattern "sxt:instances:*" | xargs redis-cli DEL
```

### 如果 listeners 重复运行
```bash
# 检查所有权
redis-cli HGETALL sxt:listener_owners

# 检查活跃实例
redis-cli --scan --pattern "sxt:instances:*"

# 手动停止所有 listeners
curl -X POST https://your-app.railway.app/admin/command \
  -H "Content-Type: application/json" \
  -d '{"command": "stop", "listener_id": "问题listener的ID"}'
```

## 📊 监控指标

### 需要监控的 Redis 键
1. `sxt:consumer_lock` - 谁持有锁？
2. `sxt:listener_owners` - 每个 listener 的所有者
3. `sxt:instances:*` - 有多少个活跃实例？
4. `sxt:tokens` - 存储了多少 listeners？
5. `sxt:listener_status` - Listeners 的状态

### 添加监控端点（可选）
```python
@app.get("/admin/instances")
async def get_instances():
    """查看所有活跃实例"""
    pattern = "sxt:instances:*"
    instances = []
    async for key in subscriber.scan_iter(match=pattern):
        instance_id = key.replace("sxt:instances:", "")
        ttl = await subscriber.ttl(key)
        instances.append({"id": instance_id, "ttl": ttl})
    
    return {
        "count": len(instances),
        "instances": instances,
        "lock_holder": await subscriber.get(CONSUMER_LOCK_KEY)
    }

@app.get("/admin/owners")
async def get_owners():
    """查看所有 listener 的所有权"""
    owners = await subscriber.hgetall(LISTENER_OWNER_KEY)
    
    # 检查每个所有者是否还活着
    result = {}
    for listener_id, owner in owners.items():
        instance_key = f"sxt:instances:{owner}"
        is_alive = await subscriber.exists(instance_key)
        result[listener_id] = {
            "owner": owner,
            "owner_alive": bool(is_alive)
        }
    
    return result
```

## 🎯 下一步行动

1. **立即**: 检查 `listener.py` 的 WebSocket 超时问题
2. **部署前**: 本地测试双实例切换
3. **部署后**: 密切监控 Railway 日志
4. **持续**: 添加监控端点，实时查看系统状态

## ✅ 部署就绪条件

- [x] 所有语法错误已修复
- [x] 分布式锁机制完整
- [x] 实例生命周期管理完整
- [x] 所有权检查完整
- [x] 异步操作正确
- [x] 测试计划明确
- [ ] 本地双实例测试通过（建议做）
- [ ] WebSocket 超时问题修复（建议做）

**当前状态**: ✅ 代码就绪，可以部署到 Railway 测试
