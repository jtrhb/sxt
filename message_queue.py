import json
import time
import asyncio
from datetime import datetime
import time
from listener import LSXT
from redis_client import subscriber
# 队列名称
MESSAGE_QUEUE = "message_queue"
PROCESSED_SET = "processed_messages"
# 消费者：只监听新消息
class ListenerCommandConsumer:
    def __init__(self, app):
        self.running = False
        self.app = app

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
                                    elif command == "status":
                                        self.show_status()
                                    elif command == "ping":
                                        print(f"🏓 Pong - 监听器活跃，当前时间: {datetime.now()}")
                                    else:
                                        print(f"❓ 未知命令 '{command}'。支持的命令: start, stop, status, ping")
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
                
            sxt = LSXT(
                listener_id=listener_id, 
                cookies={"access-token-sxt.xiaohongshu.com": token}
            )
            sxt.run()
            self.app.SXTS[listener_id] = sxt
            print(f"✅ Listener {listener_id} 已成功启动")
            
        except Exception as e:
            print(f"❌ 启动 Listener {listener_id} 失败: {e}")

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
                    print(f"🗑️ 已从字典中移除 {listener_id}")
            except KeyError:
                pass

    def show_status(self):
        """显示当前listeners状态"""
        try:
            listeners = list(self.app.SXTS.keys())
            count = len(listeners)
            print(f"📊 当前状态: {count} 个listeners在运行")
            if listeners:
                for i, listener_id in enumerate(listeners, 1):
                    print(f"  {i}. {listener_id}")
            else:
                print("  没有运行中的listeners")
        except Exception as e:
            print(f"❌ 获取状态失败: {e}")

    def stop_listening(self):
        """停止监听"""
        self.running = False
        print("Consumer stopped listening")