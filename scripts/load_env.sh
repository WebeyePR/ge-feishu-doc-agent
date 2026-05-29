#!/bin/bash
# ==============================================================================
# 自动加载项目环境变量 (.env 和 .deploy_env)
# ==============================================================================

# 重置部署脚本动态管理的内存生成变量，杜绝当前 Shell 终端进程的环境变量残留污染。
# 确保每次加载皆以物理配置文件 (.deploy_env) 为绝对的唯一真相来源。
unset VERTEX_REASONING_ENGINE_NAME
unset GE_AGENT_RESOURCE_NAME

# 获取脚本所在目录的绝对路径
L_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 获取项目根目录 (假设此脚本在 scripts/ 目录下)
L_ROOT_DIR="$(dirname "$L_SCRIPT_DIR")"

# 支持指定环境后缀，优先通过参数传递，其次通过环境变量传递
ENV_SUFFIX="${1:-$ENV_SUFFIX}"

BASE_ENV_NAME=".env"
DEPLOY_ENV_NAME=".deploy_env"

if [ -n "$ENV_SUFFIX" ]; then
    BASE_ENV_NAME=".env-${ENV_SUFFIX}"
    DEPLOY_ENV_NAME=".deploy_env-${ENV_SUFFIX}"
fi

# 导出当前的物理配置文件绝对路径，供 Python 和其他脚本使用，确保全链路读写同一物理文件
export BASE_ENV_FILE="$L_ROOT_DIR/$BASE_ENV_NAME"
export DEPLOY_ENV_FILE="$L_ROOT_DIR/$DEPLOY_ENV_NAME"
export ENV_SUFFIX

# 如果当前不在根目录，且根目录存在相应配置文件，则加载
# 使用 set -a 确保所有加载的变量都会被自动 export

# 1. 加载基础配置
if [ -f "$BASE_ENV_FILE" ]; then
    set -a
    source "$BASE_ENV_FILE"
    set +a
fi

# 2. 加载部署生成配置
if [ -f "$DEPLOY_ENV_FILE" ]; then
    set -a
    source "$DEPLOY_ENV_FILE"
    set +a
fi

# 如果还是没有加载到必要变量，尝试直接在当前目录查找 (以防 CWD 变化且不匹配 L_ROOT_DIR)
if [ -z "$PROJECT_ID" ]; then
    if [ -f "$BASE_ENV_NAME" ]; then
        set -a
        source "$BASE_ENV_NAME"
        set +a
    fi
    if [ -f "$DEPLOY_ENV_NAME" ]; then
        set -a
        source "$DEPLOY_ENV_NAME"
        set +a
    fi
fi
