#!/bin/bash

# 列出所有已部署的 Agent 和授权资源
# 使用方法: ./list_all_resources.sh

# 加载配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load_env.sh"

# 默认值
GE_APP_LOCATION="${GE_APP_LOCATION:-global}"

echo "=========================================="
echo "=== 1. 所有 Agent 列表 ==="
echo "=========================================="

agents_response=$(curl -s -X GET \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/collections/default_collection/engines/${GE_APP_ID}/assistants/default_assistant/agents")

echo "$agents_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
agents = data.get('agents', [])
print(f'总共找到 {len(agents)} 个 Agent:\n')
for i, agent in enumerate(agents, 1):
    name = agent.get('name', 'N/A')
    display_name = agent.get('displayName', 'N/A')
    reasoning_engine = agent.get('adkAgentDefinition', {}).get('provisionedReasoningEngine', {}).get('reasoningEngine', 'N/A')
    auth_configs = agent.get('authorizationConfig', {}).get('toolAuthorizations', [])
    state = agent.get('state', 'N/A')
    create_time = agent.get('createTime', 'N/A')
    
    print(f'{i}. {display_name}')
    print(f'   Name: {name}')
    print(f'   State: {state}')
    print(f'   Reasoning Engine: {reasoning_engine}')
    print(f'   Authorizations: {len(auth_configs)} 个')
    for auth in auth_configs:
        print(f'     - {auth}')
    print(f'   Created: {create_time}')
    print()
"

echo ""
echo "=========================================="
echo "=== 2. 所有授权资源列表 ==="
echo "=========================================="

auths_response=$(curl -s -X GET \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/authorizations")

echo "$auths_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
auths = data.get('authorizations', [])
print(f'总共找到 {len(auths)} 个授权资源:\n')
for i, auth in enumerate(auths, 1):
    name = auth.get('name', 'N/A')
    auth_id = name.split('/')[-1] if '/' in name else name
    client_id = auth.get('serverSideOauth2', {}).get('clientId', 'N/A')
    
    print(f'{i}. {auth_id}')
    print(f'   Name: {name}')
    print(f'   Client ID: {client_id}')
    print()
"

echo ""
echo "=========================================="
echo "=== 3. Reasoning Engine 列表 ==="
echo "=========================================="

echo "提示: 使用以下命令查看 Reasoning Engine:"
echo "gcloud ai reasoning-engines list --region=${LOCATION} --project=${PROJECT_ID}"

