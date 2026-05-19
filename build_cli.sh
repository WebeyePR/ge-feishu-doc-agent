#!/bin/bash

# This script compiles the lark-cli Go binary for the target deployment environment (Linux x64).
# It places the resulting binary in the lark_agent/bin/ directory so it can be packaged with the Python agent.

set -e

# 1. 确保目录存在
mkdir -p lark_agent/bin

# 2. 进入 CLI 源码目录
cd lib/cli

# 3. 执行编译
# 目标平台: Linux x64 (Vertex AI Reasoning Engine 运行环境)
echo "Building lark-cli for Linux x64..."
GOOS=linux GOARCH=amd64 go build -o ../../lark_agent/bin/lark-cli-linux

# 同时编译一个本地版本供测试 (根据当前系统自动识别)
echo "Building lark-cli for local testing..."
go build -o ../../lark_agent/bin/lark-cli

echo "Build complete!"
echo "Binaries are located in the lark_agent/bin/ directory:"
ls -lh ../../lark_agent/bin/
