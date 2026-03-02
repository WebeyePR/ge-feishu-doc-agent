#!/bin/bash

# 删除 Vertex AI Reasoning Engine
# 使用方法: ./delete_reasoning_engine.sh <REASONING_ENGINE_NAME>

# 加载配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load_env.sh"

RE_NAME="${1:-$VERTEX_REASONING_ENGINE_NAME}"

if [ -z "$RE_NAME" ]; then
    echo "使用方法: $0 <REASONING_ENGINE_NAME>"
    echo "示例: $0 projects/PROJECT_ID/locations/us-central1/reasoningEngines/123456789"
    exit 1
fi

echo "正在删除 Reasoning Engine: $RE_NAME"

# 尝试提取 location
RE_LOCATION=$(echo "$RE_NAME" | cut -d/ -f4)
RE_LOCATION="${RE_LOCATION:-us-central1}"

# 1. 优先尝试 gcloud
# 注意：某些版本的 gcloud 可能不支持 reasoning-engines 子命令
gcloud ai reasoning-engines delete "$RE_NAME" --quiet 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ Reasoning Engine 删除成功 (通过 gcloud)"
    exit 0
fi

# 2. 如果 gcloud 不可用或失败，fallback 到 API 直接调用
echo "🔄 gcloud 命令不可用或失败，尝试通过 API 接口直接删除..."
TOKEN=$(gcloud auth print-access-token)

response=$(curl -s -X DELETE \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    "https://${RE_LOCATION}-aiplatform.googleapis.com/v1/${RE_NAME}" \
    -w "\nHTTP_STATUS:%{http_code}")

http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
body=$(echo "$response" | sed '/HTTP_STATUS/d')

if [ "$http_status" = "200" ] || [ "$http_status" = "204" ]; then
    echo "✅ Reasoning Engine 删除成功"
elif [ "$http_status" = "404" ]; then
    echo "✅ Reasoning Engine 已不存在 (404)"
else
    echo "❌ 删除失败 (HTTP $http_status)"
    echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
    exit 1
fi
