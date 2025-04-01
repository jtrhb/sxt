import asyncio
import json
import base64
import websockets
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from message_queue import produce_new_msg

# AES-ECB 加密
def aes_ecb_encrypt(key: str, plaintext: str) -> str:
    cipher = AES.new(key.encode(), AES.MODE_ECB)
    ciphertext = cipher.encrypt(pad(plaintext.encode(), AES.block_size))
    return base64.urlsafe_b64encode(ciphertext).decode()


class WebSocketClient:
    WS_URI = "wss://zelda.xiaohongshu.com/websocketV2"
    APP_NAME = "walle-ad"
    APP_VERSION = "0.7.1"

    def __init__(self, app_id, user_id, seller_id, token, sxt_id):
        self.app_id = app_id
        self.user_id = user_id
        self.seller_id = seller_id
        self.token = token
        self.seq = 0
        self.sxt_id = sxt_id
        self.lock = asyncio.Lock()
        self.websocket = None

    async def increase_seq(self) -> int:
        async with self.lock:
            self.seq += 1
            return self.seq

    async def ws_send(self, data):
        if self.websocket:
            data["seq"] = await self.increase_seq()
            await self.websocket.send(json.dumps(data))
            print(f"> Sent: {data}\n")

    async def ack(self, ack_seq):
        await self.ws_send({"type": 130, "ack": ack_seq})

    async def handle_message(self, server_message):
        msg_type = server_message.get("type")

        match msg_type:
            case 2:  # 服务器要求 ACK
                await self.ack(server_message["seq"])
                if server_message['data']['type'] == 'PUSH_SIXINTONG_MSG' and server_message['data']['payload']['sixin_message']['sender_id'] != self.sxt_id:
                    produce_new_msg(server_message)
            case 4:
                await self.ws_send({"type": 132})
                await self.ws_send({"type": 4})
            case 129:  # 服务器返回 secureKey
                next_message = {
                    "type": 10,
                    "topic": aes_ecb_encrypt(server_message["secureKey"], self.user_id),
                    "encrypt": True
                }
                await self.ws_send(next_message)
            case 138:  # 服务器请求 userAgent & additionalInfo
                next_message = {
                    "type": 12,
                    "data": {
                        "userAgent": {"appName": self.APP_NAME, "appVersion": self.APP_VERSION},
                        "additionalInfo": {
                            "userId": self.user_id,
                            "sellerId": self.seller_id
                        }
                    }
                }
                await self.ws_send(next_message)
            case 132:  # 服务器心跳
                await asyncio.sleep(60)
                await self.ws_send({"type": 4})
            case 140:
                await asyncio.sleep(30)
                await self.ws_send({"type": 4})

    async def websocket_connect(self):
        while True:
            try:
                async with websockets.connect(self.WS_URI) as ws:
                    self.websocket = ws
                    print("[Connected] WebSocket connection established.")

                    await self.ws_send({
                        "type": 1,
                        "token": self.token,
                        "appId": self.app_id
                    })

                    while True:
                        response = await ws.recv()
                        server_message = json.loads(response)
                        print(f"< Received: {json.dumps(server_message, ensure_ascii=False)}\n")
                        asyncio.create_task(self.handle_message(server_message))

            except websockets.exceptions.ConnectionClosed:
                print("[Error] Connection closed, reconnecting in 3s...")
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                print("[Cancelled] WebSocket task cancelled.")
                break


async def main():
    client = WebSocketClient(
        app_id="647e8f23d15d890d5cc02700",
        user_id="67bc150804f0000000000003",
        seller_id="6698b21b3289650015d6f4df",
        token="7f54749ef19aaf9966ed7a616982c016bda5dfba",
        sxt_id="65000f210000000005000a45"  # sender_id of the account
    )
    await client.websocket_connect()


asyncio.run(main())
