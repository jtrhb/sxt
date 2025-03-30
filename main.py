from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from message import SXT, cookies

app = FastAPI()

# 实例化SXT类
sxt = SXT(cookies=cookies)

# 定义请求数据模型
class SendMessage(BaseModel):
    receiver_id: str
    content: str
    
class MessageList(BaseModel):
    customer_user_id: str
    limit: str

@app.get("/info")
def get_info():
    """获取用户信息"""
    info = sxt.get_info()
    if not info:
        raise HTTPException(status_code=500, detail="获取用户信息失败")
    return info

@app.get("/has_new")
def has_new():
    """检查是否有新消息"""
    response = sxt.has_new()
    return response

@app.get("/chats")
def get_chats(is_active: str = "false", limit: str = "80"):
    """获取聊天列表"""
    response = sxt.get_chats(is_active=is_active, limit=limit)
    return response

@app.post("/chat_messages")
def get_chat_messages(query: MessageList):
    """获取具体聊天记录"""
    response = sxt.get_chat_messages(query.customer_user_id, query.limit)
    return JSONResponse(content = response)

@app.post("/send_text")
def send_text(msg: SendMessage):
    """发送文本消息"""
    response = sxt.send_text(msg.receiver_id, msg.content)
    return response

@app.get("/read_chat")
def read_chat(chat_user_id: str):
    """标记聊天为已读"""
    response = sxt.read_chat(chat_user_id)
    return response
  
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3333)