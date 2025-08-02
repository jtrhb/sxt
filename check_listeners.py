#!/usr/bin/env python3
"""
查看当前运行的listeners状态的命令行工具
"""
import requests
import json
import sys

def check_listeners(host="localhost", port=3333):
    """检查当前运行的listeners"""
    try:
        url = f"http://{host}:{port}/listeners"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print(f"📊 当前运行的Listeners状态:")
            print(f"   数量: {data['count']}")
            print(f"   时间: {data['timestamp']}")
            
            if data['listeners']:
                print(f"   Listener列表:")
                for i, listener in enumerate(data['listeners'], 1):
                    print(f"     {i}. {listener}")
            else:
                print(f"   没有运行中的listeners")
        else:
            print(f"❌ 请求失败: HTTP {response.status_code}")
            print(f"   响应内容: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接到服务器 {host}:{port}")
        print(f"   请确保SXT服务正在运行")
    except requests.exceptions.Timeout:
        print(f"❌ 请求超时")
    except Exception as e:
        print(f"❌ 发生错误: {e}")

if __name__ == "__main__":
    # 支持命令行参数指定host和port
    host = "localhost"
    port = 3333
    
    if len(sys.argv) >= 2:
        host = sys.argv[1]
    if len(sys.argv) >= 3:
        try:
            port = int(sys.argv[2])
        except ValueError:
            print("❌ 端口号必须是数字")
            sys.exit(1)
    
    check_listeners(host, port)
