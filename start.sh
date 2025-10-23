#!/bin/bash
echo "项目文件列表："
find engine

echo "🚀 Starting API"
uvicorn main:app --host 0.0.0.0 --port 3333

wait