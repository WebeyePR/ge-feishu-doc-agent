#!/bin/bash

# 删除 Gemini Enterprise 授权资源
# 使用方法: ./delete_authorization.sh <AUTH_ID>

# 加载配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load_env.sh"

AUTH_ID="${1:-$LARK_AUTH_ID}"

if [ -z "$AUTH_ID" ]; then
    echo "使用方法: $0 <AUTH_ID>"
    echo "示例: $0 lark-agent-oauth-id"
    echo ""
    echo "当前将删除: $AUTH_ID"
    read -p "确认删除? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "已取消"
        exit 0
    fi
fi

echo "正在删除授权资源: $AUTH_ID"

response=$(curl -X DELETE \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://global-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/authorizations/${AUTH_ID}" \
  -w "\nHTTP_STATUS:%{http_code}" \
  2>/dev/null)

http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
body=$(echo "$response" | sed '/HTTP_STATUS/d')

if [ "$http_status" = "200" ] || [ "$http_status" = "204" ]; then
    echo "✅ 授权资源删除成功"
    echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
elif [ "$http_status" = "404" ]; then
    echo "✅ 授权资源已不存在 (404)"
else
    echo "❌ 删除失败 (HTTP $http_status)"
    echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
fi

