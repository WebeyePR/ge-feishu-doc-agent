#!/bin/bash
# ==============================================================================
# 自动加载项目环境变量 (.env 和 .deploy_env)
# ==============================================================================

# 获取脚本所在目录的绝对路径
L_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 获取项目根目录 (假设此脚本在 scripts/ 目录下)
L_ROOT_DIR="$(dirname "$L_SCRIPT_DIR")"

# 如果当前不在根目录，且根目录存在相应配置文件，则加载
# 使用 set -a 确保所有加载的变量都会被自动 export

# 1. 加载基础配置 .env
if [ -f "$L_ROOT_DIR/.env" ]; then
    # echo "--- 自动加载 .env ---"
    set -a
    source "$L_ROOT_DIR/.env"
    set +a
fi

# 2. 加载部署生成配置 .deploy_env
if [ -f "$L_ROOT_DIR/.deploy_env" ]; then
    # echo "--- 自动加载 .deploy_env ---"
    set -a
    source "$L_ROOT_DIR/.deploy_env"
    set +a
fi

# 如果还是没有加载到必要变量（比如在根目录运行且上述逻辑没触发），尝试直接在当前目录查找
if [ -z "$PROJECT_ID" ]; then
    if [ -f ".env" ]; then
        set -a
        source .env
        set +a
    fi
    if [ -f ".deploy_env" ]; then
        set -a
        source .deploy_env
        set +a
    fi
fi
