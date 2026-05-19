# Lark Agent (ADK Agent)

这是一个基于 Google Agent Development Kit (ADK) 构建的智能对话助手，旨在帮助企业员工在统一的 AI 交互环境中，通过自然语言对话快速、安全地完成飞书云文档查询、读取、创建、更新以及更多飞书生态操作。

## ✨ 主要功能

*   **文档智能搜索**: 支持通过自然语言关键词搜索飞书云文档，并以 Markdown 超链接形式展示结果。
*   **文档内容问答**: 支持读取飞书文档内容，包含 Markdown 文本与多模态图片内容，并基于文档内容回答问题。
*   **AI 输出写入文档**: 支持将 Agent 生成的 Markdown 内容写入新的飞书云文档，或追加、覆盖更新已有文档，并返回文档链接、任务状态或更新结果。
*   **飞书生态操作扩展**: 内置 `lark-cli` 二进制，可通过 CLI 封装或通用 OpenAPI 工具扩展 Drive、Wiki、Base、Sheets、Calendar、Task、IM 等飞书能力。
*   **安全认证**: 集成 Lark OAuth2.0 认证流程。当检测到用户未授权时，会自动引导用户登录授权（Gemini Enterprise 默认行为）。

## 🎥 使用演示

![img.png](docs/imgs/show_1.png)

![img.png](docs/imgs/show_2.png)

## 🏗️ 架构设计

本项目遵循 Clean Architecture 原则，主要包含以下模块：

*   **lark_agent/**: Agent 的核心逻辑。
    *   `agent.py`: 定义 `LlmAgent` 的角色、Prompt 和工具集。
    *   `tools.py`: 定义供 Agent 调用的工具函数，负责参数校验、授权读取、结果归一化和业务级封装。
    *   `callbacks.py`: 处理多模态响应兼容性等运行时补丁。
    *   `infrastructure/lark_api_repository.py`: 封装飞书 OpenAPI、文档读取和飞书 MCP 网关调用。
    *   `infrastructure/cli_client.py`: 封装随包发布的 `lark-cli` 二进制，使用隔离 `HOME` 和环境变量传递用户态 access token。

系统时序图如下所示：

```mermaid
sequenceDiagram
  autonumber
  participant U as User
  participant G as Gemini Enterprise
  participant AE as AI Agent (ADK/Agent Engine)
  participant AUTH as Authority Module
  participant DB as Firestore
  participant SM as Secret Manager
  participant MCP as Feishu MCP Gateway
  participant CLI as Packaged lark-cli
  participant FS as Feishu/Lark Open Platform

  U->>G: Natural language request (search / read / create / update / operate)
  G->>AE: Invoke Agent tool (with contextual intent)

  rect rgb(245,245,245)
    AE->>AUTH: Request current user's access_token
    AUTH->>DB: Read/Write user token metadata (existence / expiry)
    AUTH->>SM: Read app secrets and sensitive config
    AUTH->>AUTH: If expired, perform OAuth 2.0 refresh / authorization
    AUTH-->>AE: Return valid access_token
  end

  alt Document high-fidelity tools
    AE->>MCP: JSON-RPC tool call (access_token + allowed tool)
    MCP->>FS: Execute official MCP-backed document operation
    FS-->>MCP: Return document result
    MCP-->>AE: Return normalized MCP payload
  else Broad Feishu ecosystem tools
    AE->>CLI: Run packaged lark-cli (access_token + structured args)
    CLI->>FS: Execute CLI/OpenAPI operation
    FS-->>CLI: Return API result
    CLI-->>AE: Return normalized JSON result
  end

  AE-->>G: Organize results (summarization / structuring)
  G-->>U: Present output (text / list / links / export status)
```

## ⚙️ 配置 (.env)

在本地运行或部署前，请在项目根目录创建 `.env` 文件，并修改必填配置：

```env
# --- 飞书 (Lark) 集成配置 ---
# （必填参数）注册 Agent Engine 到 Gemini Enterprise 时设置的 auth_id (唯一资源标识符，自定义)
LARK_AUTH_ID="your-unique-auth-id"  # 如 lark-agent-oauth-id
# （必填参数）飞书开放平台应用 App ID
LARK_CLIENT_ID="your-lark-app-id"
# （必填参数）飞书开放平台应用 App Secret
LARK_CLIENT_SECRET="your-lark-app-secret"


# --- Google Cloud Platform (Vertex AI) 配置 ---
# （必填参数）GCP 项目 ID (Vertex AI 调用及部署时需要)
PROJECT_ID=""  # your-project-id


# --- Gemini Enterprise 配置，查看App: https://console.cloud.google.com/gemini-enterprise/apps ---
# （必填参数）Gemini Enterprise 中对应的 App (Engine) ID
GE_APP_ID=""  # 如 webeye-app_1742521319182
# （必填参数）Gemini Enterprise 数据区域 (如 global, us, eu)
GE_APP_LOCATION="global"  # e.g., global, us, eu


# --- 本地运行配置 ---
# （选填）是否使用 Vertex AI (1) 或 Google AI Studio (0)
GOOGLE_GENAI_USE_VERTEXAI=0
# （选填）Google API Key (仅在 GOOGLE_GENAI_USE_VERTEXAI=0 时需要)
GOOGLE_API_KEY="your-api-key"
```

## 🚀 部署与运行

> **📖 详细部署指南**：请参考 [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) 了解完整的打包部署流程、注意事项和常见问题解决方案。

### 前置要求

*   Python 3.12+
*   Google Cloud Project (启用 Vertex AI, Cloud Run, Firestore API)
*   飞书/Lark 开放平台应用app应用 (获取 App ID 和 Secret，配置 OAuth 回调 URL)

### 飞书/Lark App创建

1. 请访问 开发者后台 创建一个新应用，并获得：
    * `App ID` - 飞书/Lark应用编号
    * `App Secret` - 请注意保管 App Secret，不要泄露到互联网。

2. 在应用管理页面，点击添加应用能力，找到机器人卡片，点击 +添加。
    ![img.png](docs/imgs/img.png)

3. 为应用开启如下权限
    ```json
    {
      "scopes": {
        "tenant": [
          "bitable:app",
          "docs:document:import",
          "docx:document",
          "drive:drive",
          "drive:drive.metadata:readonly",
          "drive:drive.search:readonly",
          "drive:drive:readonly",
          "wiki:wiki",
          "wiki:wiki:readonly"
        ],
        "user": [
          "docs:document.content:read",
          "docs:document.media:download",
          "docs:document:export",
          "docx:document.media:download",
          "docs:permission.setting:read",
          "docx:document",
          "docx:document:readonly",
          "drive:export:readonly",
          "offline_access",
          "search:docs:read"
        ] 
      }
    }
    ```
4. 配置 OAuth2.0 回调 URL：
    ```
    https://vertexaisearch.cloud.google.com/oauth-redirect
    ```
   ![img_1.png](docs/imgs/img_1.png)

5. 发布应用到企业。

### 🚀 快速部署 (推荐)

如果您是第一次使用本项目，请按照以下步骤快速完成环境准备与部署：

**1. 初始化环境**:
```bash
# 自动执行：安装 uv, 准备 Python 环境, 创建 .env, 检查并准备 GCS Bucket
bash scripts/init.sh
```
*注意：脚本会自动从 `.env.example` 创建 `.env`。执行后请务必打开 `.env` 并根据注释填写必填参数。*

**2. 一键部署**:
```bash
# 自动执行：打包应用 -> 部署到 Vertex AI -> 自动注册/更新到 Gemini Enterprise
bash deploy.sh
```
*部署完成后，控制台会输出直接访问 Gemini Enterprise 和 Vertex AI 的快捷链接。*

---

### 💻 本地运行与开发

**1. 本地 CLI 运行**:

本地运行会启动命令行交互。涉及飞书写操作或用户态读取时，工具会从运行上下文读取 Gemini Enterprise OAuth 注入的 access token；如需本地验证单个工具，建议直接编写测试或临时脚本传入 access token，不要修改 `tools.py` 中的授权读取逻辑。

```bash
uv run python -m lark_agent.main
```

**2. ADK Web 界面运行**:

启动本地开发服务器，通过 Web UI 与 Agent 交互：

```bash
uv run adk web
```

### ✍️ 将 AI 输出写入飞书文档

当前已支持通过飞书官方 MCP 网关创建云文档，并将 Agent 生成的 Markdown 内容写入新文档或已有文档。创建、更新类操作可能返回异步 `task_id`，可继续让 Agent 查询任务状态。

推荐触发方式：

```text
请帮我生成一份项目周报，并直接写入飞书文档，标题叫“项目周报”
```

```text
请把这段总结追加到这个飞书文档：https://...
```

能力说明见：

- [docs/FEISHU_DOC_SAVE_MCP_GUIDE.md](docs/FEISHU_DOC_SAVE_MCP_GUIDE.md)

---

### 🛠️ 进阶说明

如果您需要手动执行特定步骤，可以参考以下脚本：

*   **环境初始化**: [scripts/init.sh](scripts/init.sh)
*   **注册到 Gemini Enterprise**: [scripts/register_to_GE.sh](scripts/register_to_GE.sh)
*   **更新 Gemini Enterprise 引擎**: [scripts/update_agent_GE.sh](scripts/update_agent_GE.sh)

**详细部署细节与常见问题**：请参考 [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)。

## 🛠️ 开发与贡献

*   **依赖管理**: 本项目使用 `uv` 进行高效的包管理。
    ```bash
    uv sync
    ```
*   **代码风格**: 遵循 Python 标准代码风格。

## 📚 相关文档

*   [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) - 详细的打包部署指南
*   [scripts/README.md](scripts/README.md) - 脚本工具使用说明
*   [docs/GEMINI_ENTERPRISE_REGISTRATION_GUIDE.md](docs/GEMINI_ENTERPRISE_REGISTRATION_GUIDE.md) - Gemini Enterprise 注册指南
*   [docs/FEISHU_DOC_SAVE_MCP_GUIDE.md](docs/FEISHU_DOC_SAVE_MCP_GUIDE.md) - 飞书云文档写入能力说明
