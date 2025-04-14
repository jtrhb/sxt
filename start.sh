#!/bin/bash
echo "项目文件列表："
find . -type f

echo "🚀 Starting API"
python main.py &

echo "🛰️ Starting WebSocket"
python listener.py &

wait