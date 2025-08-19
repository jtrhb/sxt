import json
import time
import asyncio
from datetime import datetime
import time
from listener import LSXT
from redis_client import subscriber, publisher

# Redis键名常量
TOKEN_STORAGE_KEY = "sxt:tokens"  # Hash存储 listener_id -> token
LISTENER_STATUS_KEY = "sxt:listener_status"  # Hash存储 listener_id -> status

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
        
        # 启动时从Redis恢复tokens
        self._load_tokens_from_redis()

    def _load_tokens_from_redis(self):
        """从Redis加载所有存储的tokens"""
        try:
            stored_tokens = subscriber.hgetall(TOKEN_STORAGE_KEY)
            if stored_tokens:
                # decode_responses=True 时，直接就是字符串，不需要decode
                self.tokens = stored_tokens  # 直接使用，不需要decode
                print(f"📥 从Redis恢复了 {len(self.tokens)} 个tokens")
                for listener_id in self.tokens.keys():
                    masked_token = f"{self.tokens[listener_id][:8]}***{self.tokens[listener_id][-4:]}"
                    print(f"   {listener_id}: {masked_token}")
            else:
                print("📥 Redis中没有存储的tokens")
        except Exception as e:
            print(f"❌ 从Redis加载tokens失败: {e}")
            self.tokens = {}

    def _save_token_to_redis(self, listener_id, token):
        """保存token到Redis"""
        try:
            subscriber.hset(TOKEN_STORAGE_KEY, listener_id, token)
            # 同时更新本地缓存
            self.tokens[listener_id] = token
            print(f"💾 已保存 {listener_id} 的token到Redis")
        except Exception as e:
            print(f"❌ 保存token到Redis失败: {e}")

    def _remove_token_from_redis(self, listener_id):
        """从Redis删除token"""
        try:
            subscriber.hdel(TOKEN_STORAGE_KEY, listener_id)
            # 同时更新本地缓存
            if listener_id in self.tokens:
                del self.tokens[listener_id]
            print(f"🗑️ 已从Redis删除 {listener_id} 的token")
        except Exception as e:
            print(f"❌ 从Redis删除token失败: {e}")

    def _update_listener_status(self, listener_id, status, extra_info=None):
        """更新listener状态到Redis"""
        try:
            status_data = {
                "status": status,
                "timestamp": time.time(),
                "listener_id": listener_id
            }
            if extra_info:
                status_data.update(extra_info)
            
            subscriber.hset(LISTENER_STATUS_KEY, listener_id, json.dumps(status_data))
            print(f"📊 已更新 {listener_id} 状态到Redis: {status}")
        except Exception as e:
            print(f"❌ 更新listener状态到Redis失败: {e}")

    def auto_recover_listeners(self):
        """自动恢复所有存储的listeners"""
        try:
            print("🔄 开始自动恢复listeners...")
            recovered_count = 0
            
            for listener_id, token in self.tokens.items():
                try:
                    # 检查是否已经在运行
                    if self.app.SXTS.get(listener_id) is not None:
                        print(f"⚠️ Listener {listener_id} 已在运行，跳过恢复")
                        continue
                    
                    print(f"🚀 恢复 Listener {listener_id}...")
                    sxt = LSXT(
                        listener_id=listener_id,
                        cookies={"access-token-sxt.xiaohongshu.com": token}
                    )
                    sxt.run()
                    self.app.SXTS[listener_id] = sxt
                    
                    # 更新状态
                    self._update_listener_status(listener_id, "running", {"recovered": True})
                    
                    print(f"✅ 成功恢复 Listener {listener_id}")
                    recovered_count += 1
                    
                    # 避免同时启动太多，间隔一下
                    time.sleep(1)
                    
                except Exception as e:
                    print(f"❌ 恢复 Listener {listener_id} 失败: {e}")
                    self._update_listener_status(listener_id, "failed", {"error": str(e)})
            
            print(f"🎉 自动恢复完成，成功恢复 {recovered_count} 个listeners")
            
        except Exception as e:
            print(f"❌ 自动恢复过程失败: {e}")

    async def start_listening(self):
        """实时监听并处理新消息"""
        self.running = True
        pubsub = None
        last_heartbeat = time.time()
        reconnect_attempts = 0
        max_reconnect_attempts = 5
        
        while self.running and reconnect_attempts < max_reconnect_attempts:
            try:
                pubsub = subscriber.pubsub()
                pubsub.subscribe("listenerCommandChannel")
                reconnect_attempts = 0  # 重置重连计数
                loop = asyncio.get_running_loop()
                print(f"🔗 Consumer subscribed to channel, waiting for new listener commands... (attempt {reconnect_attempts + 1})")

                def sync_loop():
                    nonlocal last_heartbeat
                    try:
                        for message in pubsub.listen():
                            if not self.running:  # 检查是否应该停止
                                print("🛑 收到停止信号，退出监听循环")
                                break
                            
                            # 更新心跳时间
                            current_time = time.time()
                            
                            if message['type'] == 'subscribe':
                                print(f"✅ 成功订阅频道: {message['channel']}")
                                last_heartbeat = current_time
                                continue
                                
                            if message['type'] == 'message':
                                last_heartbeat = current_time
                                print(f"📨 收到消息: {message['data']}")
                                
                                try:
                                    payload = json.loads(message["data"])
                                except json.JSONDecodeError as e:
                                    print(f"❌ 无效 JSON 消息: {message['data']}, 错误: {e}")
                                    continue
                                    
                                command = payload.get("command", "").lower()
                                listener_id = payload.get("listener_id", "").strip()
                                token = payload.get("sxtToken", "").strip()
                                
                                print(f"🎯 处理命令: {command}, listener_id: {listener_id}")
                                
                                if not command or not listener_id:
                                    print(f"⚠️ 消息缺少必要的 'command' 或 'listener_id' 键: {payload}")
                                    continue

                                try:
                                    if command == "start":
                                        self.start_listener(listener_id, token)
                                    elif command == "stop":
                                        print(f"🛑 执行停止命令: {listener_id}")
                                        self.stop_listener(listener_id)
                                    elif command == "restart":
                                        print(f"🔄 执行重启命令: {listener_id}")
                                        # 检查是否有重启原因
                                        reason = payload.get("reason")
                                        self.restart_listener(listener_id, token, reason)
                                    elif command == "status":
                                        self.show_status()
                                    elif command == "recover":
                                        print("🔄 执行自动恢复命令")
                                        self.auto_recover_listeners()
                                    elif command == "ping":
                                        print(f"🏓 Pong - 监听器活跃，当前时间: {datetime.now()}")
                                    else:
                                        print(f"❓ 未知命令 '{command}'。支持的命令: start, stop, restart, status, recover, ping")
                                except Exception as e:
                                    print(f"❌ 处理命令 '{command}' 时发生错误: {e}")
                            
                            # 检查心跳超时（5分钟无消息）
                            if current_time - last_heartbeat > 300:  
                                print("💔 心跳超时，可能连接已断开")
                                break
                                    
                    except Exception as e:
                        print(f"❌ Redis监听过程中发生错误: {e}")
                        raise e
                    finally:
                        if pubsub:
                            try:
                                pubsub.close()
                                print("🔌 Pubsub连接已关闭")
                            except Exception as e:
                                print(f"❌ 关闭pubsub连接时发生错误: {e}")

                await loop.run_in_executor(None, sync_loop)
                
            except Exception as e:
                reconnect_attempts += 1
                print(f"❌ 启动监听器时发生错误 (尝试 {reconnect_attempts}/{max_reconnect_attempts}): {e}")
                
                if reconnect_attempts < max_reconnect_attempts and self.running:
                    wait_time = min(2 ** reconnect_attempts, 30)  # 指数退避，最大30秒
                    print(f"🔄 {wait_time}秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    print("❌ 达到最大重连次数或收到停止信号，停止尝试")
                    break
            finally:
                # 确保资源被清理
                if pubsub:
                    try:
                        pubsub.close()
                    except Exception as e:
                        print(f"❌ 最终清理pubsub连接时发生错误: {e}")
                        
        print("🏁 监听器已停止")

    def start_listener(self, listener_id, token):
        """初始化SXT类"""
        try:
            if self.app.SXTS.get(listener_id) is not None:
                print(f"Listener {listener_id} 已经在运行中")
                return
                
            if not token:
                print(f"启动 Listener {listener_id} 失败: 缺少token")
                return
            
            # 保存token到Redis
            self._save_token_to_redis(listener_id, token)
                
            sxt = LSXT(
                listener_id=listener_id, 
                cookies={"access-token-sxt.xiaohongshu.com": token}
            )
            sxt.run()
            self.app.SXTS[listener_id] = sxt
            
            # 更新状态
            self._update_listener_status(listener_id, "running")
            
            print(f"✅ Listener {listener_id} 已成功启动")
            
        except Exception as e:
            print(f"❌ 启动 Listener {listener_id} 失败: {e}")
            # 如果启动失败，清理存储的token
            self._remove_token_from_redis(listener_id)
            self._update_listener_status(listener_id, "failed", {"error": str(e)})

    def stop_listener(self, listener_id):
        """停止监听"""
        try:
            print(f"🔍 检查 Listener {listener_id} 是否存在...")
            if self.app.SXTS.get(listener_id) is None:
                print(f"⚠️ Listener {listener_id} 不存在或已经停止")
                return
                
            print(f"🛑 正在停止 Listener {listener_id}...")
            self.app.SXTS[listener_id].stop_background_loop()
            del self.app.SXTS[listener_id]
            
            # 删除对应的token（从Redis和本地缓存）
            self._remove_token_from_redis(listener_id)
            
            # 更新状态
            self._update_listener_status(listener_id, "stopped")
            
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
                self._remove_token_from_redis(listener_id)
                self._update_listener_status(listener_id, "failed", {"error": str(e)})
            except Exception as cleanup_error:
                print(f"❌ 清理失败: {cleanup_error}")

    def show_status(self):
        """显示当前listeners状态"""
        try:
            listeners = list(self.app.SXTS.keys())
            count = len(listeners)
            print(f"📊 当前状态: {count} 个listeners在运行")
            if listeners:
                for i, listener_id in enumerate(listeners, 1):
                    token_status = "✅" if listener_id in self.tokens else "❌"
                    # 只显示token的前8位和后4位，中间用*替代
                    token = self.tokens.get(listener_id, "")
                    masked_token = f"{token[:8]}***{token[-4:]}" if token else "无"
                    print(f"  {i}. {listener_id} {token_status} Token: {masked_token}")
            else:
                print("  没有运行中的listeners")
            
            # 显示Redis中存储的tokens
            try:
                redis_token_count = subscriber.hlen(TOKEN_STORAGE_KEY)
                print(f"💾 Redis中存储的tokens: {redis_token_count} 个")
            except Exception as e:
                print(f"❌ 获取Redis token数量失败: {e}")
            
        except Exception as e:
            print(f"❌ 获取状态失败: {e}")

    def get_token(self, listener_id):
        """获取指定listener的token"""
        # 先从本地缓存获取，如果没有再从Redis获取
        token = self.tokens.get(listener_id)
        if not token:
            try:
                redis_token = subscriber.hget(TOKEN_STORAGE_KEY, listener_id)
                if redis_token:
                    token = redis_token  # 不需要decode
                    self.tokens[listener_id] = token  # 同步到本地缓存
            except Exception as e:
                print(f"❌ 从Redis获取token失败: {e}")
        return token

    def list_tokens(self):
        """列出所有存储的tokens（用于调试）"""
        try:
            # 从Redis获取最新的tokens
            redis_tokens = subscriber.hgetall(TOKEN_STORAGE_KEY)
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

    def stop_listening(self):
        """停止监听"""
        self.running = False
        print("Consumer stopped listening")

    def restart_listener(self, listener_id, token=None, reason=None):
        """重启监听器"""
        try:
            restart_reason = f" (原因: {reason})" if reason else ""
            print(f"🔄 开始重启 Listener {listener_id}{restart_reason}...")
            
            # 如果没有提供新token，使用存储的token
            if not token:
                token = self.get_token(listener_id)  # 这个方法会从Redis获取
                if not token:
                    print(f"❌ 重启 Listener {listener_id} 失败: 没有找到存储的token，请提供token")
                    return
                print(f"📝 使用存储的token重启 {listener_id}")
            else:
                print(f"📝 使用新token重启 {listener_id}")
            
            # 记录重启原因
            if reason:
                print(f"📋 重启原因: {reason}")
            
            # 先停止现有的listener（如果存在）
            if self.app.SXTS.get(listener_id) is not None:
                print(f"🛑 正在停止现有的 Listener {listener_id}...")
                try:
                    self.app.SXTS[listener_id].stop_background_loop()
                    del self.app.SXTS[listener_id]
                    print(f"✅ 现有 Listener {listener_id} 已停止")
                    
                    # 心跳超时导致的重启，稍微等待一下再启动
                    if reason and "心跳超时" in reason:
                        print("⏱️  等待3秒后重启...")
                        time.sleep(3)
                        
                except Exception as e:
                    print(f"⚠️ 停止现有 Listener 时出现警告: {e}")
                    # 强制删除，继续重启流程
                    if listener_id in self.app.SXTS:
                        del self.app.SXTS[listener_id]
            else:
                print(f"📋 Listener {listener_id} 之前不存在，将创建新实例")
            
            # 保存/更新token到Redis
            if token:
                self._save_token_to_redis(listener_id, token)
                print(f"💾 已保存token到Redis")
            
            # 启动新的listener
            print(f"🚀 正在启动新的 Listener {listener_id}...")
            sxt = LSXT(
                listener_id=listener_id,
                cookies={"access-token-sxt.xiaohongshu.com": token}
            )
            sxt.run()
            self.app.SXTS[listener_id] = sxt
            
            # 更新状态
            self._update_listener_status(listener_id, "running", {"restarted": True, "reason": reason})
            
            print(f"✅ Listener {listener_id} 重启成功")
            
            # 显示当前状态
            remaining = list(self.app.SXTS.keys())
            print(f"📊 当前运行中的listeners: {len(remaining)} 个")
            if remaining:
                print(f"   列表: {remaining}")
                
        except Exception as e:
            print(f"❌ 重启 Listener {listener_id} 失败: {e}")
            # 清理可能的不一致状态
            try:
                if listener_id in self.app.SXTS:
                    del self.app.SXTS[listener_id]
                    print(f"🧹 已清理失败的 Listener {listener_id} 实例")
                self._update_listener_status(listener_id, "failed", {"error": str(e), "operation": "restart"})
            except Exception as cleanup_error:
                print(f"⚠️ 清理失败状态时发生错误: {cleanup_error}")