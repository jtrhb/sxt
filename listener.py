from engine.pysxt.core import SXTWebSocketClient, aes_ecb_encrypt, SXT
import asyncio
import json
import time
import typing
import threading
import os
import websockets
from websockets_proxy import Proxy, proxy_connect
from redis_client import publisher

proxy_url = "socks5://14ac82adf87db:dec6b3a5a6@194.153.253.190:12324"
# 环境变量控制是否使用代理，默认不使用（Railway 部署时可以设置 USE_PROXY=true）
USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"

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
        self._shutdown_requested = False  # ✅ 添加关闭标志

    async def ws_send(self, data: dict) -> None:
        if self.websocket:
            # ACK消息(type=130)不需要添加seq
            if data["type"] == 130:
                await self.websocket.send(json.dumps(data))
                print(f"[Sent ACK] ack={data.get('ack')}")
                return
            
            # 心跳消息也不需要seq
            if data["type"] in [4, 132]:
                await self.websocket.send(json.dumps(data))
                return
            
            # 其他消息需要seq
            seq = await self.increase_seq()
            data["seq"] = seq
            await self.websocket.send(json.dumps(data))
            print(f"[Sent] type={data['type']}, seq={seq}")

    async def close(self):
        """正确关闭 WebSocket 连接"""
        ws = getattr(self, "websocket", None)
        if ws is None:
            return
        
        # ✅ 设置关闭标志
        self._shutdown_requested = True
        
        try:
            print(f"📤 正在关闭 WebSocket...")
            await ws.close(code=1001, reason="GoingAway")
            print(f"✅ WebSocket 已关闭")
        except websockets.exceptions.ConnectionClosed:
            print(f"ℹ️ WebSocket 已经关闭")
        except Exception as e:
            print(f"⚠️ 关闭 WebSocket 时出错: {e}")
        finally:
            self.websocket = None

    async def produce_new_msg(self, msg):
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
        await publisher.publish("newMessageChannel", json.dumps(message, ensure_ascii=False))
        print(f"发送消息: {message}")

    def _ensure_hb_task(self):
        if self._hb_task is None or self._hb_task.done():
            if self._hb_task is not None and self._hb_task.done():
                print("🔄 心跳任务已结束，重新启动")
            self._hb_task = asyncio.create_task(self._heartbeat_loop())
            print(f"💗 心跳任务已启动，间隔: {self._hb_interval}秒")

    async def _heartbeat_loop(self):
        # print(f"💗 心跳循环开始，间隔: {self._hb_interval}秒")  # 减少日志输出
        while self.websocket:
            try:
                now = time.time()
                if self._hb_next_deadline is None or now >= self._hb_next_deadline:
                    # print(f"💓 发送心跳包 type=4")  # 高频日志，注释掉
                    await self.ws_send({"type": 4})
                    self._hb_next_deadline = now + self._hb_interval
                await asyncio.sleep(1)
            except Exception as e:
                print(f"❌ 心跳循环异常: {e}")
                break
        # print("💔 心跳循环结束")  # 减少日志输出

    async def handle_message(self, server_message):
        msg_type = server_message.get("type")

        match msg_type:
            case 2:  # 服务器要求 ACK - ACK已经在connect()中发送了
                if server_message["data"]["type"] == "PUSH_SIXINTONG_MSG":
                    print('💬 收到新消息:')
                    if server_message['data']['payload']['sixin_message']['sender_id'] != self.sxt_id:
                        content = server_message['data']['payload']['sixin_message']['content']
                        if server_message['data']['payload']['sixin_message']['message_source'] == "ads_system":
                            server_message['data']['payload']['sixin_message']['content'] = json.loads(content)["content"]
                        print(f"收到消息: {server_message['data']['payload']['sixin_message']}")
                        asyncio.create_task(self.produce_new_msg(server_message))
                        return
            case 4:
                # print("💗 收到服务器心跳 type=4")  # 高频日志，注释掉
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
                await publisher.publish("listenerCommandChannel", json.dumps(restart_message, ensure_ascii=False))
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
                # print("💚 收到心跳响应 type=132")  # 高频日志，注释掉
                # 不修改 deadline，让心跳循环继续按计划运行
                pass
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
                old_interval = self._hb_interval
                self._hb_interval = 30
                print(f"📋 收到type=140，调整心跳间隔: {old_interval}s → {self._hb_interval}s")
                self._hb_next_deadline = time.time() + 1  # 1秒后发送
                self._ensure_hb_task()  # 确保心跳任务在运行
                print(f"⏰ 心跳将在1秒后发送，然后每{self._hb_interval}秒一次")
            case _:
                # 委托给基类处理未知类型
                await super().handle_message(server_message)

    async def connect(self) -> typing.NoReturn:
        print("Starting WebSocket connection...")
        retry_count = 0
        max_retry_delay = 60
        connection_timeout = 30
        
        # ✅ 重置关闭标志
        self._shutdown_requested = False
        
        while True:
            # ✅ 检查关闭标志
            if self._shutdown_requested:
                print(f"🛑 检测到主动关闭请求，停止重连循环")
                break
            
            try:
                proxy = Proxy.from_url(proxy_url)
                use_proxy_msg = "✅ 启用" if USE_PROXY else "❌ 禁用"
                print(f"[Connecting] 🔐 尝试连接 #{retry_count + 1} (代理: {use_proxy_msg})")
                if USE_PROXY:
                    print(f"[Proxy] 代理地址: {proxy_url.split('@')[1] if '@' in proxy_url else proxy_url}")
                print(f"[Timeout] 连接超时设置: {connection_timeout}秒")
                
                start_time = time.time()
                
                try:
                    if USE_PROXY:
                        websocket_ctx = proxy_connect(self.ws_uri, proxy=proxy, open_timeout=15)
                    else:
                        websocket_ctx = websockets.connect(self.ws_uri, open_timeout=15)
                    
                    async with websocket_ctx as self.websocket:
                        connect_time = time.time() - start_time
                        print(f"[Connected] ✅ WebSocket连接已建立 ({connect_time:.2f}秒)")
                        
                        # 设置连接就绪标志
                        if hasattr(self.sxt, 'connection_ready'):
                            self.sxt.connection_ready = True
                        
                        retry_count = 0  # 连接成功，重置计数器

                        # 发送登录消息
                        await self.ws_send({
                            "type": 1,
                            "token": self.token,
                            "appId": self.app_id
                        })

                        # 接收和处理消息
                        while True:
                            # ✅ 接收消息前检查关闭标志
                            if self._shutdown_requested:
                                print(f"🛑 检测到主动关闭请求，退出消息接收循环")
                                break
                            
                            response = await asyncio.wait_for(self.websocket.recv(), timeout=60)
                            server_message = json.loads(response)
                            print(f"[Received] {server_message}")
                            
                            if server_message.get("type") == 2:
                                await self.ws_send({"type": 130, "ack": server_message["seq"]})
                            
                            asyncio.create_task(self.handle_message(server_message))
                
                except asyncio.TimeoutError:
                    elapsed = time.time() - start_time
                    print(f"[Timeout] ❌ WebSocket连接超时 ({elapsed:.2f}秒)")
                    raise

            except Exception as e:
                # ✅ 检查是否主动关闭
                if self._shutdown_requested:
                    print(f"ℹ️ 主动关闭，忽略连接错误: {e}")
                    break
                
                if hasattr(self.sxt, 'connection_ready'):
                    self.sxt.connection_ready = False
                
                retry_count += 1
                delay = min(self.connect_retry_interval * retry_count, max_retry_delay)
                print(f"[Error] 连接错误: {e}, {delay}秒后重连...")
                self.seq = 0
                await asyncio.sleep(delay)
        
        print(f"🏁 WebSocket connect() 循环已退出")

class LSXT(SXT):
    def __init__(self, listener_id: str, cookies=None):
        super().__init__(cookies=cookies)
        self.listener_id = listener_id
        self.websocket_client = Listener(
            user_id=self.b_user_id, 
            seller_id=self.seller_id, 
            sxt_id=self.c_user_id, 
            listener_id=self.listener_id
        )
        self.websocket_client.attach(self)
        self.connection_ready = False  # 添加连接状态标志
    
    def start_background_loop(self, loop):
        """启动事件循环"""
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            print("🧹 清理事件循环...")
            
            # 1. 先关闭 WebSocket
            try:
                loop.run_until_complete(self.websocket_client.close())
            except Exception as e:
                print(f"⚠️ 清理 WebSocket 失败: {e}")
            
            # 2. 取消所有任务
            pending = asyncio.all_tasks(loop=loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            
            # 3. 关闭循环
            loop.close()
            print("✅ 事件循环已关闭")
        
    def stop_background_loop(self):
        """停止后台循环"""
        print(f"🛑 停止 Listener {self.listener_id}...")
        
        if not self.loop:
            return
        
        # 先关闭 WebSocket（在停止循环之前）
        if self.websocket_client:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.websocket_client.close(),
                    self.loop
                )
                future.result(timeout=5)
                print("✅ WebSocket 已提前关闭")
            except Exception as e:
                print(f"⚠️ 提前关闭失败（将在 finally 中重试）: {e}")
        
        # 停止事件循环
        self.loop.call_soon_threadsafe(self.loop.stop)
        
        # 等待线程退出
        if self.thread:
            self.thread.join(timeout=10)
        
        self.loop = None
        print(f"✅ Listener {self.listener_id} 已停止")
    
    async def listen(self) -> typing.NoReturn:
        await self.websocket_client.connect()
        
        print(
            f'[{self.user_detail["data"]["flow_user"]["status"]}] {self.user_detail["data"]["flow_user"]["name"]}')
        print("Message listening...")
        
    def run(self) -> typing.NoReturn:
        new_loop = asyncio.new_event_loop()
        t = threading.Thread(target=self.start_background_loop, args=(new_loop,), daemon=True)
        t.start()
        self.loop = new_loop
        self.thread = t
        
        # 启动WebSocket连接
        asyncio.run_coroutine_threadsafe(self.listen(), new_loop)
        
        print(f"🔄 Listener {self.listener_id} 后台线程已启动，WebSocket连接中...")
