"""
日志工具 - 为每条日志添加实例ID前缀
"""
import os

# 全局实例ID（在应用启动时设置）
INSTANCE_ID = None

def set_instance_id(instance_id: str):
    """设置当前实例的ID"""
    global INSTANCE_ID
    INSTANCE_ID = instance_id

def log(message: str):
    """打印带实例ID前缀的日志"""
    if INSTANCE_ID:
        # 只取实例ID的后8位，避免日志太长
        short_id = INSTANCE_ID.split('-')[-1][:8]
        print(f"[{short_id}] {message}")
    else:
        print(message)
