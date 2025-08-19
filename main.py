from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from datetime import datetime
import json
import asyncio
import random
import time
from message_queue import ListenerCommandConsumer

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时的操作
    print("🚀 启动 SXT 应用...")
    consumer = ListenerCommandConsumer(app)
    
    # 自动恢复之前存储的listeners
    print("🔄 尝试自动恢复listeners...")
    consumer.auto_recover_listeners()
    
    # 启动消息队列监听
    task = asyncio.create_task(consumer.start_listening())
    
    yield
    
    # 关闭时的操作
    print("🛑 关闭 SXT 应用...")
    consumer.stop_listening()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)
app.SXTS = {}
cookies = {
    "access-token-sxt.xiaohongshu.com": "customer.sxt.AT-68c517483891070912775173wndbrtlvszckosbb"
}

# 定义请求数据模型
class SendMessage(BaseModel):
    receiver_id: str
    content: str
    listener_id: str = None
    
class SendBusinessCard(BaseModel):
    receiver_id: str
    listener_id: str = None
    
class MessageList(BaseModel):
    customer_user_id: str
    limit: str
    listener_id: str = None

# @app.get("/info")
# def get_info():
#     """获取用户信息"""
#     info = sxt.get_info()
#     if not info:
#         raise HTTPException(status_code=500, detail="获取用户信息失败")
#     return info

# @app.get("/has_new")
# def has_new():
#     """检查是否有新消息"""
#     response = sxt.has_new()
#     return response

@app.get("/chats")
def get_chats(is_active: str = "false", limit: str = "80", listener_id: str = 'default'):
    """获取聊天列表"""
    response = app.SXTS[listener_id].get_chats(is_active=is_active, limit=limit)
    return response

@app.post("/chat_messages")
async def get_chat_messages(query: MessageList):
    """获取具体聊天记录"""
    response = await app.SXTS[query.listener_id].get_chat_messages(query.customer_user_id, query.limit)
    return JSONResponse(content = response)

@app.post("/send_text")
async def send_text(msg: SendMessage):
    """发送文本消息"""
    response = await app.SXTS[msg.listener_id].send_text(msg.receiver_id, msg.content)
    return response

@app.post("/send_image")
async def send_text(msg: SendMessage):
    """发送文本消息"""
    response = await app.SXTS[msg.listener_id].send_image(msg.receiver_id, msg.content)
    return response

@app.post("/send_business_card")
async def send_business_card(msg: SendBusinessCard):
    """发送名片"""
    business_cards = await app.SXTS[msg.listener_id].get_business_cards()
    # 随机选择一个名片
    card_list = business_cards["data"]["list"]
    selected_card = random.choice(card_list)
    response = await app.SXTS[msg.listener_id].send_card(
        msg.receiver_id,
        json.dumps({"type": "commercialBusinessCard", **selected_card})
    )
    return response

@app.get("/read_chat")
def read_chat(chat_user_id: str, listener_id: str = 'default'):
    """标记聊天为已读"""
    response = app.SXTS[listener_id].read_chat(chat_user_id)
    return response

@app.get("/listeners")
def get_listeners():
    """获取当前运行的listeners列表"""
    listeners = list(app.SXTS.keys())
    # 获取token信息（如果ListenerCommandConsumer实例可访问的话）
    listener_details = []
    for listener_id in listeners:
        listener_details.append({
            "id": listener_id,
            "status": "running",
            "has_token": True  # 这里可以添加更详细的token检查逻辑
        })
    
    return {
        "count": len(listeners),
        "listeners": listener_details,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/tokens/status")
def get_token_status():
    """获取token存储状态（需要访问ListenerCommandConsumer实例）"""
    # 这个需要在lifespan中保存ListenerCommandConsumer实例的引用
    return {
        "message": "Token status endpoint - implementation needed",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/tokens/redis")
def get_redis_tokens():
    """获取Redis中存储的所有tokens状态"""
    try:
        from redis_client import subscriber
        
        # 获取tokens
        redis_tokens = subscriber.hgetall("sxt:tokens")
        tokens_info = []
        
        for listener_id_bytes, token_bytes in redis_tokens.items():
            listener_id = listener_id_bytes.decode('utf-8')
            token = token_bytes.decode('utf-8')
            masked_token = f"{token[:8]}***{token[-4:]}"
            
            # 检查是否正在运行
            is_running = listener_id in app.SXTS
            
            tokens_info.append({
                "listener_id": listener_id,
                "token_masked": masked_token,
                "is_running": is_running,
                "status": "running" if is_running else "stopped"
            })
        
        # 获取状态信息
        try:
            status_info = subscriber.hgetall("sxt:listener_status")
            status_dict = {}
            for k, v in status_info.items():
                status_dict[k.decode('utf-8')] = json.loads(v.decode('utf-8'))
        except:
            status_dict = {}
        
        return {
            "total_stored": len(redis_tokens),
            "running_count": len(app.SXTS),
            "tokens": tokens_info,
            "status": status_dict,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取Redis tokens失败: {str(e)}")

@app.post("/recover")
def manual_recover():
    """手动触发恢复所有存储的listeners"""
    try:
        # 发送恢复命令
        recover_message = {
            "command": "recover",
            "listener_id": "system",  # 系统命令
            "timestamp": time.time()
        }
        
        from redis_client import publisher
        publisher.publish("listenerCommandChannel", json.dumps(recover_message, ensure_ascii=False))
        
        return {
            "message": "已发送恢复命令",
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"发送恢复命令失败: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3333)