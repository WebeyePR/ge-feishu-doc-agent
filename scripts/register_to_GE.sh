#!/bin/bash
set -e

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

assert_no_api_error() {
    local response="$1"
    local context="$2"
    local allow_exists="${3:-false}"
    RESPONSE="$response" CONTEXT="$context" ALLOW_EXISTS="$allow_exists" python3 - <<'PY'
import json
import os
import sys

raw = os.environ["RESPONSE"]
context = os.environ["CONTEXT"]
allow_exists = os.environ.get("ALLOW_EXISTS") == "true"
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(0)

if "error" not in data:
    sys.exit(0)

error = data["error"]
if allow_exists and error.get("status") == "ALREADY_EXISTS":
    print(
        f"提示: {context} 已存在，继续复用该 Authorization Resource。"
    )
    sys.exit(0)

print(f"错误: {context} 失败。", file=sys.stderr)
print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
message = error.get("message", "")
if "used by another agent" in message:
    print(
        "提示: Authorization Resource 已被其他 Agent 绑定。请更换对应 AUTH_ID，"
        "或先删除使用它的旧 Agent。",
        file=sys.stderr,
    )
if error.get("status") == "ALREADY_EXISTS":
    print(
        "提示: Authorization Resource ID 已存在。GE 的授权资源建议按 Agent/环境使用唯一 ID。",
        file=sys.stderr,
    )
sys.exit(1)
PY
}

# 获取项目编号，用于资源名称引用
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

AUTH_RESOURCES=("\"projects/$PROJECT_NUMBER/locations/${GE_APP_LOCATION}/authorizations/$LARK_AUTH_ID\"")

echo "1. 正在创建 OAuth 授权资源: $LARK_AUTH_ID ..."
LARK_AUTH_RESPONSE=$(curl -s -X POST \
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
   }")
echo "$LARK_AUTH_RESPONSE"
assert_no_api_error "$LARK_AUTH_RESPONSE" "创建飞书 OAuth 授权资源" "true"

if [ -n "$GOOGLE_WORKSPACE_CLIENT_ID" ] || [ -n "$GOOGLE_WORKSPACE_CLIENT_SECRET" ] || [ -n "$GOOGLE_WORKSPACE_AUTHORIZATION_URI" ] || [ -n "$GOOGLE_WORKSPACE_TOKEN_URI" ]; then
    if [ -z "$GOOGLE_WORKSPACE_AUTH_ID" ] || [ -z "$GOOGLE_WORKSPACE_CLIENT_ID" ] || [ -z "$GOOGLE_WORKSPACE_CLIENT_SECRET" ] || [ -z "$GOOGLE_WORKSPACE_AUTHORIZATION_URI" ] || [ -z "$GOOGLE_WORKSPACE_TOKEN_URI" ]; then
        echo "错误: Google Workspace OAuth 配置不完整。请配置 GOOGLE_WORKSPACE_AUTH_ID / CLIENT_ID / CLIENT_SECRET / AUTHORIZATION_URI / TOKEN_URI。"
        exit 1
    fi

    GWS_AUTH_PAYLOAD_FILE=$(mktemp)
    ROOT_DIR="$ROOT_DIR" \
    PROJECT_NUMBER="$PROJECT_NUMBER" \
    GE_APP_LOCATION="$GE_APP_LOCATION" \
    GOOGLE_WORKSPACE_AUTH_ID="$GOOGLE_WORKSPACE_AUTH_ID" \
    GOOGLE_WORKSPACE_CLIENT_ID="$GOOGLE_WORKSPACE_CLIENT_ID" \
    GOOGLE_WORKSPACE_CLIENT_SECRET="$GOOGLE_WORKSPACE_CLIENT_SECRET" \
    GOOGLE_WORKSPACE_AUTHORIZATION_URI="$GOOGLE_WORKSPACE_AUTHORIZATION_URI" \
    GOOGLE_WORKSPACE_TOKEN_URI="$GOOGLE_WORKSPACE_TOKEN_URI" \
    uv run python - <<'PY' > "$GWS_AUTH_PAYLOAD_FILE"
import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

root_dir = os.environ["ROOT_DIR"]
with open(os.path.join(root_dir, "scripts", "google_workspace_scopes.json"), encoding="utf-8") as f:
    scopes = json.load(f)["scopes"]

authorization_uri = os.environ["GOOGLE_WORKSPACE_AUTHORIZATION_URI"]
parts = urlsplit(authorization_uri)
query = parse_qsl(parts.query, keep_blank_values=True)
query_keys = {key for key, _ in query}
if "response_type" not in query_keys:
    query.insert(0, ("response_type", "code"))
authorization_uri = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

payload = {
    "name": (
        f"projects/{os.environ['PROJECT_NUMBER']}/locations/"
        f"{os.environ['GE_APP_LOCATION']}/authorizations/{os.environ['GOOGLE_WORKSPACE_AUTH_ID']}"
    ),
    "serverSideOauth2": {
        "clientId": os.environ["GOOGLE_WORKSPACE_CLIENT_ID"],
        "clientSecret": os.environ["GOOGLE_WORKSPACE_CLIENT_SECRET"],
        "authorizationUri": authorization_uri,
        "tokenUri": os.environ["GOOGLE_WORKSPACE_TOKEN_URI"],
        "scopes": scopes,
    },
}

print(json.dumps(payload, ensure_ascii=False))
PY

    echo -e "\n\n1b. 正在创建 Google Workspace OAuth 授权资源: $GOOGLE_WORKSPACE_AUTH_ID ..."
    GWS_AUTH_RESPONSE=$(curl -s -X POST \
       -H "Authorization: Bearer $(gcloud auth print-access-token)" \
       -H "Content-Type: application/json" \
       -H "X-Goog-User-Project: $PROJECT_ID" \
       "https://${GE_APP_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/$PROJECT_ID/locations/${GE_APP_LOCATION}/authorizations?authorizationId=$GOOGLE_WORKSPACE_AUTH_ID" \
       --data-binary "@$GWS_AUTH_PAYLOAD_FILE")
    echo "$GWS_AUTH_RESPONSE"
    assert_no_api_error "$GWS_AUTH_RESPONSE" "创建 Google Workspace OAuth 授权资源" "true"
    rm -f "$GWS_AUTH_PAYLOAD_FILE"

    AUTH_RESOURCES+=("\"projects/$PROJECT_NUMBER/locations/${GE_APP_LOCATION}/authorizations/$GOOGLE_WORKSPACE_AUTH_ID\"")
fi

AUTH_RESOURCES_JSON=$(IFS=,; echo "${AUTH_RESOURCES[*]}")

echo -e "\n\n2. 正在将 Agent 注册到 Gemini Enterprise ..."
RESPONSE=$(curl -s -X POST \
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
  $AUTH_RESOURCES_JSON
]
}}")

echo -e "\n响应结果: $RESPONSE"
assert_no_api_error "$RESPONSE" "注册 Agent 到 Gemini Enterprise"

# 尝试提取资源名并回写到 .deploy_env
AGENT_NAME=$(echo $RESPONSE | grep -o '"name": *"[^"]*"' | head -1 | cut -d'"' -f4)

if [ -n "$AGENT_NAME" ] && [[ "$AGENT_NAME" == *"agents/"* ]]; then
    echo -e "\n注册成功！Agent 资源名: $AGENT_NAME"
    uv run python -c "import dotenv; dotenv.set_key('$ROOT_DIR/.deploy_env', 'GE_AGENT_RESOURCE_NAME', '$AGENT_NAME', quote_mode='always')"
    echo "已同步 GE_AGENT_RESOURCE_NAME 到 .deploy_env"
else
    echo -e "\n警告: 未能从响应中识别出 Agent 资源名，请检查输出。"
fi
