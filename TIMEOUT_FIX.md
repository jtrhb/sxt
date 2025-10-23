# 🔧 Railway 网络超时问题修复

## 问题描述

部署到 Railway 后，容器启动时出现两类超时错误：

### 1. SSL 握手超时
```
httpcore.ConnectTimeout: _ssl.c:999: The handshake operation timed out
```
发生在：`LSXT.__init__() → get_user_detail()` 调用时

### 2. 通用超时
```
❌ 恢复 Listener 2025041707592 失败: timed out
```
发生在：`auto_recover_listeners()` 尝试初始化 listener 时

### 根本原因
Railway 容器访问小红书 API 时网络不稳定，`httpx.get()` 没有设置超时参数，导致长时间阻塞直到默认超时（可能很长或无限）。

## 修复方案

### 1. 添加 HTTP 请求超时 ✅
**文件**: `engine/pysxt/core.py`

```python
# 在 SXT.__init__() 中添加超时配置
self.http_timeout = 15.0  # 15秒超时

# 修改 get_user_info()
def get_user_info(self) -> dict:
    url = self.base_url + "/im/user/info"
    response = httpx.get(
        url, 
        headers=self.headers, 
        cookies=self.cookies, 
        timeout=self.http_timeout  # ← 添加超时
    )
    return response.json()

# 修改 get_user_detail()
def get_user_detail(self, account_no: str) -> dict:
    url = self.base_url + "/pc/flow/user/detail"
    params = {
        "account_no": account_no,
        "contact_way": self.contact_way
    }
    response = httpx.get(
        url, 
        headers=self.headers, 
        cookies=self.cookies, 
        params=params,
        timeout=self.http_timeout  # ← 添加超时
    )
    return response.json()
```

### 2. 增强错误处理 - _takeover_listeners() ✅
**文件**: `message_queue.py`

```python
# 在接管 listener 时捕获初始化异常
try:
    sxt = LSXT(
        listener_id=listener_id,
        cookies={"access-token-sxt.xiaohongshu.com": token}
    )
    sxt.run()
    self.app.SXTS[listener_id] = sxt
    # ... 标记所有权 ...
    print(f"✅ 成功接管 Listener {listener_id}")
except Exception as e:
    print(f"❌ 接管 Listener {listener_id} 失败: {e}")
    # 清理失败的 listener
    if listener_id in self.app.SXTS:
        del self.app.SXTS[listener_id]
    if listener_id in self.tokens:
        del self.tokens[listener_id]
    await self._update_listener_status_async(
        listener_id,
        "failed",
        {"error": str(e), "operation": "takeover"}
    )
    # 继续处理其他 listeners，不要因为一个失败而中断
    continue
```

### 3. 增强错误处理 - auto_recover_listeners() ✅
**文件**: `message_queue.py`

```python
# 在恢复 listener 时捕获初始化异常
print(f"🚀 恢复 Listener {listener_id}...")
try:
    sxt = LSXT(
        listener_id=listener_id,
        cookies={"access-token-sxt.xiaohongshu.com": token}
    )
    sxt.run()
    self.app.SXTS[listener_id] = sxt
    # ... 标记所有权和状态 ...
    print(f"✅ 成功恢复 Listener {listener_id}")
    recovered_count += 1
except Exception as init_error:
    # LSXT 初始化失败（网络超时等）
    print(f"❌ 初始化 Listener {listener_id} 失败: {init_error}")
    # 清理失败的 listener
    if listener_id in self.app.SXTS:
        del self.app.SXTS[listener_id]
    await self._update_listener_status_async(
        listener_id,
        "failed",
        {"error": str(init_error), "error_type": "init_timeout"}
    )
    # 继续处理其他 listeners
```

## 修复效果

### 修复前 ❌
```
🚀 接管 Listener 2025041707592...
[等待 30+ 秒...]
❌ 接管 listeners 失败: _ssl.c:999: The handshake operation timed out
[整个接管流程失败，应用继续但 listener 未启动]
```

### 修复后 ✅
```
🚀 接管 Listener 2025041707592...
❌ 接管 Listener 2025041707592 失败: _ssl.c:999: The handshake operation timed out
[立即失败（15秒），更新状态为 failed，继续处理其他 listeners]
✅ 实例获得监听权限并订阅频道
[应用正常运行，失败的 listener 可以稍后手动重试]
```

## 预期行为

1. **快速失败**: HTTP 请求在15秒内超时，不会长时间阻塞
2. **隔离失败**: 单个 listener 初始化失败不影响其他 listeners
3. **状态跟踪**: 失败的 listener 状态被记录到 Redis，可以查询
4. **可恢复**: 可以通过 API 手动重试失败的 listener

## 测试验证

### 本地测试
```bash
# 模拟网络超时（可选）
# 在 core.py 中临时设置: self.http_timeout = 0.1

# 启动应用
python main.py

# 观察日志，应该看到：
# ✅ 快速超时（15秒内）
# ✅ 失败被捕获
# ✅ 应用继续运行
```

### Railway 部署测试
```bash
# 1. 提交修复
git add engine/pysxt/core.py message_queue.py
git commit -m "Fix HTTP timeout issues in Railway deployment

- Add 15s timeout to all httpx requests
- Enhance error handling in _takeover_listeners
- Enhance error handling in auto_recover_listeners
- Failed listeners don't block app startup
- Status properly tracked in Redis"

git push

# 2. 监控 Railway 日志
railway logs --follow

# 3. 预期日志
✅ 实例 instance-xxx 获得监听权限
✅ 实例 instance-xxx 已注册
🔄 实例 instance-xxx 开始接管 listeners...
🚀 接管 Listener xxx...

# 如果网络正常：
✅ 成功接管 Listener xxx

# 如果网络超时：
❌ 接管 Listener xxx 失败: timed out
📊 已更新 xxx 状态到Redis: failed

# 重点：无论成功还是失败，应用都能继续运行
🔗 实例 instance-xxx 已订阅频道
✅ 成功订阅频道: listenerCommandChannel
```

## 手动重试失败的 Listener

如果某个 listener 因网络超时失败，可以稍后手动重试：

```bash
# 方法1：重启命令
curl -X POST https://your-app.railway.app/admin/command \
  -H "Content-Type: application/json" \
  -d '{
    "command": "restart",
    "listener_id": "2025041707592"
  }'

# 方法2：恢复所有
curl -X POST https://your-app.railway.app/admin/command \
  -H "Content-Type: application/json" \
  -d '{"command": "recover"}'
```

## 进一步优化（可选）

### 1. 添加重试机制
```python
# 在 LSXT.__init__() 中添加重试
import time
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10)
)
def get_user_detail_with_retry(self, account_no: str):
    return self.get_user_detail(account_no)
```

### 2. 延迟初始化
```python
# 不在容器启动时立即恢复所有 listeners
# 而是逐个延迟恢复，减少网络压力
for listener_id, token in self.tokens.items():
    await asyncio.sleep(5)  # 每个 listener 间隔5秒
    # ... 恢复逻辑 ...
```

### 3. 健康检查端点
```python
@app.get("/admin/health/listeners")
async def check_listeners_health():
    """检查所有 listeners 的健康状态"""
    failed_listeners = []
    for listener_id in app.SXTS.keys():
        # 检查 listener 状态
        status = await subscriber.hget(LISTENER_STATUS_KEY, listener_id)
        if status and json.loads(status).get("status") == "failed":
            failed_listeners.append(listener_id)
    
    return {
        "total": len(app.SXTS),
        "failed": failed_listeners,
        "healthy": len(app.SXTS) - len(failed_listeners)
    }
```

## 总结

✅ **已修复**:
- HTTP 请求超时控制（15秒）
- Listener 初始化失败处理
- 隔离故障，不影响其他 listeners
- 状态跟踪和错误日志

✅ **效果**:
- 应用快速启动，不会因单个 listener 超时而卡住
- 失败的 listener 可以稍后重试
- 提升 Railway 部署的稳定性

🚀 **可以部署了！**
