#!/bin/bash
echo "项目文件列表："
find .

echo "🚀 Starting API"
python main.py &

echo "🛰️ Starting WebSocket"
python listener.py &

wait