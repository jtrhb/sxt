import redis
import json
import time
import os
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urlparse
import threading

load_dotenv()
# Redis连接配置
redis_url = os.getenv("REDIS_URL")
url = urlparse(redis_url)
redis_client = redis.Redis(
    host=url.hostname,
    port=url.port,
    username=url.username,
    password=url.password,
    db=0,
    decode_responses=True
)

# 队列名称
MESSAGE_QUEUE = "message_queue"
PROCESSED_SET = "processed_messages"

# 生产者：推送消息到队列
class MessageProducer:
    def push_message(self, message_data):
        """推送消息到redis的哈希中并通知消费者"""
        message_id = f"msg:{int(time.time())}:{hash(str(message_data))}"
        message = {
            "id": message_id,
            "data": message_data,
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        }
        redis_client.hset("newMessage:unprocessed", message_id, json.dumps(message))
        redis_client.publish("newMessageChannel", message_id)
        print(f"Pushed and notified message: {message_id}")
        return message_id

# 消费者：只监听新消息
class MessageConsumer:
    def __init__(self):
        self.running = False

    def start_listening(self):
        """实时监听并处理新消息"""
        self.running = True
        pubsub = redis_client.pubsub()
        pubsub.subscribe("newMessageChannel")
        print("Consumer subscribed to channel, waiting for new messages...")

        for message in pubsub.listen():
            if message['type'] == 'message' and self.running:
                message_id = message['data']
                message_json = redis_client.hget("newMessage:unprocessed", message_id)
                if message_json:
                    self.process_message(message_json)
                    redis_client.hset("newMessage:processed", message_id, message_json)
                    redis_client.hdel("newMessage:unprocessed", message_id)

    def process_message(self, message_json):
        """处理单个消息"""
        try:
            message = json.loads(message_json)
            message_id = message["id"]
            
            # 检查是否已处理
            if redis_client.sismember(PROCESSED_SET, message_id):
                print(f"Message {message_id} already processed, skipping")
                return
            
            # 处理消息
            print(f"Processing new message: {message_id}")
            print(f"Message data: {message['data']}")
            
            # 标记为已处理
            redis_client.sadd(PROCESSED_SET, message_id)
            print(f"Marked message as processed: {message_id}")
            
        except Exception as e:
            print(f"Error processing message: {e}")
            # 如果处理失败，可以选择将消息放回哈希
            redis_client.hset("newMessage:error", message_id, message_json)

    def stop_listening(self):
        """停止监听"""
        self.running = False
        print("Consumer stopped listening")

def produce_new_msg(msg):
    message_id = f"msg:{int(time.time())}:{msg['data']['payload']['sixin_message']['id']}"
    payload = msg['data']['payload']['sixin_message']
    message = {
        "user_id": payload['sender_id'],
        "last_msg_content": payload['content'],
        "last_msg_ts": payload['created_at'],
        "last_store_id": payload['store_id'],
        "view_store_id": payload['store_id'],
        "avatar": "",
        "nickname": ""
    }
    # redis_client.hset("newMessage:unprocessed", message_id, json.dumps(message))
    # print(f"Pushed message to hash newMessage:unprocessed: {message_id}")
    redis_client.publish("newMessageChannel", json.dumps(message, ensure_ascii=False))
    print(f"notified consumer: {message_id}")
    

# 示例使用
def run_producer():
    # 模拟启动时已处理的消息
    print("Simulating previously processed messages...")
    for i in range(3):
        message_id = f"msg:{int(time.time())}:old:{i}"
        message = {
            "id": message_id,
            "data": {"user_id": i, "content": f"Old Processed Message {i}"},
            "timestamp": datetime.now().isoformat(),
            "status": "processed"
        }
        redis_client.hset("newMessage:processed", message_id, json.dumps(message))

    # 推送新消息到未处理队列
    print("Pushing new unprocessed messages...")
    for i in range(3, 6):
        message_id = f"msg:{int(time.time())}:{i}"
        message = {
            "id": message_id,
            "data": {"user_id": i, "content": f"New Unprocessed Message {i}"},
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        }
        redis_client.hset("newMessage:unprocessed", message_id, json.dumps(message))
        print(f"Pushed message to hash newMessage:unprocessed: {message_id}")
        redis_client.publish("newMessageChannel", message_id)
        print(f"notified consumer: {message_id}")

def run_consumer():
    consumer = MessageConsumer()
    consumer.start_listening()

if __name__ == "__main__":
    # 清空队列和已处理集合（测试用）
    redis_client.delete(MESSAGE_QUEUE, PROCESSED_SET)

    # 创建生产者和消费者线程
    producer_thread = threading.Thread(target=run_producer)
    consumer_thread = threading.Thread(target=run_consumer)

    # 先启动消费者
    consumer_thread.start()

    # 再启动生产者
    producer_thread.start()

    # 等待生产者完成
    producer_thread.join()
    
    # 让消费者再运行几秒后停止
    time.sleep(5)
    consumer = MessageConsumer()
    consumer.stop_listening()
    consumer_thread.join()