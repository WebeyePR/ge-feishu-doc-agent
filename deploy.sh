#!/bin/bash

# ==============================================================================
# Lark Agent 一键部署脚本 (Using UV and Google Cloud CLI)
# ==============================================================================

# 设置错误即停止
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 1. 检查环境与配置
ENV_SUFFIX="$1"
TARGET_ENV=".env"
if [ -n "$ENV_SUFFIX" ]; then
    TARGET_ENV=".env-${ENV_SUFFIX}"
fi

if [ ! -f "$TARGET_ENV" ]; then
    echo -e "${RED}错误: 未找到 ${TARGET_ENV} 配置文件。请创建并填写配置。${NC}"
    exit 1
fi

# 设置部署目录
DEPLOY_DIR=nexus_agent/deployement
# 设置 Python 路径
export PYTHONPATH=$(pwd)

echo "--- 加载配置文件 ---"
source scripts/load_env.sh "$ENV_SUFFIX"

echo -e "\n=================================================================="
echo -e "🔮  ${GREEN}WebEye Nexus Agent 部署配置看板${NC}"
echo -e "=================================================================="
echo -e "- 环境后缀 (ENV_SUFFIX):  ${BLUE}${ENV_SUFFIX:-无 (使用默认环境 .env)}${NC}"
echo -e "- 基础配置文件 (.env):     ${YELLOW}${BASE_ENV_FILE}${NC}"
echo -e "- 状态部署文件 (.deploy_env): ${YELLOW}${DEPLOY_ENV_FILE}${NC}"
echo -e "- 目标 GCP 项目 (PROJECT_ID): ${GREEN}${PROJECT_ID}${NC}"
echo -e "- 目标智能体名称 (DISPLAY_NAME): ${GREEN}${AGENT_DISPLAY_NAME}${NC}"
echo -e "- 飞书授权 ID (LARK_AUTH_ID): ${GREEN}${LARK_AUTH_ID}${NC}"
if [ -n "$GOOGLE_WORKSPACE_AUTH_ID" ]; then
echo -e "- GWS 授权 ID (GWS_AUTH_ID): ${GREEN}${GOOGLE_WORKSPACE_AUTH_ID}${NC}"
fi
if [ -n "$GE_AGENT_RESOURCE_NAME" ]; then
echo -e "- 部署模式:                ${YELLOW}更新部署 (Update Agent Engine)${NC}"
echo -e "- 已注册 GE Agent 资源:     ${YELLOW}${GE_AGENT_RESOURCE_NAME}${NC}"
else
echo -e "- 部署模式:                ${GREEN}首次部署 (Register New Agent)${NC}"
fi
echo -e "=================================================================="
echo -e "请在 5 秒内确认以上信息是否正确，按 ${RED}Ctrl+C${NC} 可安全取消部署..."
for i in {5..1}; do
    echo -ne "倒计时: ${YELLOW}$i${NC} 秒...\r"
    sleep 1
done
echo -e "\n==================================================================\n"

# 保存旧的 Reasoning Engine 资源名称以供后续清理
PREVIOUS_REASONING_ENGINE="$VERTEX_REASONING_ENGINE_NAME"

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

echo "--- [0/4] 正在检查二进制文件(Lark CLI) ---"
if [ ! -f "nexus_agent/bin/lark-cli" ]; then
    echo "未发现 CLI 二进制文件，正在开始构建..."
    bash scripts/build_cli.sh
else
    echo "CLI 二进制文件已存在，跳过构建。"
fi

echo "--- [0/4] 正在检查二进制文件(Google Workspace CLI) ---"
if [ ! -f "nexus_agent/bin/gws" ]; then
    echo "错误: 未发现 Google Workspace CLI 二进制文件: nexus_agent/bin/gws"
    echo "请从 googleworkspace/cli release 下载 Linux amd64 版本并放入 nexus_agent/bin/gws。"
    exit 1
fi

echo "--- [1/4] 正在使用 UV 打包应用 ---"
# 清理旧的构建文件
rm -rf dist/ build/ *.egg-info
rm -f $DEPLOY_DIR/ge_nexus_agent-1.0.0-py3-none-any.whl
# uv build 会自动处理构建依赖
uv build --wheel --out-dir $DEPLOY_DIR

echo "--- [2/4] 正在部署到 Vertex AI Reasoning Engine ---"
# 在根目录运行部署脚本，确保模块导入 and PYTHONPATH 正确
uv run python nexus_agent/deployement/deploy.py

# 重新加载部署生成的变量
source scripts/load_env.sh "$ENV_SUFFIX"
if [ -z "$GE_AGENT_RESOURCE_NAME" ] && [ ! -f "$DEPLOY_ENV_FILE" ]; then
    echo "错误: 部署失败，未能生成 $DEPLOY_ENV_FILE。"
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
source scripts/load_env.sh "$ENV_SUFFIX"

# 清理旧的 Reasoning Engine 资源 (避免云端资源累积和持续计费)
if [ -n "$PREVIOUS_REASONING_ENGINE" ] && [ "$PREVIOUS_REASONING_ENGINE" != "$VERTEX_REASONING_ENGINE_NAME" ]; then
    echo "--- 正在清理历史 Reasoning Engine 资源 ---"
    echo "检测到旧的 Reasoning Engine: $PREVIOUS_REASONING_ENGINE"
    echo "正在安全删除旧实例以节省云端资源..."
    
    # 调用项目中已有的删除脚本，并允许忽略失败，确保部署主流程能顺利完成
    bash scripts/delete_reasoning_engine.sh "$PREVIOUS_REASONING_ENGINE" || echo "⚠️ 警告: 删除旧 Reasoning Engine 失败，您后续可以手动运行: bash scripts/delete_reasoning_engine.sh $PREVIOUS_REASONING_ENGINE 进行清理。"
fi

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
