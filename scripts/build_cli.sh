#!/bin/bash

# ==============================================================================
# Lark CLI 跨平台构建脚本 (支持版本锁定与自动下载)
# ==============================================================================

set -e

# --- 配置区 ---
CLI_VERSION="v1.0.32"
CLI_REPO_URL="https://github.com/larksuite/cli.git"
# --------------

# 获取项目根目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LIB_DIR="$PROJECT_ROOT/lib"
SPECIFIC_CLI_DIR="$LIB_DIR/lark-cli-$CLI_VERSION"
TARGET_DIR="$PROJECT_ROOT/lark_agent/bin"

mkdir -p "$TARGET_DIR"
mkdir -p "$LIB_DIR"

echo "--- 正在准备 Lark CLI ($CLI_VERSION) ---"

# 1. 检查指定版本的源码是否存在，不存在则 clone
if [ ! -d "$SPECIFIC_CLI_DIR" ]; then
    echo "未发现版本 $CLI_VERSION 的源码，正在从官方仓库克隆..."
    git clone --branch "$CLI_VERSION" --depth 1 "$CLI_REPO_URL" "$SPECIFIC_CLI_DIR"
else
    echo "已发现版本 $CLI_VERSION 的源码: $SPECIFIC_CLI_DIR"
fi

# 2. 检查 go 是否安装
if ! command -v go >/dev/null 2>&1; then
    echo "错误: 未找到 go 命令。请先安装 Go 语言环境 (https://golang.org/doc/install)。"
    exit 1
fi

cd "$SPECIFIC_CLI_DIR"

# 3. 构造编译参数
# 使用指定的版本号注入
LDFLAGS="-s -w -X github.com/larksuite/cli/internal/build.Version=${CLI_VERSION} -X github.com/larksuite/cli/internal/build.Date=$(date +%Y-%m-%d)"

# 4. 执行编译
# 4.1 为云端部署构建 Linux 二进制文件 (Reasoning Engine 使用 Linux 环境)
echo "正在为云端部署构建 Linux (amd64) 二进制文件..."
GOOS=linux GOARCH=amd64 go build -ldflags "$LDFLAGS" -o "$TARGET_DIR/lark-cli" .
echo "成功: $TARGET_DIR/lark-cli (Linux amd64)"

# 4.2 为本地调试构建当前平台的二进制文件 (可选)
LOCAL_BIN="$PROJECT_ROOT/bin/lark-cli"
mkdir -p "$(dirname "$LOCAL_BIN")"
echo "正在为本地调试构建当前平台二进制文件..."
go build -ldflags "$LDFLAGS" -o "$LOCAL_BIN" .
echo "成功: $LOCAL_BIN (Local)"

echo "--- 构建完成 ($CLI_VERSION) ---"
