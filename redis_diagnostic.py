#!/usr/bin/env python3
"""
Redis Pub/Sub 连接测试和诊断工具
"""
import json
import time
import redis
from datetime import datetime

def test_redis_connection():
    """测试Redis连接"""
    try:
        # 假设你的redis_client.py中有类似的配置
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.ping()
        print("✅ Redis连接正常")
        return r
    except Exception as e:
        print(f"❌ Redis连接失败: {e}")
        return None

def test_publish_commands(redis_client):
    """测试发布命令"""
    if not redis_client:
        return
        
    commands = [
        {"command": "ping", "listener_id": "test"},
        {"command": "status", "listener_id": "test"},
        {"command": "start", "listener_id": "test1", "sxtToken": "test-token"},
        {"command": "stop", "listener_id": "test1"},
    ]
    
    for i, cmd in enumerate(commands, 1):
        try:
            message = json.dumps(cmd)
            result = redis_client.publish("listenerCommandChannel", message)
            print(f"📤 发送命令 {i}: {cmd['command']} -> {result} 个订阅者收到")
            time.sleep(1)  # 等待1秒
        except Exception as e:
            print(f"❌ 发送命令失败: {e}")

def test_subscribe(redis_client):
    """测试订阅"""
    if not redis_client:
        return
        
    pubsub = redis_client.pubsub()
    pubsub.subscribe("listenerCommandChannel")
    
    print("🔗 开始监听测试...")
    start_time = time.time()
    message_count = 0
    
    try:
        for message in pubsub.listen():
            if message['type'] == 'subscribe':
                print(f"✅ 订阅成功: {message['channel']}")
                continue
                
            if message['type'] == 'message':
                message_count += 1
                print(f"📨 收到消息 {message_count}: {message['data']}")
                
                # 测试5秒后停止
                if time.time() - start_time > 5:
                    break
                    
    except KeyboardInterrupt:
        print("\n🛑 手动停止测试")
    finally:
        pubsub.close()
        print(f"📊 测试结束，共收到 {message_count} 条消息")

def check_redis_info(redis_client):
    """检查Redis信息"""
    if not redis_client:
        return
        
    try:
        info = redis_client.info()
        print(f"📊 Redis版本: {info.get('redis_version')}")
        print(f"📊 连接的客户端数: {info.get('connected_clients')}")
        print(f"📊 内存使用: {info.get('used_memory_human')}")
        
        # 检查pub/sub信息
        pubsub_info = redis_client.info('replication')
        print(f"📊 Pub/Sub频道数: {info.get('pubsub_channels', 0)}")
        print(f"📊 Pub/Sub模式数: {info.get('pubsub_patterns', 0)}")
        
    except Exception as e:
        print(f"❌ 获取Redis信息失败: {e}")

def main():
    print("🚀 Redis Pub/Sub 诊断工具启动")
    print("=" * 50)
    
    # 测试连接
    redis_client = test_redis_connection()
    
    # 检查Redis信息
    check_redis_info(redis_client)
    print()
    
    while True:
        print("\n选择测试:")
        print("1. 发布测试命令")
        print("2. 监听测试")
        print("3. 检查Redis信息")
        print("4. 退出")
        
        choice = input("请选择 (1-4): ").strip()
        
        if choice == '1':
            test_publish_commands(redis_client)
        elif choice == '2':
            test_subscribe(redis_client)
        elif choice == '3':
            check_redis_info(redis_client)
        elif choice == '4':
            print("👋 再见!")
            break
        else:
            print("❌ 无效选择")

if __name__ == "__main__":
    main()
