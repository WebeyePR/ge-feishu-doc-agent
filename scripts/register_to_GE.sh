#!/bin/bash

# 默认值
GE_APP_LOCATION="${GE_APP_LOCATION:-global}"

# 加载配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

AGENT_DISPLAY_NAME="${AGENT_DISPLAY_NAME:-Lark Document Agent}"
AGENT_DESCRIPTION="${AGENT_DESCRIPTION:-Lark Document Agent}"

source "$SCRIPT_DIR/load_env.sh"

# 检查必要参数
if [ -z "$PROJECT_ID" ] || [ -z "$LARK_AUTH_ID" ] || [ -z "$LARK_CLIENT_ID" ] || [ -z "$VERTEX_REASONING_ENGINE_NAME" ] || [ -z "$GE_APP_ID" ] || [ -z "$GE_APP_LOCATION" ]; then
    echo "错误: 缺少必要参数。请确保 .env 和 .deploy_env 已正确配置。"
    exit 1
fi

# 获取项目编号，用于资源名称引用
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

echo "1. 正在创建 OAuth 授权资源: $LARK_AUTH_ID ..."
curl -X POST \
   -H "Authorization: Bearer $(gcloud auth print-access-token)" \
   -H "Content-Type: application/json" \
   -H "X-Goog-User-Project: $PROJECT_ID" \
   "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/$PROJECT_ID/locations/${GE_APP_LOCATION}/authorizations?authorizationId=$LARK_AUTH_ID" \
   -d "{
      \"name\": \"projects/$PROJECT_NUMBER/locations/${GE_APP_LOCATION}/authorizations/$LARK_AUTH_ID\",
      \"serverSideOauth2\": {
         \"clientId\": \"$LARK_CLIENT_ID\",
         \"clientSecret\": \"$LARK_CLIENT_SECRET\",
         \"authorizationUri\": \"$LARK_AUTHORIZATION_URI\",
         \"tokenUri\": \"$LARK_TOKEN_URI\"
      }
   }"

echo -e "\n\n2. 正在将 Agent 注册到 Gemini Enterprise ..."
RESPONSE=$(curl -X POST \
-H "Authorization: Bearer $(gcloud auth print-access-token)" \
-H "Content-Type: application/json" \
-H "X-Goog-User-Project: $PROJECT_ID" \
"https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/$PROJECT_ID/locations/${GE_APP_LOCATION}/collections/default_collection/engines/$GE_APP_ID/assistants/default_assistant/agents" \
-d "{
\"displayName\": \"$AGENT_DISPLAY_NAME\",
\"description\": \"$AGENT_DESCRIPTION\",
\"adkAgentDefinition\": {
\"provisionedReasoningEngine\": {
\"reasoningEngine\": \"$VERTEX_REASONING_ENGINE_NAME\"
}},
\"authorizationConfig\": {
\"toolAuthorizations\": [
  \"projects/$PROJECT_NUMBER/locations/${GE_APP_LOCATION}/authorizations/$LARK_AUTH_ID\"
]
}}")

echo -e "\n响应结果: $RESPONSE"

# 尝试提取资源名并回写到 .deploy_env
AGENT_NAME=$(echo $RESPONSE | grep -o '"name": *"[^"]*"' | head -1 | cut -d'"' -f4)

if [ -n "$AGENT_NAME" ] && [[ "$AGENT_NAME" == *"agents/"* ]]; then
    echo -e "\n注册成功！Agent 资源名: $AGENT_NAME"
    uv run python -c "import dotenv; dotenv.set_key('$ROOT_DIR/.deploy_env', 'GE_AGENT_RESOURCE_NAME', '$AGENT_NAME', quote_mode='always')"
    echo "已同步 GE_AGENT_RESOURCE_NAME 到 .deploy_env"
else
    echo -e "\n警告: 未能从响应中识别出 Agent 资源名，请检查输出。"
fi