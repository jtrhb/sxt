from core.pysxt.core import SXTWebSocketClient, aes_ecb_encrypt
from message_queue import produce_new_msg
import asyncio

class Listener(SXTWebSocketClient):
    async def handle_message(self, server_message):
        msg_type = server_message.get("type")

        match msg_type:
            case 2:  # 服务器要求 ACK
                await self.ws_send({"type": 130, "ack": server_message["seq"]})
                if server_message["data"]["type"] == "PUSH_SIXINTONG_MSG":
                    produce_new_msg(server_message)
            case 4:
                await self.ws_send({"type": 132})
                await self.ws_send({"type": 4})
            case 129:  # 服务器返回 secureKey
                await self.ws_send({
                    "type": 10,
                    "topic": aes_ecb_encrypt(server_message["secureKey"], self.user_id),
                    "encrypt": True
                })
            case 132:  # 服务器心跳
                await asyncio.sleep(60)
                await self.ws_send({"type": 4})
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
                await asyncio.sleep(30)
                await self.ws_send({"type": 4})

async def start():
    client = Listener(
        app_id="647e8f23d15d890d5cc02700",
        user_id="67bc150804f0000000000003",
        seller_id="6698b21b3289650015d6f4df",
        token="7f54749ef19aaf9966ed7a616982c016bda5dfba",
        sxt_id="65000f210000000005000a45"  # sender_id of the account
    )
    await client.connect()

asyncio.run(start())
