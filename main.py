from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import json
import asyncio
from message_queue import ListenerCommandConsumer

app = FastAPI()
app.SXTS = {}
cookies = {
    "access-token-sxt.xiaohongshu.com": "customer.sxt.AT-68c517483891070912775173wndbrtlvszckosbb"
}

# 定义请求数据模型
class SendMessage(BaseModel):
    receiver_id: str
    content: str
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
async def get_chat_messages(query: MessageList, listener_id: str = 'default'):
    """获取具体聊天记录"""
    response = await app.SXTS[listener_id].get_chat_messages(query.customer_user_id, query.limit)
    return JSONResponse(content = response)

@app.post("/send_text")
async def send_text(msg: SendMessage, listener_id: str = 'default'):
    """发送文本消息"""
    response = await app.SXTS[listener_id].send_text(msg.receiver_id, msg.content)
    return response

@app.post("/send_image")
async def send_text(msg: SendMessage, listener_id: str = 'default'):
    """发送文本消息"""
    response = await app.SXTS[listener_id].send_image(msg.receiver_id, msg.content)
    return response

@app.post("/send_business_card")
async def send_business_card(msg: SendMessage, listener_id: str = 'default'):
    """发送名片"""
    business_cards = await app.SXTS[listener_id].get_business_cards()
    response = await app.SXTS[listener_id].send_card(
        msg.receiver_id,
        json.dumps({"type": "commercialBusinessCard", **business_cards["data"]["list"][0]})
    )
    return response

@app.get("/read_chat")
def read_chat(chat_user_id: str, listener_id: str = 'default'):
    """标记聊天为已读"""
    response = app.SXTS[listener_id].read_chat(chat_user_id)
    return response
  
if __name__ == "__main__":
    import uvicorn
    lcc = ListenerCommandConsumer(app)
    asyncio.run(lcc.start_listening())
    uvicorn.run(app, host="0.0.0.0", port=3333)