from engine.pysxt.core import SXTWebSocketClient, aes_ecb_encrypt, SXT
import asyncio
import json
import time
import typing
import threading
import websockets
from redis_client import publisher

class Listener(SXTWebSocketClient):
    def __init__(
        self,
        user_id: str,
        seller_id: str,
        sxt_id: str,
        listener_id: str,
        ws_uri: str = "wss://zelda.xiaohongshu.com/websocketV2",
        app_id: str = "647e8f23d15d890d5cc02700",
        token: str = "7f54749ef19aaf9966ed7a616982c016bda5dfba",
        app_name: str = "walle-ad",
        app_version: str = "0.44.2",
        connect_retry_interval: int = 3
    ):
        super().__init__(user_id, seller_id, ws_uri, app_id, token, app_name, app_version, connect_retry_interval)
        self.sxt_id = sxt_id
        self.listener_id = listener_id
        self._hb_interval = 30
        self._hb_task = None
        self._hb_next_deadline = None
        
    async def ws_send(self, data: dict) -> None:
        if self.websocket:
            seq = await self.increase_seq()
            if data["type"] != 4 and data["type"] != 132:
                data["seq"] = seq
            await self.websocket.send(json.dumps(data))
            # print(f"[Sent] {data}")

    async def close(self):
        ws = getattr(self, "websocket", None)
        if ws is None:
            return
        await ws.close()

    def produce_new_msg(self, msg):
        message_id = f"msg:{int(time.time())}:{msg['data']['payload']['sixin_message']['id']}"
        payload = msg['data']['payload']['sixin_message']
        message = {
            "user_id": payload['sender_id'],
            "last_msg_content": payload['content'],
            "last_msg_ts": payload['created_at'],
            "last_store_id": payload['store_id'],
            "view_store_id": payload['store_id'],
            "listener_id": self.listener_id,
            "avatar": "",
            "nickname": ""
        }
        publisher.publish("newMessageChannel", json.dumps(message, ensure_ascii=False))
        print(f"发送消息: {message}")

    def _ensure_hb_task(self):
        if self._hb_task is None or self._hb_task.done():
            self._hb_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self):
        while self.websocket:
            now = time.time()
            if self._hb_next_deadline is None or now >= self._hb_next_deadline:
                await self.ws_send({"type": 4})
                self._hb_next_deadline = now + self._hb_interval
            await asyncio.sleep(1)

    async def handle_message(self, server_message):
        msg_type = server_message.get("type")

        match msg_type:
            case 2:  # 服务器要求 ACK
                await self.ws_send({"type": 130, "ack": server_message["seq"]})
                if server_message["data"]["type"] == "PUSH_SIXINTONG_MSG":
                    if server_message['data']['payload']['sixin_message']['sender_id'] != self.sxt_id:
                        content = server_message['data']['payload']['sixin_message']['content']
                        if server_message['data']['payload']['sixin_message']['message_source'] == "ads_system":
                            server_message['data']['payload']['sixin_message']['content'] = json.loads(content)["content"]
                        print(f"收到消息: {server_message['data']['payload']['sixin_message']}")
                        self.produce_new_msg(server_message)
                        return
            case 4:
                await self.ws_send({"type": 132})
                self._ensure_hb_task()
            case 8:  # 心跳超时，服务器要求重连
                reason = server_message.get("reason", "未知原因")
                print(f"💔 收到心跳超时消息 (type=8): {reason}")
                print(f"🔄 准备重启 Listener {self.listener_id}...")
                
                # 通过Redis发送重启命令给消息队列处理器
                restart_message = {
                    "command": "restart",
                    "listener_id": self.listener_id,
                    "reason": f"心跳超时: {reason}",
                    "timestamp": time.time()
                }
                publisher.publish("listenerCommandChannel", json.dumps(restart_message, ensure_ascii=False))
                print(f"📡 已发送重启命令到消息队列: {self.listener_id}")
                
                # 关闭当前WebSocket连接，触发重连逻辑
                if self.websocket:
                    await self.websocket.close()
                    print(f"🔌 已关闭 WebSocket 连接")
                return
            case 129:  # 服务器返回 secureKey
                await self.ws_send({
                    "type": 10,
                    "topic": aes_ecb_encrypt(server_message["secureKey"], self.user_id),
                    "encrypt": True
                })
            case 132:
                # 服务器确认；刷新 deadline，但不立即 sleep
                self._hb_next_deadline = time.time() + self._hb_interval
            case 138:  # 服务器请求 userAgent & additionalInfo
                await self.ws_send({
                    "type": 12,
                    "data": {
                        "userAgent": {"appName": self.app_name, "appVersion": self.app_version},
                        "additionalInfo": {
                            "userId": self.user_id,
                            "sellerId": self.seller_id
                        }
                    }
                })
            case 140:
                self._hb_interval = 30
                self._hb_next_deadline = time.time() + 30  # 尽快进入新周期
                self._ensure_hb_task()
            case _:
                # 委托给基类处理未知类型
                await super().handle_message(server_message)

    async def connect(self) -> typing.NoReturn:
        while True:
            try:
                async with websockets.connect(self.ws_uri) as self.websocket:
                    print("[Connected] WebSocket connection established.")

                    await self.ws_send({
                        "type": 1,
                        "token": self.token,
                        "appId": self.app_id
                    })

                    while True:
                        response = await asyncio.wait_for(self.websocket.recv(), timeout=60)
                        server_message = json.loads(response)
                        print(f"[Received] {server_message}")
                        asyncio.create_task(self.handle_message(server_message))

            except websockets.exceptions.ConnectionClosed:
                print("[Error] Connection closed, reconnecting in 3s...")
                self.seq = 0
                await asyncio.sleep(self.connect_retry_interval)
class LSXT(SXT):
    def __init__(self, listener_id: str, cookies=None):
        super().__init__(cookies=cookies)
        self.listener_id = listener_id
        self.websocket_client = Listener(user_id=self.b_user_id, seller_id=self.seller_id, sxt_id=self.c_user_id, listener_id=self.listener_id)
        self.websocket_client.attach(self)
    
    def start_background_loop(self, loop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            # run_forever() 结束后，才会执行到这里
            pending = asyncio.all_tasks(loop=loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(self.websocket_client.close())
            print("WebSocket client closed.")
            loop.close()
            print("Background loop closed.")
        
    def stop_background_loop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.loop = None
            print("Background loop stopped.")
            self.thread.join()
            print("后台线程已退出，ws loop结束。")
        
    def run(self) -> typing.NoReturn:
        new_loop = asyncio.new_event_loop()
        t = threading.Thread(target=self.start_background_loop, args=(new_loop,), daemon=True)
        t.start()
        self.loop = new_loop
        self.thread = t
        asyncio.run_coroutine_threadsafe(self.listen(), new_loop)
