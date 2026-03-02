#!/bin/bash

# 删除 Gemini Enterprise Agent
# 使用方法: ./delete_agent.sh <AGENT_NAME>

# 加载配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load_env.sh"

AGENT_NAME="${1:-$GE_AGENT_RESOURCE_NAME}"

# 默认值
GE_APP_LOCATION="${GE_APP_LOCATION:-global}"
if [ -z "$AGENT_NAME" ]; then
    echo "使用方法: $0 <AGENT_NAME>"
    echo "示例: $0 projects/839062387451/locations/global/collections/default_collection/engines/webeye-agentspace-app_1742521319182/assistants/default_assistant/agents/123456789"
    echo ""
    echo "提示: Agent NAME 可以从查询结果中获取"
    exit 1
fi

echo "正在删除 Agent: $AGENT_NAME"

response=$(curl -X DELETE \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/${AGENT_NAME}" \
  -w "\nHTTP_STATUS:%{http_code}" \
  2>/dev/null)

http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
body=$(echo "$response" | sed '/HTTP_STATUS/d')

if [ "$http_status" = "200" ] || [ "$http_status" = "204" ]; then
    echo "✅ Agent 删除成功"
    echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
elif [ "$http_status" = "404" ]; then
    echo "✅ Agent 已不存在 (404)"
else
    echo "❌ 删除失败 (HTTP $http_status)"
    echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
fi

