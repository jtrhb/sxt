#!/bin/bash
echo "🚀 Starting API"
python main.py &

echo "🛰️ Starting WebSocket"
python ws.py &

wait