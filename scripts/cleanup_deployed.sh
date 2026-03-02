#!/bin/bash
# ==============================================================================
# 完全清理部署资源脚本
# 功能：根据本地环境变量记录，彻底删除远程 Agent、授权资源和 Reasoning Engine
# ==============================================================================

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# 1. 加载环境变量
if [ -f "$SCRIPT_DIR/load_env.sh" ]; then
    source "$SCRIPT_DIR/load_env.sh"
else
    echo "❌ 错误: 找不到 $SCRIPT_DIR/load_env.sh"
    exit 1
fi

echo "=========================================="
echo "🛡️  正在准备清理远程部署资源..."
echo "=========================================="

# 检查必要的基础变量
if [ -z "$PROJECT_ID" ]; then
    echo "❌ 错误: PROJECT_ID 未设置，请检查 .env 文件"
    exit 1
fi

TOKEN=$(gcloud auth print-access-token)
if [ -z "$TOKEN" ]; then
    echo "❌ 错误: 无法获取 gcloud access token，请先运行 gcloud auth login"
    exit 1
fi

# 2. 删除 Gemini Enterprise Agent
if [ -n "$GE_AGENT_RESOURCE_NAME" ]; then
    bash "$SCRIPT_DIR/delete_agent.sh" "$GE_AGENT_RESOURCE_NAME"
else
    echo "⏭️  跳过 Agent 删除: 未在 .deploy_env 中找到 GE_AGENT_RESOURCE_NAME"
fi

# 3. 删除授权资源 (Authorization)
if [ -n "$LARK_AUTH_ID" ]; then
    # 注意：如果授权资源被其他 Agent 使用，脚本内部会返回错误响应
    bash "$SCRIPT_DIR/delete_authorization.sh" "$LARK_AUTH_ID"
else
    echo "⏭️  跳过授权资源删除: 未在 .env 中找到 LARK_AUTH_ID"
fi

# 4. 删除 Vertex AI Reasoning Engine
if [ -n "$VERTEX_REASONING_ENGINE_NAME" ]; then
    bash "$SCRIPT_DIR/delete_reasoning_engine.sh" "$VERTEX_REASONING_ENGINE_NAME"
else
    echo "⏭️  跳过 Reasoning Engine 删除: 未在 .deploy_env 中找到 VERTEX_REASONING_ENGINE_NAME"
fi

# 5. 清理本地部署记录
echo "------------------------------------------"
read -p "是否同步清理本地 .deploy_env 记录? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -f "$ROOT_DIR/.deploy_env" ]; then
        # 仅保留空文件或删除特定键值，这里采取清空策略但保留文件
        > "$ROOT_DIR/.deploy_env"
        echo "✨ .deploy_env 已清空"
    fi
fi

echo "=========================================="
echo "🎉 清理流程结束"
echo "=========================================="
