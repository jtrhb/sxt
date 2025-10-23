#!/bin/bash
echo "🚀 Starting API"

# ✅ 使用Railway的PORT环境变量，本地默认3333
PORT=${PORT:-3333}
echo "📍 启动端口: $PORT"
echo "🌍 环境: ${RAILWAY_ENVIRONMENT:-local}"

# ✅ 设置 PYTHONUNBUFFERED=1 强制 Python 不缓冲输出
export PYTHONUNBUFFERED=1

# uvicorn main:app --host 0.0.0.0 --port $PORT --lifespan on
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port $PORT \
    --log-level info \
    --lifespan on