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
        pubsub = subscriber.pubsub()
        pubsub.subscribe("listenerCommandChannel")
        loop = asyncio.get_running_loop()
        print("Consumer subscribed to channel, waiting for new listener commands...")

        def sync_loop():
            for message in pubsub.listen():
                if message['type'] == 'message' and self.running:
                    try:
                        payload = json.loads(message["data"])
                    except json.JSONDecodeError:
                        print(f"无效 JSON 消息: {message['data']}")
                        continue
                    command = payload.get("command", "").lower()
                    listener_id = payload.get("listener_id", "").strip()
                    # cookie 为可选参数，如果没有则默认为空字符串
                    token = payload.get("sxtToken", "").strip()
                    if not command or not listener_id:
                        print(f"消息缺少必要的 'command' 或 'listener_id' 键: {payload}")
                        continue

                    if command == "start":
                        self.start_listener(listener_id, token)
                    elif command == "stop":
                        self.stop_listener(listener_id)
                    else:
                        print(f"未知命令 '{command}'。只支持 start 和 stop。")

        await loop.run_in_executor(None, sync_loop)

    def start_listener(self, listener_id, token):
        """初始化SXT类"""
        if self.app.SXTS.get(listener_id) is not None:
            return
        sxt = LSXT(
            listener_id=listener_id, 
            cookies={"access-token-sxt.xiaohongshu.com": token}
        )
        sxt.run()
        self.app.SXTS[listener_id] = sxt
        print(f"Listener {listener_id} 已启动")

    def stop_listener(self, listener_id):
        """停止监听"""
        if self.app.SXTS.get(listener_id) is None:
            print(f"Listener {listener_id} 不存在")
            return
        self.app.SXTS[listener_id].stop_background_loop()
        del self.app.SXTS[listener_id]
        print(f"Listener {listener_id} 已停止")

    def stop_listening(self):
        """停止监听"""
        self.running = False
        print("Consumer stopped listening")