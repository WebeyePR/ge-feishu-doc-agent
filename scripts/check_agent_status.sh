#!/bin/bash

# 检查 Gemini Enterprise Agent 状态和授权配置
# 使用方法: ./check_agent_status.sh

# 加载配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load_env.sh"

# 默认值
GE_APP_LOCATION="${GE_APP_LOCATION:-global}"

echo "=== 1. 查询 Agent 列表 ==="
curl -X GET \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/collections/default_collection/engines/${GE_APP_ID}/assistants/default_assistant/agents" \
  | jq '.'

echo ""
echo "=== 2. 查询授权资源列表 ==="
curl -X GET \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/authorizations" \
  | jq '.'

echo ""
echo "=== 3. 查询特定授权资源详情 ==="
echo "Auth ID: $LARK_AUTH_ID"
curl -X GET \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/authorizations/${LARK_AUTH_ID}" \
  | jq '.'

