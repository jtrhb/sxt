#!/bin/bash
echo "项目文件列表："
find ./engine

echo "🚀 Starting API"
python main.py &

echo "🛰️ Starting WebSocket"
python listener.py &

wait