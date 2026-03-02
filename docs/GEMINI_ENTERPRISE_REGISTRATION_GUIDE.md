# Google Cloud Vertex AI Agent Engine & Gemini Enterprise 通用注册指南

本文档详细说明了如何将基于 Google Agent Development Kit (ADK) 开发的自定义 Agent 部署到 Vertex AI，并将其注册集成到 Gemini Enterprise 平台。

## 📋 目录

1.  [前置要求](#1-前置要求)
2.  [阶段一：配置 OAuth 2.0 认证](#2-阶段一配置-oauth-20-认证)
3.  [阶段二：部署 Agent (Reasoning Engine)](#3-阶段二部署-agent-reasoning-engine)
4.  [阶段三：注册至 Gemini Enterprise](#4-阶段三注册至-gemini-enterprise)

---

## 1. 前置要求

*   **Google Cloud Project**: 已启用 Vertex AI API, Discovery Engine API (Gemini for Google Cloud)。
*   **Python 环境**: Python 3.12+，并安装了 `google-cloud-aiplatform` 及 ADK 相关依赖。
*   **OAuth 提供方**: 你希望 Agent 连接的第三方服务（例如：Jira, Salesforce, HR系统, 自研后端等）必须支持 OAuth 2.0 标准。

---

## 2. 阶段一：配置 OAuth 2.0 认证

为了让 Gemini Enterprise 代表用户安全地访问第三方数据，需要在你的第三方应用平台中配置 OAuth 应用。

1.  **创建应用**: 在你的第三方服务开发者后台创建一个新的 OAuth App。
2.  **获取凭证**: 记录下生成的 `Client ID` (客户端 ID) 和 `Client Secret` (客户端密钥)。
3.  **配置回调地址 (Redirect URI)**:
    这是最关键的一步。必须将 Google 的通用回调地址添加到你的 OAuth 应用白名单中：
    ```text
    https://vertexaisearch.cloud.google.com/oauth-redirect
    ```
4.  **确定权限范围 (Scopes)**:
    确定 Agent 运行所需的最小权限集。
    *   *注意*: 通常需要包含 `offline_access` 权限，以允许 Google 存储 Refresh Token 并在 Access Token 过期时自动刷新。
5.  **记录 API 端点**:
    确认第三方服务的以下端点地址：
    *   **Authorization URI** (授权页面地址)
    *   **Token URI** (换取 Token 地址)

---

## 3. 阶段二：部署 Agent (Vertex AI Agent Engine)

在使用 ADK 开发完 Agent 代码后，需要将其作为 "Reasoning Engine" 部署到 Vertex AI。

1.  **部署**:
    
    部署方式参考官方文档。

    部署成功后，你会获得一个 **Reasoning Engine Resource ID**，格式如下：

    ```text
    projects/<PROJECT_ID>/locations/<LOCATION>/reasoningEngines/<ENGINE_ID>
    ```
    *请记录此 ID，后续注册步骤需要使用。*

---

## 4. 阶段三：注册至 Gemini Enterprise

将部署好的 Reasoning Engine 与 OAuth 配置结合，注册为 Gemini Enterprise 中的可用 Agent。

推荐使用 `curl` 命令行方式进行注册，以便于自动化和精确控制参数。

### 步骤 3.1: 创建授权资源 (Authorization Resource)

此步骤告诉 Google 如何与你的第三方服务进行 OAuth 认证。

*   **AUTH_ID**: 自定义一个授权 ID (例如 `my-custom-agent-auth`)

```bash
curl -X POST \
   -H "Authorization: Bearer $(gcloud auth print-access-token)" \
   -H "Content-Type: application/json" \
   -H "X-Goog-User-Project: <GCP_PROJECT_ID>" \
   "https://global-discoveryengine.googleapis.com/v1alpha/projects/<GCP_PROJECT_ID>/locations/global/authorizations?authorizationId=<AUTH_ID>" \
   -d '{
      "name": "projects/<GCP_PROJECT_ID>/locations/global/authorizations/<AUTH_ID>",
      "serverSideOauth2": {
         "clientId": "<YOUR_CLIENT_ID>",
         "clientSecret": "<YOUR_CLIENT_SECRET>",
         "authorizationUri": "<YOUR_AUTH_URI>?client_id=<YOUR_CLIENT_ID>&response_type=code&scope=<YOUR_SCOPES>",
         "tokenUri": "<YOUR_TOKEN_URI>"
      }
}'
```

**参数说明:**
*   `<GCP_PROJECT_ID>`: 你的 Google Cloud 项目 ID。
*   `<AUTH_ID>`: 你定义的授权资源名称。
*   `<YOUR_CLIENT_ID>` / `<YOUR_CLIENT_SECRET>`: 阶段一获取的凭证。
*   `<YOUR_AUTH_URI>`: 完整的授权 URL，通常需要包含 `scope` (如 `offline_access`) 和 `response_type=code`。
*   `<YOUR_TOKEN_URI>`: 获取 Token 的 API 地址。

### 步骤 3.2: 注册 Agent

将 Reasoning Engine 资源与刚刚创建的授权资源绑定，并连接到 Gemini Enterprise 应用。

```bash
curl -X POST \
-H "Authorization: Bearer $(gcloud auth print-access-token)" \
-H "Content-Type: application/json" \
-H "X-Goog-User-Project: <GCP_PROJECT_ID>" \
"https://discoveryengine.googleapis.com/v1alpha/projects/<GCP_PROJECT_ID>/locations/global/collections/default_collection/engines/<GEMINI_APP_ID>/assistants/default_assistant/agents" \
-d '{
"displayName": "<AGENT_DISPLAY_NAME>",
"description": "<AGENT_DESCRIPTION>",
"adkAgentDefinition": {
    "provisionedReasoningEngine": {
        "reasoningEngine": "projects/<GCP_PROJECT_ID>/locations/<LOCATION>/reasoningEngines/<ENGINE_ID>"
    }
},
"authorizationConfig": {
    "toolAuthorizations": [
        "projects/<GCP_PROJECT_ID>/locations/global/authorizations/<AUTH_ID>"
    ]
}}'
```

**参数说明:**
*   `<GEMINI_APP_ID>`: 你的 Gemini Enterprise (Discovery Engine) 应用 ID。
*   `<AGENT_DISPLAY_NAME>`: 在 Gemini 界面上显示的 Agent 名称。
*   `<ENGINE_ID>`: 阶段二中部署获得的 Reasoning Engine ID。
*   `<AUTH_ID>`: 步骤 3.1 中创建的授权 ID。

---

## ✅ 验证

完成上述步骤后：

1.  进入 **Google Cloud Console** -> **Gemini Enterprise**。
2.  你应该能看到新注册的 Agent。
3.  在预览界面向 Agent 提问，系统应会提示你“登录”第三方服务。
4.  点击登录，完成 OAuth 流程后，Agent 即可代表你执行工具调用。
5. 开发agent tools时，**用户access_token获取方式**：`tool_context.state[f"{LARK_AUTH_ID}"]` 的代码。

