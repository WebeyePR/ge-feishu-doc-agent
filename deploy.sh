#!/bin/bash

# ==============================================================================
# Lark Agent 一键部署脚本 (Using UV and Google Cloud CLI)
# ==============================================================================

# 设置错误即停止
set -e

# 1. 检查环境与配置
if [ ! -f ".env" ]; then
    echo "错误: 未找到 .env 文件。请参考 .env.example 创建并填写配置。"
    exit 1
fi

# 设置部署目录
DEPLOY_DIR=lark_agent/deployement
# 设置 Python 路径
export PYTHONPATH=$(pwd)

echo "--- 加载配置文件 ---"
source scripts/load_env.sh

echo "--- 初始化项目 ---"
bash scripts/init.sh

echo "--- 检查必要工具和配置 ---"
# 检查必要工具
command -v gcloud >/dev/null 2>&1 || { echo "错误: 未安装 gcloud CLI。"; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "错误: 未安装 uv。建议访问 https://docs.astral.sh/uv/ 安装。"; exit 1; }

if [ -z "$PROJECT_ID" ]; then
    echo "错误: 未设置 PROJECT_ID。请在 .env 文件中配置。"
    exit 1
fi

if [ -z "$LOCATION" ]; then
    echo "错误: 未设置 LOCATION。请在 .env 文件中配置。"
    exit 1
fi

if [ -z "$STAGING_BUCKET" ]; then
    echo "错误: 未设置 STAGING_BUCKET。请在 .env 文件中配置。"
    exit 1
fi

if [ -z "$AGENT_DISPLAY_NAME" ]; then
    echo "错误: 未设置 AGENT_DISPLAY_NAME。请在 .env 文件中配置。"
    exit 1
fi

if [ -z "$GE_APP_ID" ]; then
    echo "错误: 未设置 GE_APP_ID。请在 .env 文件中配置。"
    exit 1
fi

if [ -z "$GE_APP_LOCATION" ]; then
    echo "错误: 未设置 GE_APP_LOCATION。请在 .env 文件中配置。"
    exit 1
fi


echo "--- [1/4] 正在使用 UV 打包应用 ---"
# 清理旧的构建文件
rm -rf dist/ build/ *.egg-info
rm -f $DEPLOY_DIR/adk_agents-0.1.0-py3-none-any.whl
# uv build 会自动处理构建依赖
uv build --wheel --out-dir $DEPLOY_DIR

echo "--- [2/4] 正在部署到 Vertex AI Reasoning Engine ---"
cd $DEPLOY_DIR
# uv run 会自动管理虚拟环境并执行部署脚本
uv run python -m deploy
cd -

# 重新加载部署生成的变量
source scripts/load_env.sh
if [ -z "$GE_AGENT_RESOURCE_NAME" ] && [ ! -f ".deploy_env" ]; then
    echo "错误: 部署失败，未能生成 .deploy_env。"
    exit 1
fi

echo "--- [3/4] 正在同步到 Gemini Enterprise ---"
if [ -z "$GE_AGENT_RESOURCE_NAME" ]; then
    echo "识别到首次部署，正在执行注册流程..."
    bash "scripts/register_to_GE.sh"
else
    echo "识别到更新部署，正在更新 Agent 引擎..."
    bash "scripts/update_agent_GE.sh"
fi

# 重新加载部署生成的变量 (包含刚刚生成的 GE_AGENT_RESOURCE_NAME)
source scripts/load_env.sh

echo "--- [4/4] 部署完成！ ---"
echo "Reasoning Engine ID: $VERTEX_REASONING_ENGINE_NAME"
if [ -n "$GE_AGENT_RESOURCE_NAME" ]; then
    echo "Agent Resource Name: $GE_AGENT_RESOURCE_NAME"
    
    # 拼接控制台 URL
    AGENT_ID=$(basename "$GE_AGENT_RESOURCE_NAME")
    RE_ID=$(basename "$VERTEX_REASONING_ENGINE_NAME")
    # 提取 Reasoning Engine 的区域 (通常是 us-central1 等)
    RE_REGION=$(echo "$VERTEX_REASONING_ENGINE_NAME" | cut -d/ -f4)
    echo ""
    
    echo -e "\n--- 控制台快捷访问地址 ---"
    # 使用正确的变量和格式
    echo "Gemini Enterprise App: https://console.cloud.google.com/gemini-enterprise/locations/${GE_APP_LOCATION}/engines/${GE_APP_ID}/overview/dashboard?project=${PROJECT_ID}"
    echo ""
    # Reasoning Engine (Vertex AI)
    echo "Reasoning Engine (Vertex AI): https://console.cloud.google.com/vertex-ai/agents/agent-engines/locations/${RE_REGION}/agent-engines/${RE_ID}/dashboard?project=${PROJECT_ID}"
    echo -e "\n您可以点击上方链接前往 Gemini Enterprise 控制台体验部署的 Agent。"
else
    echo -e "\n警告: 未检测到 GE_AGENT_RESOURCE_NAME。这可能意味着 Agent 注册失败，或者环境变量未正确同步。"
    echo "请检查 .deploy_env 文件并确保 Agent 资源已正确创建。"
    
    # 即使 Agent 注册有问题，通常 Reasoning Engine 已经好了，可以单独打出它的链接作为备选
    if [ -n "$VERTEX_REASONING_ENGINE_NAME" ]; then
        RE_ID=$(basename "$VERTEX_REASONING_ENGINE_NAME")
        RE_REGION=$(echo "$VERTEX_REASONING_ENGINE_NAME" | cut -d/ -f4)
        echo -e "\n--- Reasoning Engine (Vertex AI) 快捷访问地址 ---"
        echo "https://console.cloud.google.com/vertex-ai/agents/agent-engines/locations/${RE_REGION}/agent-engines/${RE_ID}/dashboard?project=${PROJECT_ID}"
    fi
fi
echo ""
