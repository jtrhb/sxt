from engine.pysxt.core import SXTWebSocketClient, aes_ecb_encrypt, SXT
import asyncio
import json
import time
import typing
import threading
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
        app_version: str = "0.21.0",
        connect_retry_interval: int = 3
    ):
        super().__init__(user_id, seller_id, ws_uri, app_id, token, app_name, app_version, connect_retry_interval)
        self.sxt_id = sxt_id
        self.listener_id = listener_id

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
        print(f"notified consumer: {message_id}")

    async def send_periodic_heartbeat(self):
        while True:
            try:
                await self.ws_send({"type": 4})
                await asyncio.sleep(30)
            except Exception as e:
                print(f"Error sending heartbeat: {e}")
                break

    async def handle_message(self, server_message):
        msg_type = server_message.get("type")

        match msg_type:
            case 2:  # 服务器要求 ACK
                await self.ws_send({"type": 130, "ack": server_message["seq"]})
                if server_message["data"]["type"] == "PUSH_SIXINTONG_MSG" and server_message['data']['payload']['sixin_message']['sender_id'] != self.sxt_id:
                    self.produce_new_msg(server_message)
            case 4:
                await self.ws_send({"type": 132})
                # await self.ws_send({"type": 4})
            case 129:  # 服务器返回 secureKey
                await self.ws_send({
                    "type": 10,
                    "topic": aes_ecb_encrypt(server_message["secureKey"], self.user_id),
                    "encrypt": True
                })
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
                if not hasattr(self, "_heartbeat_task_started"):
                    setattr(self, "_heartbeat_task_started", True)
                    asyncio.create_task(self.send_periodic_heartbeat())

class LSXT(SXT):
    def __init__(self, listener_id: str, cookies=None):
        super().__init__(cookies=cookies)
        self.listener_id = listener_id
        self.websocket_client = Listener(user_id=self.b_user_id, seller_id=self.seller_id, sxt_id=self.c_user_id, listener_id=self.listener_id)
        self.websocket_client.attach(self)
    
    def start_background_loop(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()
        # run_forever() 结束后，才会执行到这里
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
