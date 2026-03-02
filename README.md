* Lark Agent (ADK Agent)

  本项目是基于 **Gemini Enterprise** 平台，依托 **Google Cloud Vertex AI** 与 **Agent Development Kit (ADK)** 构建的全托管企业级智能 Agent。它深度集成了 Gemini 系列多模态大模型，旨在为企业员工提供一个统一、安全、且具备**视觉解析能力**的对话界面。用户通过自然语言即可实时检索Lark (飞书) 云空间，并针对文档内的文本、复杂图表及多模态图片进行深度问答与分析。

  > **Gemini Enterprise** 是部署在 Google Cloud Vertex AI 上的企业级生成式 AI 平台。与个人版（Gemini App）不同，它提供 **企业级数据隐私保护**（数据不用于训练）、**可保障的资源配额 (Quota)** 以及通过 **Reasoning Engine (ADK)** 部署自定义 Agent 的能力。

  ## ✨ 主要功能

  - **文档智能搜索**: 支持通过自然语言关键词搜索 Lark 云文档，并以 Markdown 超链接形式直观展示结果。
  - **文档内容问答**: Agent 可以深入读取文档的具体内容（支持 Markdown 格式），并基于内容回答用户的问题。
  - **安全认证**: 集成了 Lark OAuth2.0 认证流程。当检测到用户未授权时，会自动引导用户进行安全登录（Gemini Enterprise默认行为）。
  - **多模态视觉/富文本解析:** 依托 Gemini 多模态模型，Agent 不仅能读懂文字，还能“看懂”文档中的图片、流程图、架构图及 PDF 布局。

  ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=Y2VmODlhOTM1ZDFlM2QxNjlhOWRkNjMyNTQwY2FiMDNfeFo4d3hWUGppVlZxTkFoMnBDQUY4Vnh4aFcyVkRvOFNfVG9rZW46WUlSOWJ3SUhnb1pyM0t4NDZFbWMwUXVlbm9JXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)

  ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=OTVlNTI4M2Y0OTAyZWEyNWZjMTAxMzk5NzI4NGY5YmRfa2VrRnVxSmZVNjdOVmZqVDhzUVdtV2dhaVpJOE90UHhfVG9rZW46VFc2V2JBbEJabzk5T2l4ZExRTGN6ek5qbkpnXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)

  ## 🏗️ 架构设计

  本项目遵循 Clean Architecture 原则，主要包含以下模块：

  - **lark_agent/**: Agent 的核心逻辑。
    - `agent.py`: 定义 `LlmAgent` 的角色、Prompt 和工具集。
    - `tools.py`: 定义供 Agent 调用的工具函数（Interface Adapter 层）。
    - `callbacks.py`：通过**覆写** **SDK** **核心函数**，实现了直接读取文件字节流进行多模态识别的底层能力。
    - `infrastructure/`: 基础设施层，包含 `lark_api_repository.py` (Lark API 调用)。

  **多模态工具集：**当识别到问题涉及“富文本图片、图表、多媒体或复杂 PDF”时，Agent 会自动调用：

  - `get_lark_document_rich_content`: 提取富文本内容，将图片转化为多模态 `Part` 对象。
  - `get_lark_document_content_pdf`: 导出 PDF 字节流，适用于跨页图表和复杂表格的视觉还原。

  ## 🚀 部署与运行

  #### 前置要求

  - **Google Cloud Project**: 需启用`Vertex AI API`。
  - **创建Gemini Enterprise App** : 确保项目已创建 App 且已激活所需功能并拥有足够的 Vertex AI 配额。
  - **创建飞书/Lark 开放平台应用**: 获取 App ID 和 Secret，配置 OAuth 回调，[去配置](https://webeye.feishu.cn/wiki/LZDhwo1gliSKjjkD6GHc9bcZnLc#BfArd2NXhoMqtexOheEcKW45nyf)。

  **注**：需要一个存储桶（如：`gs://adk-agent-deploy`）用于托管代码，通过初始化脚本自动创建或手动创建。

  #### 快速部署 (推荐)

  如果您是第一次使用本项目，请按照以下步骤快速完成环境准备与部署：

  1. **初始化环境**:

  ```Shell
  # 自动执行：安装 uv, 准备 Python 环境, 从 .env.example 创建 .env, 检查并准备 GCS Bucket
  bash scripts/init.sh
  ```

  *执行后请务必打开 .env 文件，根据其中的中文注释填写必填参数（如 PROJECT_ID, GE_APP_ID 等）。*

  1. **一键部署**:

  ```Shell
  # 自动执行：打包应用 -> 部署到 Vertex AI (Reasoning Engine) -> 自动注册/更新到 Gemini Enterprise
  bash deploy.sh
  ```

  *部署完成后，脚本会输出直接访问 Gemini Enterprise 和 Vertex* *AI* *的快捷链接。*

  ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=ZWFhYTNkNTdhZTIzNTQ5NGU2NzJkMTAyNzAyOTVmMjVfcHI3WlgwcDNyUzJ0dzlmUW1TWURCWVlOYURlTWZFeG9fVG9rZW46TmVXVWJweG05b0lZUGR4aDBZRmNJY2gybkNmXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)

  #### ⚙️ 配置说明 (.env)

  在本地运行或部署前，请确保 `.env` 文件已正确配置。核心配置项如下：

  ```Plain
  # --- 飞书 (Lark) 集成配置 ---
  LARK_AUTH_ID="your-unique-auth-id"  # 授权资源唯一标识符
  LARK_CLIENT_ID="your-lark-app-id"     # 飞书应用 App ID
  LARK_CLIENT_SECRET="your-lark-app-secret" # 飞书应用 App Secret
  
  # --- Google Cloud Platform 配置 ---
  PROJECT_ID="your-project-id"        # GCP 项目 ID
  STAGING_BUCKET="gs://your-bucket"   # 部署文件存储桶
  
  # --- Gemini Enterprise 配置 ---
  GE_APP_ID="your-app-id"             # Gemini Enterprise App ID
  GE_APP_LOCATION="global"            # 数据区域 (如 global, us, eu)
  ```

  #### 创建 飞书/Lark App

  1. 请访问 [开发者后台](https://open.feishu.cn/app) 创建一个新应用，并获得：
     1. `App ID` - 飞书/Lark应用编号
     2. `App Secret` - 请注意保管 App Secret，不要泄露到互联网。
  2. 在应用管理页面，点击 **“添加应用能力”** -> **“机器人”** -> **“添加”**

  ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=NjEwOTNlMDJiNDVlYmY1ZjE5YWY3MzY5NWI1MzEzY2ZfQklqMVJqcDhyYXc1SHkwS1dHTElaV3VwNEpkQXRzeExfVG9rZW46WXczMGJnQ1RGb3YxYkp4M0YwbmN0elNZbnllXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)

  1. 为应用开启如下权限，点击 **“权限管理”** -> **“批量导入权限”**，粘贴以下内容并确认添加

  ```JSON
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

  1. 配置 OAuth2.0 回调 URL：
     1. ```Plain
        https://vertexaisearch.cloud.google.com/oauth-redirect
        ```

     2. ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=ODMyYTkyMzliNmFjNjJiYjc5NGY1M2Y0NTgzZjkwNTJfRGtIZUdUNGZ0UlV0aDZ1eEpPT0YyVUVkUXhrSTM1VzFfVG9rZW46TERDOGJFbGs5b2FUTER4TEVGQ2NiSkZtblRlXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)
  2. 发布应用到企业。

  #### 🛠️ 进阶：手动管理与维护

  如果您需要精细化控制部署过程，可以参考项目源码中的 `scripts/` 目录。

  **重要注意事项**:

  - **Python 版本**: Vertex AI Agent Engine 目前主要支持版本 **Python 3.10+**。
  - **依赖说明**: 构建时必须确保 `pyproject.toml` 中包含 `google-adk`, `google-cloud-aiplatform` 以及 `requests`。
  - **LARK_DOMAIN**: 必须填写完整的 URL（如 `https://open.feishu.cn`），不可包含中文或多余空格。

  **常见问题排查**:

  - **"Access token is missing"**: 通常是因为 `.env` 中的 `LARK_AUTH_ID` 与在 Gemini Enterprise 注册时填写的 `授权名称 (Authorization ID)` 不匹配。
  - **"is used by another agent"**: 一个授权资源只能被一个 Agent 使用。如果需要更换 Agent，请先在控制台或使用脚本删除旧的 Agent。
  - **"Invalid URL"**: 请检查 `.env` 中的域名配置是否包含协议头 `https://`。

  ##### 将 Agent 注册至 Gemini Enterprise 平台

  ###### 控制台方式 （不推荐）

  1. 进入Console Gemini Enterprise控制台，选择【代理】，点击【+添加代理】，如下图所示：
     1. ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=MmQwOTgxZWFiYjAzZTM1ZjNkNmRlMjE4YzBhZmE5NDZfTTJtMW1nNFB4T3FSbDVvSUZidml1cDlSOTk4dHVwdXlfVG9rZW46WE9KNGJRZmFDb1ZLSHp4QnN1UmNqYUJmbjhlXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)
  2. 选择`通过 Agent Engine 构建的自定义代理`
     1. ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=NGRkZjQyYTdlMmYyMTc4MGY3YWRiNjJiM2IyYTYxMzBfY2d5TmU0NmZ4U05BbThrQWNqYWtybGF0TW9vWDh4MUJfVG9rZW46RGtGRmJFTFZXbzJIU2R4QVJZVmM3dGhybm1jXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)
  3. 进行配置页面填写授权和配置信息
     1. ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=NGViOGNjMjc2M2ZjMDYyMmFkMmUxYzhjNTg0YTYxMDJfWVJNQ1pPQkxvTGE5WTFPTU9ZNThnYVlRSW4waVhjeW5fVG9rZW46Q09SZWJTMmlwb1NkdGF4M3FWYWNEQ2h3bk5lXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)

     2. 

     3. ```Plain
        授权名称：用户自定于（lark-agent-oauth），此时会生成一个授权资源ID（auth_id），请记住该ID，后续需要在.env中配置LARK_AUTH_ID（若Agent已部署，可使用新环境变量更新部署）
        客户端ID: 飞书/Lark App ID
        客户端密钥: 飞书/Lark App Secret
        令牌URI: https://open.feishu.cn/open-apis/authen/v2/oauth/token or https://open.larksuite.com/open-apis/authen/v2/oauth/token
        授权URI：https://accounts.feishu.cn/open-apis/authen/v1/authorize?client_id=cli_a84b9e54717a500e&response_type=code&scope=offline_access bitable:app docs:document:import docx:document drive:drive wiki:wiki wiki:wiki:readonly docs:document.content:read search:docs:read or https://accounts.larksuite.com/open-apis/authen/v1/authorize?client_id=APP_ID&response_type=code&scope=offline_access  bitable:app docs:document:import docx:document drive:drive wiki:wiki wiki:wiki:readonly docs:document.content:read search:docs:read
        ```

     4. ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=M2UwNzVjOWZkZDdiYzdmNjNlZWQ5ODY5ZjZiMTJkNzJfRXNyZTQwYVZFTFdrY0sycVNwc3pITE5vaXNOSWlKVnRfVG9rZW46VVZKNmJOdVB5b3NqdEN4SHF6QmNlR0FBbmdiXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)
  4. 完成创建后即可在 Gemini Enterprise 控制台使用 Lark Agent。
     1. ![img](https://webeye.feishu.cn/space/api/box/stream/download/asynccode/?code=ODg4NjczYzNiOTM5YWExMTY1ZGM2OTkwNjExNzlkYjlfQUk0N2h6VlNLUkk5U1IxTVVmWUo4ZUlJbkRLV2E5SHFfVG9rZW46VnVFYmJwalZwbzB0WjJ4S3lzbmNTMko2bkJlXzE3NzIwODg4NjQ6MTc3MjA5MjQ2NF9WNA)

     2. 

     3. ######  curl command 方式 （推荐）

     4. 创建授权资源（auth_id）：
        1. ```Bash
           curl -X POST \
              -H "Authorization: Bearer $(gcloud auth print-access-token)" \
              -H "Content-Type: application/json" \
              -H "X-Goog-User-Project: <GCP 项目ID>" \
              "https://global-discoveryengine.googleapis.com/v1alpha/projects/<GCP 项目ID>/locations/global/authorizations?authorizationId=<AUTH_ID>" \
              -d '{
                 "name": "projects/<GCP 项目ID>/locations/global/authorizations/<AUTH_ID>",
                 "serverSideOauth2": {
                    "clientId": "<飞书/Lark App ID>",
                    "clientSecret": "<飞书/Lark App Secret>",
                    "authorizationUri": "https://accounts.feishu.cn/open-apis/authen/v1/authorize?client_id=cli_a84b9e54717a500e&response_type=code&scope=offline_access bitable:app docs:document:import docx:document drive:drive wiki:wiki wiki:wiki:readonly docs:document.content:read search:docs:read",
                    "tokenUri": "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
                 }
           }'
           ```

        2.   根据实际需要填写：

        3. `<GCP 项目ID>`: 您的 Google Cloud 项目 ID。
        4. `<AUTH_ID>`: 您为该授权资源指定的唯一标识符。
        5. `<飞书/Lark App ID>`: 您的飞书/Lark 应用的 App ID。
        6. `<飞书/Lark App Secret>`: 您的飞书/Lark 应用的 App Secret。
     5. 将agent注册到Gemini Enterprise
        1. ```Bash
           curl -X POST \
           -H "Authorization: Bearer $(gcloud auth print-access-token)" \
           -H "Content-Type: application/json" \
           -H "X-Goog-User-Project: <GCP 项目ID>" \
           "https://discoveryengine.googleapis.com/v1alpha/projects/<GCP 项目ID>/locations/global/collections/default_collection/engines/<Gemini Enterpirse App ID>/assistants/default_assistant/agents" \
           -d '{
           "displayName": "Lark Document Agent",
           "description": "Lark Document Agent",
           "adkAgentDefinition": {
           "provisionedReasoningEngine": {
           "reasoningEngine":"<Agent Engine Resource ID>"
           }},
           "authorizationConfig": {
           "toolAuthorizations": [
             "projects/<GCP 项目ID>/locations/global/authorizations/<AUTH_ID>"
           ]
           }}'
           ```

        2.   根据实际参数填写：

        3. `<GCP 项目ID>`: 您的 Google Cloud 项目 ID。
        4. `<Gemini Enterpirse App ID>`: 您的 Gemini Enterprise 应用 ID。
        5. `<Agent Engine Resource ID>`: 您的 Agent Engine 资源 ID。
        6. `<AUTH_ID>`: 您之前创建的授权资源 ID。

  ## 📄 飞书文档全量内容获取方案

  在本项目中，为了让 Agent 获取包括文本和图片在内的完整多模态信息，我们主要采用了以下两种深度提取方案。开发者应根据业务权限和并发场景选择最合适的路径。

  1. ### 导出方式

  这是目前 `get_lark_document_rich_content` 和 `get_lark_document_content_pdf` 采用的主力方案。

  - **技术路径**：
    - **PDF 模式**：直接调用飞书导出接口获取 PDF 字节流。
    - **Word 模式**：通过导出 `.docx` 并解压，从中精准提取 `media/` 文件夹中的原始图片。
  - **权限要求**：必须具备该文档的 **“可编辑 / 可导出”** 权限。若文档被开启“禁止导出”，此方案将立即失效。
  - **特性**：
    - **优势**：视觉保真度 100%，图片提取简单直接，不需要逐个处理 Block。
    - **频率限制**：飞书对导出任务有较严格的频率控制（通常为 **100 次/分钟**）。高频并发调用更易触发 `429` 限流，且导出任务属于异步重操作，系统负载较高。

  1. ### 块读取方式

  这是一套同样支持“全量内容”获取的稳健方案，通过解析所有 块/Block 提取内容。

  - **技术路径**：
    - **结构化读取**：利用 `Docx Block API` 深度遍历文档树，将文本、表格、代码块等结构化提取。
    - **资源下载**：识别图片 Block 的 `file_token`，配合 `Drive Media API` **单独下载** 每一个图片素材，最后根据 Block 顺序重组为富文本流。
  - **权限要求**：仅需 **“阅读权限”** 即可读取内容，但下载图片等资源是否受限取决于该租户的安全策略（如：是否开启了“仅能阅读不能下载资源”的细粒度策略）。
  - **特性**：
    - **优势 1：频率容忍度高**。读取 Block 的频率限制通常为 **300 次/分钟** (5次/秒)，比导出接口更宽松，适合大规模、高频的检索任务。
    - **优势 2：权限兼容性强**。在用户无法提供导出权限的场景下，这是唯一的全量采集手段。
    - **复杂度**：逻辑较繁琐，需要手动处理分段下载和内容重合。

  ### � 频率限制与安全性对比 (API Limits)

  根据飞书官方 API 规范，两者的并发安全与风险对标如下：

  | 维度         | 导出模式 (Export)                                        | 块读取模式 (Block & Download)                     |
  | ------------ | -------------------------------------------------------- | ------------------------------------------------- |
  | 并发上限     | 较低 (~100次/分)                                         | 较高 (~300次/分)                                  |
  | 权限门槛     | 高 (编辑/导出权限)                                       | 低 (仅阅读权限)                                   |
  | 限流风险     | 较高。导出是大动作，频繁调用极易触发系统级限流甚至审计。 | 较低。属于常规阅读操作。                          |
  | 图片下载限制 | 随包下载，受导出配额限制。                               | 需调用下载 API，受 10,000次/日 素材下载配额限制。 |
  | 推荐场景     | 针对单篇文档的视觉重度分析                               | 大规模语义理解与分布式内容采集                    |

  **注**：飞书会对恶意高频爬取行为进行监控。建议在代码中实现 **指数退避 (Exponential Backoff)** 逻辑，以应对 `429` 响应。

  ## 💰成本构成

  > Gemini Enterprise订阅费
  >
  > Agent托管服务费
  >
  > 大模型Token使用费

  **Google Cloud Vertex AI 与 Gemini Enterprise 服务** 这是该方案的核心成本来源：

  1. **模型推理费用 (Model Inference)**：方案深度集成了Gemini系列多模态大模型（如 `gemini-3-flash-preview`）进行文本与图表的多模态理解。这通常会根据输入和输出的Token数量产生费用。
  2. **Agent** **Engine / Reasoning Engine 费用**：项目依托 `Google Cloud Vertex AI` 与 `Reasoning Engine` 构建。部署和运行Agent Engine通常会涉及相关的计算或管理费用。
  3. **存储资源 (Storage)**
     1. **Google Cloud Storage (GCS)**：部署说明中明确指出，需提前创建一个 Google Storage 存储桶（如 `gs://adk-agent-deploy`）用于存储部署文件。这将根据存储的数据量（GB/月）和读写操作次数产生存储费用。

  ## 🛠️ 开发与贡献

  - **用户access_token获取方式**：`tool_context.state[f"{LARK_AUTH_ID}"]` 的代码。
  - **依赖管理**: 本项目使用 `uv` 进行高效的包管理。
  - **代码风格**: 遵循 Python 标准代码风格。

  ## 📎附件

  ### 《How to register and use ADK Agents with Gemini Enterprise》

  暂时无法在飞书文档外展示此内容
