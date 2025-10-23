# Rolling Deployment 测试方案

## 修改内容总结

### 1. 分布式锁机制
- ✅ 使用 `sxt:consumer_lock` 锁确保只有一个实例监听 Redis PubSub
- ✅ 锁每30秒过期，每15秒续期
- ✅ 新实例启动时会等待获取锁

### 2. 实例注册机制
- ✅ 每个实例有唯一ID: `instance-{pid}-{timestamp}`
- ✅ 实例信息存储在 `sxt:instances:{instance_id}`，60秒过期
- ✅ 实例关闭时自动注销

### 3. Listener 所有权机制
- ✅ 使用 `sxt:listener_owners` Hash 存储 listener_id → instance_id 映射
- ✅ 启动 listener 时标记所有权
- ✅ 停止 listener 时释放所有权
- ✅ 只有所有者可以停止自己的 listener

### 4. 接管机制
- ✅ 新实例获得锁后，检查所有 listeners 的所有权
- ✅ 如果原所有者已失效（实例ID不存在），新实例接管
- ✅ 避免重复启动相同的 listener

## Rolling Deployment 流程

### 阶段1：旧容器运行中
````
旧容器 (instance-old):
- ✅ 持有 consumer_lock
- ✅ 监听 listenerCommandChannel
- ✅ 运行 listener1, listener2, listener3
- ✅ listener_owners: {listener1: instance-old, ...}
````

### 阶段2：新容器启动
````
新容器 (instance-new) 启动:
1. 生成实例ID: instance-new
2. 注册实例: sxt:instances:instance-new
3. 尝试获取 consumer_lock
   → 失败（被 instance-old 持有）
4. 每5秒重试获取锁
   ⏳ 等待中...
````

### 阶段3：新容器健康检查通过
````
Railway 健康检查:
- GET / → 200 OK ✅
- 新容器标记为 healthy
- Railway 准备切换流量
````

### 阶段4：Railway 停止旧容器
````
旧容器收到 SIGTERM:
1. stop_listening() 被调用
2. self.running = False
3. PubSub 循环退出
4. finally 块执行:
   - 释放 consumer_lock ✅
   - 关闭 PubSub 连接
   - 注销实例: 删除 sxt:instances:instance-old
   - 释放所有 listeners 的所有权
5. 容器关闭
````

### 阶段5：新容器接管
````
新容器 (instance-new):
1. 检测到 consumer_lock 可用
2. 获取锁成功 ✅
3. 启动锁续期任务
4. 调用 _takeover_listeners():
   - 读取 sxt:tokens 中的所有 listeners
   - 检查每个 listener 的所有者
   - listener1 的所有者是 instance-old
   - 检查 sxt:instances:instance-old → 不存在 ✅
   - 接管 listener1，启动 WebSocket 连接
   - 标记所有权: listener_owners[listener1] = instance-new
   - 重复 listener2, listener3...
5. 订阅 listenerCommandChannel
6. 开始正常监听

✅ 平滑过渡完成！
````

## 避免的冲突

### 1. ✅ Redis PubSub 重复订阅
**问题**：新旧容器同时订阅，命令被执行两次
**解决**：分布式锁确保只有一个实例持有监听权限

### 2. ✅ Listener 重复启动
**问题**：新旧容器同时运行相同的 listener_id
**解决**：所有权机制，启动前检查所有者是否存活

### 3. ✅ WebSocket 连接冲突
**问题**：两个容器同时连接小红书
**解决**：接管机制确保旧容器关闭后才启动新连接

### 4. ✅ Redis 状态竞争
**问题**：新旧容器同时写入状态
**解决**：所有权标记，只有所有者可以修改状态

## 兼容性保证

### 对老代码的兼容
1. **旧容器关闭时**：
   - 即使没有 `_unregister_instance()`，60秒后实例记录自动过期
   - 新容器会在60秒后接管

2. **旧容器持有的 listeners**：
   - 新容器检测到所有者实例不存在 → 自动接管
   - 不需要手动干预

3. **逐步升级**：
   - 第一次部署：新代码容器启动，旧代码容器关闭
   - 新容器等待60秒后接管（因为旧容器没有注销实例）
   - 第二次部署：新→新，平滑过渡（因为都有注销逻辑）

## 测试步骤

### 1. 部署新代码
````bash
git add message_queue.py
git commit -m "Add distributed lock and ownership mechanism for rolling deployment"
git push
````

### 2. 监控日志
````bash
railway logs --follow
````

### 3. 预期日志输出

**新容器启动**：
````
🆔 实例ID: instance-123456-789
✅ 实例 instance-123456-789 已注册
⏳ 实例 instance-123456-789 等待监听权限，当前持有者: instance-old-111
⏳ 实例 instance-123456-789 等待监听权限，当前持有者: instance-old-111
...（每5秒一次）
````

**旧容器关闭**：
````
🛑 停止监听 (实例 instance-old-111)
🔓 实例 instance-old-111 释放监听锁
✅ 释放了 3 个 listeners 的所有权
✅ 实例 instance-old-111 已注销
🏁 监听器已完全停止
````

**新容器接管**：
````
✅ 实例 instance-123456-789 获得监听权限
🔄 实例 instance-123456-789 开始接管 listeners...
🔄 Listener listener1 的原所有者 instance-old-111 已失效，接管
🚀 接管 Listener listener1...
[Connecting] 🔐 使用代理连接...
[Connected] ✅ WebSocket连接已建立
✅ 成功接管 Listener listener1
🔄 Listener listener2 的原所有者 instance-old-111 已失效，接管
...
🎉 接管完成，当前运行 3 个 listeners
🔗 实例 instance-123456-789 已订阅频道
````

## 回滚方案

如果新代码有问题，回滚到旧代码：

````bash
# 1. 回滚 Git
git revert HEAD
git push

# 2. Railway 会部署旧代码
# 3. 旧代码容器启动，但没有分布式锁逻辑
# 4. 新代码容器关闭时释放锁
# 5. 旧代码容器直接监听（没有锁检查）
# 6. 正常运行 ✅
````

## 监控指标

### 部署成功指标：
- ✅ 新容器获得 consumer_lock
- ✅ 所有 listeners 被接管
- ✅ WebSocket 连接建立成功
- ✅ 没有重复消息

### 部署失败指标：
- ❌ 新容器一直在等待锁（超过5分钟）
- ❌ Listeners 接管失败
- ❌ WebSocket 连接超时

## 紧急处理

### 如果新旧容器都卡住：
````bash
# 手动释放锁
redis-cli -h your-redis DEL sxt:consumer_lock

# 清理所有实例记录
redis-cli -h your-redis --scan --pattern "sxt:instances:*" | xargs redis-cli DEL

# 清理所有者记录
redis-cli -h your-redis DEL sxt:listener_owners
````

### 如果需要强制重启所有 listeners：
````bash
# 通过 API 发送 recover 命令
curl -X POST https://your-app.railway.app/admin/command \
  -H "Content-Type: application/json" \
  -d '{"command": "recover"}'
````
