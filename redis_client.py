import redis.asyncio as redis
from dotenv import load_dotenv
import os
from urllib.parse import urlparse

load_dotenv()
# Redis连接配置
redis_url = os.getenv("REDIS_URL")
url = urlparse(redis_url)
subscriber = redis.Redis(
    host=url.hostname,
    port=url.port,
    username=url.username,
    password=url.password,
    db=0,
    decode_responses=True
)

publisher = redis.Redis(
    host=url.hostname,
    port=url.port,
    username=url.username,
    password=url.password,
    db=0,
    decode_responses=True
)