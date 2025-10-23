import json
import time
import asyncio
from datetime import datetime
import time
import os
from listener import LSXT
from redis_client import subscriber, publisher

# Redis键名常量
TOKEN_STORAGE_KEY = "sxt:tokens"  # Hash存储 listener_id -> token
LISTENER_STATUS_KEY = "sxt:listener_status"  # Hash存储 listener_id -> status
CONSUMER_LOCK_KEY = "sxt:consumer_lock"  # 消费者锁
LISTENER_OWNER_KEY = "sxt:listener_owners"  # Hash存储 listener_id -> instance_id

# 队列名称
MESSAGE_QUEUE = "message_queue"
PROCESSED_SET = "processed_messages"

# 消费者：只监听新消息
class ListenerCommandConsumer:
    def __init__(self, app):
        self.running = False
        self.app = app
        # 本地缓存，用于快速访问
        self.tokens = {}  # {listener_id: token}
        # ✅ 生成唯一实例ID
        self.instance_id = f"instance-{os.getpid()}-{int(time.time() * 1000)}"
        self.lock_acquired = False
        print(f"🆔 实例ID: {self.instance_id}")

    async def _load_tokens_from_redis(self):
        """从Redis加载所有存储的tokens（异步版本）"""
        try:
            stored_tokens = await subscriber.hgetall(TOKEN_STORAGE_KEY)
            if stored_tokens:
                self.tokens = stored_tokens
                print(f"📥 从Redis恢复了 {len(self.tokens)} 个tokens")
                for listener_id in self.tokens.keys():
                    masked_token = f"{self.tokens[listener_id][:8]}***{self.tokens[listener_id][-4:]}"
                    print(f"   {listener_id}: {masked_token}")
            else:
                print("📥 Redis中没有存储的tokens")
        except Exception as e:
            print(f"❌ 从Redis加载tokens失败: {e}")
            self.tokens = {}

    async def _save_token_to_redis(self, listener_id, token):
        """保存token到Redis"""
        try:
            await subscriber.hset(TOKEN_STORAGE_KEY, listener_id, token)
            # 同时更新本地缓存
            self.tokens[listener_id] = token
            print(f"💾 已保存 {listener_id} 的token到Redis")
        except Exception as e:
            print(f"❌ 保存token到Redis失败: {e}")

    async def _remove_token_from_redis(self, listener_id):
        """从Redis删除token"""
        try:
            await subscriber.hdel(TOKEN_STORAGE_KEY, listener_id)
            # 同时更新本地缓存
            if listener_id in self.tokens:
                del self.tokens[listener_id]
            print(f"🗑️ 已从Redis删除 {listener_id} 的token")
        except Exception as e:
            print(f"❌ 从Redis删除token失败: {e}")

    async def _update_listener_status_async(self, listener_id, status, extra_info=None):
        """异步更新状态"""
        try:
            status_data = {
                "status": status,
                "timestamp": time.time(),
                "listener_id": listener_id
            }
            if extra_info:
                status_data.update(extra_info)

            await subscriber.hset(LISTENER_STATUS_KEY, listener_id, json.dumps(status_data))
            print(f"📊 已更新 {listener_id} 状态到Redis: {status}")
        except Exception as e:
            print(f"❌ 更新listener状态到Redis失败: {e}")
    
    async def _register_instance(self):
        """注册实例到Redis"""
        try:
            instance_key = f"sxt:instances:{self.instance_id}"
            await subscriber.setex(
                instance_key,
                60,  # 60秒过期
                json.dumps({
                    "started_at": time.time(),
                    "pid": os.getpid()
                })
            )
            print(f"✅ 实例 {self.instance_id} 已注册")
        except Exception as e:
            print(f"❌ 注册实例失败: {e}")
    
    async def _unregister_instance(self):
        """注销实例"""
        try:
            instance_key = f"sxt:instances:{self.instance_id}"
            await subscriber.delete(instance_key)
            
            # 释放所有该实例拥有的 listeners
            await self._release_all_listeners()
            
            print(f"✅ 实例 {self.instance_id} 已注销")
        except Exception as e:
            print(f"❌ 注销实例失败: {e}")
    
    async def _renew_lock(self):
        """定期续期锁"""
        try:
            while self.running and self.lock_acquired:
                await asyncio.sleep(15)  # 每15秒续期一次
                
                current_holder = await subscriber.get(CONSUMER_LOCK_KEY)
                if current_holder == self.instance_id:
                    await subscriber.expire(CONSUMER_LOCK_KEY, 30)
                    # print(f"🔄 锁已续期")
                else:
                    print(f"⚠️ 锁已被其他实例持有: {current_holder}")
                    self.lock_acquired = False
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ 续期锁失败: {e}")
    
    async def _takeover_listeners(self):
        """接管所有需要运行的 listeners"""
        try:
            print(f"🔄 实例 {self.instance_id} 开始接管 listeners...")
            
            # 获取所有 tokens
            all_tokens = await subscriber.hgetall(TOKEN_STORAGE_KEY)
            
            if not all_tokens:
                print("📭 没有需要接管的 listeners")
                return
            
            # 检查每个 listener 的所有者
            for listener_id, token in all_tokens.items():
                owner = await subscriber.hget(LISTENER_OWNER_KEY, listener_id)
                
                if owner and owner != self.instance_id:
                    # 检查原所有者是否还活着
                    instance_key = f"sxt:instances:{owner}"
                    instance_exists = await subscriber.exists(instance_key)
                    
                    if instance_exists:
                        print(f"⏭️ Listener {listener_id} 由活跃实例 {owner} 拥有，跳过")
                        continue
                    else:
                        print(f"🔄 Listener {listener_id} 的原所有者 {owner} 已失效，接管")
                
                # 接管 listener
                if listener_id not in self.app.SXTS:
                    print(f"🚀 接管 Listener {listener_id}...")
                    self.tokens[listener_id] = token
                    
                    try:
                        sxt = LSXT(
                            listener_id=listener_id,
                            cookies={"access-token-sxt.xiaohongshu.com": token}
                        )
                        sxt.run()
                        self.app.SXTS[listener_id] = sxt
                        
                        # 标记所有权
                        await subscriber.hset(LISTENER_OWNER_KEY, listener_id, self.instance_id)
                        await self._update_listener_status_async(
                            listener_id, 
                            "running", 
                            {"instance_id": self.instance_id, "takeover": True}
                        )
                        
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
                    
                    # 避免同时启动太多
                    await asyncio.sleep(1)
            
            print(f"🎉 接管完成，当前运行 {len(self.app.SXTS)} 个 listeners")
            
        except Exception as e:
            print(f"❌ 接管 listeners 失败: {e}")
            import traceback
            traceback.print_exc()
    
    async def _release_all_listeners(self):
        """释放所有该实例拥有的 listeners"""
        try:
            owners = await subscriber.hgetall(LISTENER_OWNER_KEY)
            released_count = 0
            
            for listener_id, owner in owners.items():
                if owner == self.instance_id:
                    await subscriber.hdel(LISTENER_OWNER_KEY, listener_id)
                    released_count += 1
            
            if released_count > 0:
                print(f"✅ 释放了 {released_count} 个 listeners 的所有权")
        except Exception as e:
            print(f"❌ 释放 listeners 失败: {e}")

    async def auto_recover_listeners(self):
        """自动恢复所有存储的listeners（异步版本）"""
        try:
            print("🔄 开始自动恢复listeners...")
            recovered_count = 0
            
            for listener_id, token in self.tokens.items():
                # 检查是否已经在运行
                if self.app.SXTS.get(listener_id) is not None:
                    print(f"⚠️ Listener {listener_id} 已在运行，跳过恢复")
                    continue
                
                # ✅ 检查是否被其他实例拥有
                owner = await subscriber.hget(LISTENER_OWNER_KEY, listener_id)
                if owner and owner != self.instance_id:
                    # 检查原所有者是否还活着
                    instance_key = f"sxt:instances:{owner}"
                    instance_exists = await subscriber.exists(instance_key)
                    if instance_exists:
                        print(f"⚠️ Listener {listener_id} 已被实例 {owner} 拥有，跳过恢复")
                        continue
                    else:
                        print(f"🔄 Listener {listener_id} 的原所有者 {owner} 已失效，接管")
                
                print(f"🚀 恢复 Listener {listener_id}...")
                try:
                    sxt = LSXT(
                        listener_id=listener_id,
                        cookies={"access-token-sxt.xiaohongshu.com": token}
                    )
                    sxt.run()
                    self.app.SXTS[listener_id] = sxt
                    
                    # ✅ 标记所有权
                    await subscriber.hset(LISTENER_OWNER_KEY, listener_id, self.instance_id)
                    
                    # 异步更新状态
                    await self._update_listener_status_async(listener_id, "running", {
                        "recovered": True,
                        "instance_id": self.instance_id
                    })
                    
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
                
                # 避免同时启动太多，间隔一下（使用异步sleep）
                await asyncio.sleep(1)
            
            print(f"🎉 自动恢复完成，成功恢复 {recovered_count} 个listeners")
            
        except Exception as e:
            print(f"❌ 自动恢复过程失败: {e}")

    async def start_listening(self):
        """实时监听并处理新消息（带分布式锁）"""
        self.running = True
        reconnect_attempts = 0
        max_reconnect_attempts = 20  # 增加重试次数，每次等待2秒，最多40秒
        pubsub = None
        
        try:
            while self.running and reconnect_attempts < max_reconnect_attempts:
                # ✅ 尝试获取消费者锁
                lock_acquired = await subscriber.set(
                    CONSUMER_LOCK_KEY,
                    self.instance_id,
                    nx=True,  # 只在键不存在时设置
                    ex=30     # 30秒过期
                )
                
                if not lock_acquired:
                    # 锁被其他实例持有
                    reconnect_attempts += 1
                    current_holder = await subscriber.get(CONSUMER_LOCK_KEY)
                    lock_ttl = await subscriber.ttl(CONSUMER_LOCK_KEY)
                    print(f"⏳ 实例 {self.instance_id} 等待监听权限，当前持有者: {current_holder}, 锁剩余TTL: {lock_ttl}秒 (尝试 {reconnect_attempts}/{max_reconnect_attempts})", flush=True)
                    
                    # 检查持有者是否还活着
                    if current_holder:
                        instance_key = f"sxt:instances:{current_holder}"
                        instance_exists = await subscriber.exists(instance_key)
                        if not instance_exists:
                            print(f"⚠️ 持有者 {current_holder} 已失效但锁未释放", flush=True)
                            # 如果锁 TTL 还很长，主动删除死锁
                            if lock_ttl > 10:
                                print(f"🔓 主动删除死锁 (TTL={lock_ttl}秒)", flush=True)
                                await subscriber.delete(CONSUMER_LOCK_KEY)
                                # 立即重试
                                await asyncio.sleep(1)
                                continue
                            else:
                                print(f"⏱️ 锁即将过期 ({lock_ttl}秒)，等待自动过期...", flush=True)
                    
                    # 等待2秒后重试（缩短等待时间）
                    print(f"💤 等待 2 秒后重试获取锁...", flush=True)
                    await asyncio.sleep(2)
                    continue
                
                self.lock_acquired = True
                print(f"✅ 实例 {self.instance_id} 获得监听权限", flush=True)
                
                # ✅ 注册实例（获得锁后才注册）
                await self._register_instance()
                
                # 启动锁续期任务
                renew_task = asyncio.create_task(self._renew_lock())
                
                try:
                    # ✅ 接管现有的 listeners
                    await self._takeover_listeners()
                    
                    # 创建异步pubsub
                    pubsub = subscriber.pubsub()
                    await pubsub.subscribe("listenerCommandChannel")
                    print(f"🔗 实例 {self.instance_id} 已订阅频道 (attempt {reconnect_attempts + 1})")
                    reconnect_attempts = 0  # 连接成功，重置计数器
                    
                    # 异步迭代消息
                    async for message in pubsub.listen():
                        # 检查是否应该停止
                        if not self.running:
                            print("🛑 收到停止信号，退出监听循环")
                            break
                        
                        # 处理订阅确认消息
                        if message['type'] == 'subscribe':
                            print(f"✅ 成功订阅频道: {message['channel']}")
                            continue
                        
                        # 处理实际消息
                        if message['type'] == 'message':
                            print(f"📨 收到消息: {message['data']}")
                            
                            try:
                                payload = json.loads(message["data"])
                                command = payload.get("command", "").lower()
                                listener_id = payload.get("listener_id", "").strip()
                                token = payload.get("sxtToken", "").strip()
                                
                                print(f"🎯 处理命令: {command}, listener_id: {listener_id}")
                                
                                # 验证必需字段
                                if not command or not listener_id:
                                    print(f"⚠️ 消息缺少必要字段: {payload}")
                                    continue
                                
                                # ✅ 异步处理命令
                                if command == "start":
                                    await self.start_listener(listener_id, token)
                                    
                                elif command == "stop":
                                    print(f"🛑 执行停止命令: {listener_id}")
                                    await self.stop_listener(listener_id)
                                    
                                elif command == "restart":
                                    print(f"🔄 执行重启命令: {listener_id}")
                                    reason = payload.get("reason")
                                    await self.restart_listener(listener_id, token, reason)
                                    
                                elif command == "status":
                                    await self.show_status()
                                    
                                elif command == "recover":
                                    print("🔄 执行自动恢复命令")
                                    await self.auto_recover_listeners()
                                    
                                elif command == "ping":
                                    print(f"🏓 Pong - 监听器活跃，当前时间: {datetime.now()}")
                                    
                                else:
                                    print(f"❓ 未知命令 '{command}'")
                                    print("   支持的命令: start, stop, restart, status, recover, ping")
                                    
                            except json.JSONDecodeError as e:
                                print(f"❌ 无效 JSON 消息: {message['data']}, 错误: {e}")
                            except Exception as e:
                                print(f"❌ 处理命令时发生错误: {e}")
                                import traceback
                                traceback.print_exc()
                    # 循环正常退出，清理订阅
                    print("🔌 正在取消订阅...")
                    await pubsub.unsubscribe("listenerCommandChannel")
                    
                except asyncio.CancelledError:
                    print("🛑 监听任务被取消")
                    break
                    
                except Exception as e:
                    reconnect_attempts += 1
                    print(f"❌ Redis监听错误 (尝试 {reconnect_attempts}/{max_reconnect_attempts}): {e}")
                    import traceback
                    traceback.print_exc()
                    
                    if reconnect_attempts < max_reconnect_attempts and self.running:
                        wait_time = min(2 ** reconnect_attempts, 30)  # 指数退避，最大30秒
                        print(f"🔄 {wait_time}秒后重试...")
                        await asyncio.sleep(wait_time)
                    else:
                        print("❌ 达到最大重连次数或收到停止信号")
                        break
                        
                finally:
                    # ✅ 取消锁续期任务
                    if 'renew_task' in locals():
                        renew_task.cancel()
                        try:
                            await renew_task
                        except asyncio.CancelledError:
                            pass
                    
                    # ✅ 释放锁
                    if self.lock_acquired:
                        current_holder = await subscriber.get(CONSUMER_LOCK_KEY)
                        if current_holder == self.instance_id:
                            await subscriber.delete(CONSUMER_LOCK_KEY)
                            print(f"🔓 实例 {self.instance_id} 释放监听锁")
                        self.lock_acquired = False
                    
                    # ✅ 确保资源被清理
                    if pubsub:
                        try:
                            await pubsub.unsubscribe("listenerCommandChannel")
                            await pubsub.close()
                            print("🔌 Pubsub连接已关闭")
                        except Exception as e:
                            print(f"⚠️ 清理pubsub时出错: {e}")
        
        finally:
            # ✅ 注销实例
            await self._unregister_instance()
            self.running = False
            print("🏁 监听器已完全停止")

    async def start_listener(self, listener_id: str, token: str):
        """启动监听器（异步版本，带所有权检查）"""
        # ✅ 检查是否已经被其他实例运行
        owner = await subscriber.hget(LISTENER_OWNER_KEY, listener_id)
        if owner and owner != self.instance_id:
            # 检查原所有者是否还活着
            instance_key = f"sxt:instances:{owner}"
            instance_exists = await subscriber.exists(instance_key)
            
            if instance_exists:
                print(f"⚠️ Listener {listener_id} 已被实例 {owner} 运行，拒绝启动")
                return
            else:
                print(f"🔄 Listener {listener_id} 的原所有者 {owner} 已失效，接管")
        
        if listener_id in self.app.SXTS:
            print(f"⚠️ Listener {listener_id} 已经在本实例运行")
            return

        print(f"🚀 实例 {self.instance_id} 启动 listener: {listener_id}")
        self.tokens[listener_id] = token
        
        # 异步保存token到Redis
        await self._save_token_to_redis(listener_id, token)
        
        sxt = LSXT(
            listener_id=listener_id,
            cookies={"access-token-sxt.xiaohongshu.com": token}
        )
        sxt.run()
        self.app.SXTS[listener_id] = sxt
        
        # ✅ 标记所有权
        await subscriber.hset(LISTENER_OWNER_KEY, listener_id, self.instance_id)
        
        # 异步更新Redis状态
        await self._update_listener_status_async(
            listener_id, 
            "running",
            {"instance_id": self.instance_id}
        )

    async def stop_listener(self, listener_id):
        """停止监听（异步版本，带所有权检查）"""
        try:
            # ✅ 检查所有权
            owner = await subscriber.hget(LISTENER_OWNER_KEY, listener_id)
            if owner and owner != self.instance_id:
                print(f"⚠️ Listener {listener_id} 由实例 {owner} 拥有，无权停止")
                return
            
            print(f"🔍 检查 Listener {listener_id} 是否存在...")
            if self.app.SXTS.get(listener_id) is None:
                print(f"⚠️ Listener {listener_id} 不存在或已经停止")
                
                # 清理所有权标记
                await subscriber.hdel(LISTENER_OWNER_KEY, listener_id)
                return
                
            print(f"🛑 实例 {self.instance_id} 正在停止 Listener {listener_id}...")
            self.app.SXTS[listener_id].stop_background_loop()
            del self.app.SXTS[listener_id]
            
            # 删除对应的token（从Redis和本地缓存）
            await self._remove_token_from_redis(listener_id)
            
            # ✅ 释放所有权
            await subscriber.hdel(LISTENER_OWNER_KEY, listener_id)
            
            # 更新状态
            await self._update_listener_status_async(listener_id, "stopped")
            
            print(f"✅ Listener {listener_id} 已成功停止")
            
            # 打印当前剩余的listeners
            remaining = list(self.app.SXTS.keys())
            print(f"📊 剩余运行中的listeners: {len(remaining)} 个")
            if remaining:
                print(f"   列表: {remaining}")
            
        except Exception as e:
            print(f"❌ 停止 Listener {listener_id} 失败: {e}")
            # 即使停止失败，也要尝试从字典中删除
            try:
                if listener_id in self.app.SXTS:
                    del self.app.SXTS[listener_id]
                await self._remove_token_from_redis(listener_id)
                await self._update_listener_status_async(listener_id, "failed", {"error": str(e)})
            except Exception as cleanup_error:
                print(f"❌ 清理失败: {cleanup_error}")

    async def show_status(self):
        """显示当前listeners状态（异步版本）"""
        try:
            listeners = list(self.app.SXTS.keys())
            count = len(listeners)
            
            # 异步获取Redis中存储的tokens数量
            redis_token_count = await subscriber.hlen(TOKEN_STORAGE_KEY)
            
            print(f"📊 当前状态: {count} 个listeners在运行，Redis中存储了 {redis_token_count} 个tokens")
            if listeners:
                for i, listener_id in enumerate(listeners, 1):
                    token_status = "✅" if listener_id in self.tokens else "❌"
                    # 只显示token的前8位和后4位，中间用*替代
                    token = self.tokens.get(listener_id, "")
                    masked_token = f"{token[:8]}***{token[-4:]}" if token else "无"
                    print(f"  {i}. {listener_id} {token_status} Token: {masked_token}")
            else:
                print("  没有运行中的listeners")
            
        except Exception as e:
            print(f"❌ 获取状态失败: {e}")

    async def get_token(self, listener_id):
        """获取指定listener的token（异步版本）"""
        # 先从本地缓存获取，如果没有再从Redis获取
        token = self.tokens.get(listener_id)
        if not token:
            try:
                redis_token = await subscriber.hget(TOKEN_STORAGE_KEY, listener_id)
                if redis_token:
                    token = redis_token
                    self.tokens[listener_id] = token  # 同步到本地缓存
            except Exception as e:
                print(f"❌ 从Redis获取token失败: {e}")
        return token

    async def list_tokens(self):
        """列出所有存储的tokens（异步版本，用于调试）"""
        try:
            # 从Redis获取最新的tokens
            redis_tokens = await subscriber.hgetall(TOKEN_STORAGE_KEY)
            redis_count = len(redis_tokens)
            
            print(f"💾 Redis中存储的tokens ({redis_count} 个):")
            for listener_id, token in redis_tokens.items():  # 直接使用，不需要decode
                masked_token = f"{token[:8]}***{token[-4:]}"
                running_status = "🟢" if listener_id in self.app.SXTS else "🔴"
                print(f"  {listener_id}: {masked_token} {running_status}")
            
            print(f"📝 本地缓存的tokens ({len(self.tokens)} 个):")
            for listener_id, token in self.tokens.items():
                masked_token = f"{token[:8]}***{token[-4:]}"
                print(f"  {listener_id}: {masked_token}")
                
        except Exception as e:
            print(f"❌ 列出tokens失败: {e}")

    async def stop_listening(self):
        """停止监听"""
        print(f"🛑 停止监听 (实例 {self.instance_id})")
        self.running = False
        
        # ✅ 停止所有运行中的 listeners
        listeners_to_stop = list(self.app.SXTS.keys())
        print(f"📊 当前运行中的 listeners 数量: {len(listeners_to_stop)}")
        
        if listeners_to_stop:
            print(f"🛑 停止 {len(listeners_to_stop)} 个运行中的 listeners: {listeners_to_stop}")
            for listener_id in listeners_to_stop:
                try:
                    print(f"  🛑 正在停止 Listener {listener_id}...")
                    if hasattr(self.app.SXTS[listener_id], 'stop_background_loop'):
                        self.app.SXTS[listener_id].stop_background_loop()
                        print(f"  ✅ Listener {listener_id} 后台线程已停止")
                    else:
                        print(f"  ⚠️ Listener {listener_id} 没有 stop_background_loop 方法")
                    del self.app.SXTS[listener_id]
                    print(f"  ✅ Listener {listener_id} 已从字典中移除")
                except Exception as e:
                    print(f"  ❌ 停止 Listener {listener_id} 时出错: {e}")
                    import traceback
                    traceback.print_exc()
            print(f"✅ 所有 listeners 已停止")
        else:
            print(f"📭 没有运行中的 listeners 需要停止")
        
        # ✅ 注销实例（会释放所有权）
        await self._unregister_instance()

    async def restart_listener(self, listener_id, token=None, reason=None):
        """重启监听器（异步版本）"""
        try:
            restart_reason = f" (原因: {reason})" if reason else ""
            print(f"🔄 开始重启 Listener {listener_id}{restart_reason}...")
            
            # 如果没有提供新token，使用存储的token
            if not token:
                # ✅ 使用异步方法获取token
                token = await self.get_token(listener_id)
                if not token:
                    print(f"❌ 重启失败: 没有找到存储的token")
                    return
                print(f"📝 使用存储的token重启")
        
            # 记录重启原因
            if reason:
                print(f"📋 重启原因: {reason}")
        
            # 先停止现有的listener
            if self.app.SXTS.get(listener_id) is not None:
                print(f"🛑 正在停止现有的 Listener {listener_id}...")
                try:
                    self.app.SXTS[listener_id].stop_background_loop()
                    del self.app.SXTS[listener_id]
                    print(f"✅ 现有 Listener 已停止")
                
                    # 心跳超时时等待一下
                    if reason and "心跳超时" in reason:
                        print("⏱️  等待3秒...")
                        await asyncio.sleep(3)  # ✅ 使用异步sleep
                    
                except Exception as e:
                    print(f"⚠️ 停止时出现警告: {e}")
                    if listener_id in self.app.SXTS:
                        del self.app.SXTS[listener_id]
        
            # ✅ 异步保存token
            if token:
                await self._save_token_to_redis(listener_id, token)
        
            # 启动新的listener
            print(f"🚀 正在启动新的 Listener {listener_id}...")
            sxt = LSXT(
                listener_id=listener_id,
                cookies={"access-token-sxt.xiaohongshu.com": token}
            )
            sxt.run()
            self.app.SXTS[listener_id] = sxt
        
            # ✅ 异步更新状态
            await self._update_listener_status_async(listener_id, "running", {
                "restarted": True, 
                "reason": reason
            })
        
            print(f"✅ Listener {listener_id} 重启成功")
        
            # 显示状态
            remaining = list(self.app.SXTS.keys())
            print(f"📊 当前运行: {len(remaining)} 个")
        
        except Exception as e:
            print(f"❌ 重启失败: {e}")
            import traceback
            traceback.print_exc()
        
            # 清理
            if listener_id in self.app.SXTS:
                del self.app.SXTS[listener_id]
            await self._update_listener_status_async(listener_id, "failed", {
                "error": str(e), 
                "operation": "restart"
            })