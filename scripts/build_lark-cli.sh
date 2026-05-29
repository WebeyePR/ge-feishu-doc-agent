#!/bin/bash

# ==============================================================================
# WebEye Nexus CLI 构建脚本 (部署/本地统一使用 nexus_agent/bin)
# ==============================================================================

set -e

# --- 配置区 ---
CLI_VERSION="v1.0.40"
CLI_REPO_URL="https://github.com/larksuite/cli.git"
# --------------

# 获取项目根目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LIB_DIR="$PROJECT_ROOT/lib"
SPECIFIC_CLI_DIR="$LIB_DIR/lark-cli-$CLI_VERSION"
TARGET_DIR="$PROJECT_ROOT/nexus_agent/bin"
TARGET_BIN="$TARGET_DIR/lark-cli"

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
# 部署和本地运行统一使用 nexus_agent/bin 下的 Linux amd64 二进制。
# 注意：该二进制面向 Agent Engine / Linux 环境，本地 macOS 不直接执行 CLI。
echo "正在构建统一 CLI 二进制: Linux (amd64)..."
GOOS=linux GOARCH=amd64 go build -ldflags "$LDFLAGS" -o "$TARGET_BIN" .
chmod +x "$TARGET_BIN"
echo "成功: $TARGET_BIN (Linux amd64)"

echo "--- 构建完成 ($CLI_VERSION) ---"
