#!/bin/bash

# 批量删除 Agent 和授权资源
# 使用方法: ./batch_delete.sh

# 加载配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load_env.sh"

# 默认值
GE_APP_LOCATION="${GE_APP_LOCATION:-global}"

echo "=========================================="
echo "批量删除工具"
echo "=========================================="
echo ""
echo "请选择要删除的资源类型:"
echo "1. 删除 Agent"
echo "2. 删除授权资源"
echo "3. 删除未使用的授权资源（没有被任何 Agent 使用）"
echo "4. 删除所有 Lark Document Agent（保留其他）"
echo "5. 退出"
echo ""
read -p "请选择 (1-5): " choice

case $choice in
    1)
        echo ""
        echo "请输入要删除的 Agent Name（完整路径）:"
        echo "示例: projects/839062387451/locations/global/collections/default_collection/engines/webeye-agentspace-app_1742521319182/assistants/default_assistant/agents/123456789"
        read -p "Agent Name: " agent_name
        
        if [ -z "$agent_name" ]; then
            echo "错误: Agent Name 不能为空"
            exit 1
        fi
        
        echo "正在删除 Agent..."
        response=$(curl -X DELETE \
          -H "Authorization: Bearer $(gcloud auth print-access-token)" \
          -H "Content-Type: application/json" \
          -H "X-Goog-User-Project: ${PROJECT_ID}" \
          "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/${agent_name}" \
          -w "\nHTTP_STATUS:%{http_code}" \
          2>/dev/null)
        
        http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
        if [ "$http_status" = "200" ] || [ "$http_status" = "204" ]; then
            echo "✅ Agent 删除成功"
        else
            echo "❌ 删除失败 (HTTP $http_status)"
            echo "$response" | sed '/HTTP_STATUS/d'
        fi
        ;;
        
    2)
        echo ""
        echo "请输入要删除的授权资源 ID:"
        echo "示例: lark-agent-oauth-id"
        read -p "Auth ID: " auth_id
        
        if [ -z "$auth_id" ]; then
            echo "错误: Auth ID 不能为空"
            exit 1
        fi
        
        echo "正在删除授权资源..."
        response=$(curl -X DELETE \
          -H "Authorization: Bearer $(gcloud auth print-access-token)" \
          -H "Content-Type: application/json" \
          -H "X-Goog-User-Project: ${PROJECT_ID}" \
          "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/authorizations/${auth_id}" \
          -w "\nHTTP_STATUS:%{http_code}" \
          2>/dev/null)
        
        http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
        if [ "$http_status" = "200" ] || [ "$http_status" = "204" ]; then
            echo "✅ 授权资源删除成功"
        else
            body=$(echo "$response" | sed '/HTTP_STATUS/d')
            echo "❌ 删除失败 (HTTP $http_status)"
            echo "$body"
        fi
        ;;
        
    3)
        echo ""
        echo "正在查找未使用的授权资源..."
        
        # 获取所有 Agent 使用的授权资源
        agents_response=$(curl -s -X GET \
          -H "Authorization: Bearer $(gcloud auth print-access-token)" \
          -H "Content-Type: application/json" \
          -H "X-Goog-User-Project: ${PROJECT_ID}" \
          "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/collections/default_collection/engines/${GE_APP_ID}/assistants/default_assistant/agents")
        
        used_auths=$(echo "$agents_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
used = set()
for agent in data.get('agents', []):
    auths = agent.get('authorizationConfig', {}).get('toolAuthorizations', [])
    for auth in auths:
        auth_id = auth.split('/')[-1]
        used.add(auth_id)
for auth_id in sorted(used):
    print(auth_id)
")
        
        # 获取所有授权资源
        auths_response=$(curl -s -X GET \
          -H "Authorization: Bearer $(gcloud auth print-access-token)" \
          -H "Content-Type: application/json" \
          -H "X-Goog-User-Project: ${PROJECT_ID}" \
          "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/authorizations")
        
        unused_auths=$(EXPORTED_USED_AUTHS="$used_auths" echo "$auths_response" | python3 -c "
import sys, json, os
used_auths_list = os.environ.get('EXPORTED_USED_AUTHS', '').splitlines()
used_auths_set = set(line.strip() for line in used_auths_list if line.strip())

try:
    data = json.load(sys.stdin)
    unused = []
    for auth in data.get('authorizations', []):
        name = auth.get('name', '')
        auth_id = name.split('/')[-1] if '/' in name else name
        if auth_id not in used_auths_set:
            unused.append(auth_id)

    for auth_id in unused:
        print(auth_id)
except Exception:
    pass
")
        
        if [ -z "$unused_auths" ]; then
            echo "✅ 没有未使用的授权资源"
        else
            echo "找到以下未使用的授权资源:"
            echo "$unused_auths"
            echo ""
            read -p "确认删除这些授权资源? (y/N): " confirm
            if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
                for auth_id in $unused_auths; do
                    echo "正在删除: $auth_id"
                    curl -X DELETE \
                      -H "Authorization: Bearer $(gcloud auth print-access-token)" \
                      -H "Content-Type: application/json" \
                      -H "X-Goog-User-Project: ${PROJECT_ID}" \
                      "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/authorizations/${auth_id}" \
                      -w "\nHTTP_STATUS:%{http_code}" \
                      -o /dev/null -s 2>/dev/null
                    sleep 1
                done
                echo "✅ 删除完成"
            fi
        fi
        ;;
        
    4)
        echo ""
        echo "警告: 这将删除所有名称包含 'Lark Document Agent' 的 Agent"
        read -p "确认删除? (y/N): " confirm
        if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
            agents_response=$(curl -s -X GET \
              -H "Authorization: Bearer $(gcloud auth print-access-token)" \
              -H "Content-Type: application/json" \
              -H "X-Goog-User-Project: ${PROJECT_ID}" \
              "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_APP_LOCATION}/collections/default_collection/engines/${GE_APP_ID}/assistants/default_assistant/agents")
            
            lark_agents=$(echo "$agents_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for agent in data.get('agents', []):
    display_name = agent.get('displayName', '')
    if 'Lark Document Agent' in display_name:
        print(agent.get('name', ''))
")
            
            for agent_name in $lark_agents; do
                echo "正在删除: $agent_name"
                curl -X DELETE \
                  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
                  -H "Content-Type: application/json" \
                  -H "X-Goog-User-Project: ${PROJECT_ID}" \
                  "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/${agent_name}" \
                  -w "\nHTTP_STATUS:%{http_code}" \
                  -o /dev/null -s 2>/dev/null
                sleep 1
            done
            echo "✅ 删除完成"
        fi
        ;;
        
    5)
        echo "退出"
        exit 0
        ;;
        
    *)
        echo "无效的选择"
        exit 1
        ;;
esac

