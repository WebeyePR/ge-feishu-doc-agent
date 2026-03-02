#!/bin/bash

# 默认值
GE_APP_LOCATION="${GE_APP_LOCATION:-global}"

# 加载配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

source "$SCRIPT_DIR/load_env.sh"

# 检查必要参数
if [ -z "$PROJECT_ID" ] || [ -z "$GE_AGENT_RESOURCE_NAME" ] || [ -z "$VERTEX_REASONING_ENGINE_NAME" ] || [ -z "$GE_APP_LOCATION" ]; then
    echo "错误: 缺少必要参数。请确保 .env 和 .deploy_env 已正确配置。"
    exit 1
fi

AGENT_RESOURCE_NAME="$GE_AGENT_RESOURCE_NAME"
NEW_REASONING_ENGINE="$VERTEX_REASONING_ENGINE_NAME"

echo "正在更新 Gemini Enterprise Agent 到新的 Reasoning Engine..."
echo "Target Agent: Lark Document Agent GB Dev"
echo "New Engine: $NEW_REASONING_ENGINE"


# 使用 PATCH 请求更新
curl -X PATCH \
-H "Authorization: Bearer $(gcloud auth print-access-token)" \
-H "Content-Type: application/json" \
-H "X-Goog-User-Project: $PROJECT_ID" \
"https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/$AGENT_RESOURCE_NAME?updateMask=adkAgentDefinition.provisionedReasoningEngine.reasoningEngine" \
-d "{
  \"adkAgentDefinition\": {
    \"provisionedReasoningEngine\": {
      \"reasoningEngine\": \"$NEW_REASONING_ENGINE\"
    }
  }
}"

echo -e "\n\n更新完成！请前往 Gemini Enterprise 界面验证。"
