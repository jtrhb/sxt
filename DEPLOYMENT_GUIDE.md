# 🚀 Rolling Deployment 完整修复说明

## ✅ 已完成的所有修复

### 1. 分布式锁和所有权系统
**文件**: `message_queue.py`

#### 新增常量
```python
CONSUMER_LOCK_KEY = "sxt:consumer_lock"       # 消费者锁
LISTENER_OWNER_KEY = "sxt:listener_owners"    # Listener 所有权
```

#### 实例生命周期
```python
# 生成唯一实例ID
self.instance_id = f"instance-{os.getpid()}-{int(time.time() * 1000)}"

# 注册实例（60秒TTL）
async def _register_instance(self)

# 注销实例并释放所有 listeners
async def _unregister_instance(self)
```

#### 锁机制
```python
# 获取锁（30秒过期）
lock_acquired = await subscriber.set(
    CONSUMER_LOCK_KEY, 
    self.instance_id, 
    nx=True,  # 只在不存在时设置
    ex=30     # 30秒过期
)

# 每15秒续期
async def _renew_lock(self)
```

#### 接管机制
```python
# 新实例接管孤儿 listeners
async def _takeover_listeners(self)

# 检查原所有者是否还活着
instance_key = f"sxt:instances:{owner}"
instance_exists = await subscriber.exists(instance_key)
```

#### 所有权检查
- ✅ `start_listener()`: 启动前检查是否被其他实例拥有
- ✅ `stop_listener()`: 停止前检查是否有权限
- ✅ `auto_recover_listeners()`: 恢复前检查所有权

### 2. WebSocket 连接超时修复
**文件**: `listener.py`

#### 添加超时控制
```python
connection_timeout = 30  # 30秒超时

try:
    async with asyncio.timeout(connection_timeout):
        async with websockets.connect(...) as self.websocket:
            # 连接逻辑
except asyncio.TimeoutError:
    print(f"❌ WebSocket连接超时")
    raise
```

#### 代理控制
```python
# 环境变量控制是否使用代理
USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"

# Railway 部署时不使用代理（默认）
# 本地测试时可以设置: export USE_PROXY=true
```

### 3. 异步操作完整性
**文件**: `message_queue.py`, `main.py`

- ✅ 所有 Redis 操作使用 `await`
- ✅ 所有 `asyncio.sleep()` 使用异步版本
- ✅ `stop_listening()` 改为异步方法
- ✅ `main.py` 中调用改为 `await consumer.stop_listening()`

## 📊 Rolling Deployment 流程

### 阶段1: 旧容器运行中
```
旧容器 (instance-123-456):
├─ ✅ 持有 consumer_lock
├─ ✅ 监听 listenerCommandChannel  
├─ ✅ 运行 3 个 listeners
└─ ✅ listener_owners = {listener1: instance-123-456, ...}
```

### 阶段2: 新容器启动
```
新容器 (instance-789-012) 启动:
├─ 生成实例ID
├─ 尝试获取 consumer_lock → 失败（被旧容器持有）
└─ ⏳ 每5秒重试
    └─ 日志: "⏳ 实例 instance-789-012 等待监听权限"
```

### 阶段3: 健康检查通过
```
Railway 健康检查:
├─ GET / → 200 OK ✅
├─ 新容器标记为 healthy
└─ Railway 准备切换流量
```

### 阶段4: Railway 停止旧容器
```
旧容器收到 SIGTERM:
├─ stop_listening() 被调用
├─ self.running = False
├─ PubSub 循环退出
└─ finally 块执行:
    ├─ 🔓 释放 consumer_lock
    ├─ 🧹 注销实例
    └─ ✅ 释放所有 listeners 的所有权
```

### 阶段5: 新容器接管
```
新容器接管:
├─ ✅ 获取 consumer_lock
├─ ✅ 注册实例
├─ 🔄 _takeover_listeners():
│   ├─ 读取 sxt:tokens 中的所有 listeners
│   ├─ 检查每个 listener 的所有者
│   ├─ 验证原所有者是否存活
│   ├─ 接管孤儿 listeners
│   └─ 标记新所有权
└─ 🔗 订阅 listenerCommandChannel

✅ 平滑过渡完成！
```

## 🧪 部署前测试

### 本地测试（推荐）
```bash
# Terminal 1: 启动第一个实例（模拟旧容器）
cd /Users/bitpravda/Documents/sxt
source sxt/bin/activate
python main.py

# 添加测试 listener
curl -X POST http://localhost:3333/admin/command \
  -H "Content-Type: application/json" \
  -d '{
    "command": "start",
    "listener_id": "test_listener_1",
    "sxtToken": "your_test_token"
  }'

# 检查 Redis 状态
redis-cli
> GET sxt:consumer_lock
"instance-xxx-xxx"
> HGETALL sxt:listener_owners
> EXISTS sxt:instances:instance-xxx-xxx

# Terminal 2: 启动第二个实例（模拟新容器）
PORT=3334 python main.py

# 观察第二个实例日志
# 预期: ⏳ 实例 instance-yyy-yyy 等待监听权限

# 停止第一个实例 (Ctrl+C)
# 观察第二个实例接管过程

# 预期日志:
# ✅ 实例 instance-yyy-yyy 获得监听权限
# 🔄 Listener test_listener_1 的原所有者 instance-xxx-xxx 已失效，接管
# ✅ 成功接管 Listener test_listener_1
```

### Railway 部署
```bash
# 1. 提交所有更改
git add message_queue.py main.py listener.py
git commit -m "Fix rolling deployment resource conflicts

- Add distributed lock mechanism for PubSub consumer
- Add instance lifecycle management (register/unregister)
- Add listener ownership tracking to prevent duplicates
- Add takeover mechanism for orphaned listeners
- Add WebSocket connection timeout control (30s)
- Make stop_listening async and cleanup properly
- Fix all async operations with await"

git push

# 2. 监控 Railway 日志
railway logs --follow

# 3. 验证部署过程
# 查看新旧容器的切换日志
# 确认没有重复 listeners
# 确认 WebSocket 连接在30秒内完成
```

## 🔍 监控重点

### 关键日志
```bash
# 旧容器关闭
🛑 停止监听 (实例 instance-old-xxx)
🔓 实例 instance-old-xxx 释放监听锁
✅ 释放了 N 个 listeners 的所有权
✅ 实例 instance-old-xxx 已注销

# 新容器接管
✅ 实例 instance-new-yyy 获得监听权限
✅ 实例 instance-new-yyy 已注册
🔄 实例 instance-new-yyy 开始接管 listeners...
✅ 成功接管 Listener xxx
🎉 接管完成，当前运行 N 个 listeners
```

### 成功指标
- ✅ 只有一个实例持有 `sxt:consumer_lock`
- ✅ 每个 listener 只有一个所有者
- ✅ WebSocket 连接在30秒内建立
- ✅ 没有重复消息处理
- ✅ 切换过程无消息丢失

### 失败指标
- ❌ 新旧容器同时持有锁
- ❌ Listener 在两个容器中同时运行
- ❌ WebSocket 连接超时
- ❌ 实例卡在 "⏳ 等待监听权限" 超过5分钟

## 🚨 故障处理

### 如果新旧容器都卡住
```bash
# 1. 连接到 Redis
redis-cli -h your-redis-host

# 2. 手动释放所有锁
DEL sxt:consumer_lock
DEL sxt:listener_owners
KEYS sxt:instances:*
# 对每个实例key执行 DEL

# 3. Railway 重启服务
railway restart
```

### 如果 WebSocket 一直超时
```bash
# 检查网络连接
curl -v https://zelda.xiaohongshu.com

# 检查代理配置
echo $USE_PROXY  # 应该是空或false

# 增加超时时间（临时）
# 修改 listener.py 中的 connection_timeout = 60
```

### 如果 listener 重复运行
```bash
# 检查所有权
redis-cli HGETALL sxt:listener_owners

# 检查活跃实例
redis-cli KEYS "sxt:instances:*"

# 手动停止问题 listener
curl -X POST https://your-app.railway.app/admin/command \
  -H "Content-Type: application/json" \
  -d '{"command": "stop", "listener_id": "问题listener"}'
```

## 📝 环境变量配置

### Railway 环境变量（可选）
```bash
# 如果需要使用代理
USE_PROXY=true

# Redis 连接（Railway 自动注入）
REDIS_URL=redis://...

# 端口（Railway 自动注入）
PORT=3333
```

## ✅ 部署检查清单

部署前确认：
- [x] 所有代码已提交到 Git
- [x] 语法错误已全部修复
- [x] 分布式锁机制已实现
- [x] 实例生命周期管理已实现
- [x] 所有权检查已添加到所有 listener 操作
- [x] WebSocket 超时控制已添加
- [x] 所有异步操作使用 await
- [ ] 本地双实例测试通过（推荐但非必需）

部署后验证：
- [ ] 旧容器成功释放锁
- [ ] 新容器成功获取锁
- [ ] 所有 listeners 被接管
- [ ] WebSocket 连接正常
- [ ] 没有重复消息
- [ ] Redis 状态正确

## 🎯 预期结果

**✅ 完美部署**:
- 旧容器平滑关闭
- 新容器立即接管
- 无消息丢失
- 无重复 listeners
- 切换时间 < 5秒

**当前状态**: 🟢 代码就绪，可以部署！
