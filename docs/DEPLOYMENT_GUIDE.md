# 打包部署指南

## 📋 打包部署前的检查清单

### 1. 环境配置检查

#### .env 文件配置
确保 `.env` 文件包含以下配置：
```env
LARK_AUTH_ID=lark-agent-oauth-id  # 必须与注册时的授权资源ID一致
LARK_DOMAIN=https://open.feishu.cn                # 飞书开放平台域名
GOOGLE_API_KEY=your-api-key                       # 本地开发使用（部署时不需要）
```

**重要提示**：
- `LARK_AUTH_ID` 必须与 Gemini Enterprise 中创建的授权资源 ID 完全一致
- `LARK_DOMAIN` 必须是完整的 URL（包含 `https://`），不能是中文或注释

#### deploy.py 配置
检查 `lark_agent/deployement/deploy.py` 中的配置：
```python
PROJECT_ID = "webeye-internal-test"        # 你的 GCP 项目 ID
LOCATION = "us-central1"                   # 部署区域
STAGING_BUCKET = "gs://adk-agent-deploy"   # GCS 存储桶（必须存在）
```

### 2. 依赖检查

确保 `pyproject.toml` 包含所有必要的依赖：
```toml
dependencies = [
    "google-adk>=1.24.1",
    "google-cloud-aiplatform>=1.135.0",
    "requests>=2.31.0"
]
```

### 3. Python 版本要求

**重要**：Agent Engine 目前支持 Python 3.10-3.14。推荐使用最新的 Python 3.14 以获得最佳性能。

---

## 🚀 打包部署流程

### 步骤 1: 构建 Wheel 包

```bash
cd /Users/apple/IdeaProjects/adk-agents
uv build --wheel --out-dir lark_agent/deployement
```

**注意**：必须在项目根目录执行，不能在子目录中执行。

### 步骤 2: 准备部署环境

```bash
# 创建并启用虚拟环境
uv sync && source .venv/bin/activate

# 安装部署所需的依赖
pip install google-cloud-aiplatform>=1.135.0 python-dotenv google-adk>=1.24.1
```

### 步骤 3: 执行部署

```bash
cd /Users/apple/IdeaProjects/adk-agents
export PYTHONPATH=$(pwd)
cd lark_agent/deployement
python3 deploy.py
```

**部署成功后会输出**：
```
Deployment finished!
Resource Name: projects/<PROJECT_ID>/locations/<LOCATION>/reasoningEngines/<ENGINE_ID>
```

**请保存这个 Resource Name**，后续注册时需要用到。

---

## 📝 注册到 Gemini Enterprise

### 🚀 一键注册 Agent

我们也提供了一个脚本来处理 OAuth 资源创建和 Agent 注册的全过程：

```bash
./scripts/register_to_GE.sh
```

**该脚本会自动**：
1. 从 `.env` 获取 GCP 项目、Lark 凭证和 Gemini App ID。
2. 从 `.deploy_env` 获取最新部署生成的 Reasoning Engine ID。
3. 如果不存在，则在 Gemini Enterprise 中创建对应的 OAuth 授权资源。
4. 将 Agent 注册到指定的 Gemini App/Engine 中。
5. 完成后自动将 Agent 的完整资源名称保存到 `.deploy_env`，便于后续维护。

---

## ⚠️ 常见问题


### 问题 2: 授权资源被使用
**错误**：`is used by another agent`
**解决**：
1. 查找并删除使用该授权资源的 Agent
2. 或者删除授权资源后重新创建

### 问题 3: 环境变量不匹配
**错误**：`Access token is missing`
**解决**：
1. 确保 `.env` 文件中的 `LARK_AUTH_ID` 与授权资源 ID 一致
2. 重新部署 Agent

### 问题 4: URL 格式错误
**错误**：`Invalid URL '飞书开放平台...'`
**解决**：确保 `.env` 文件中的 `LARK_DOMAIN` 是完整的 URL：`https://open.feishu.cn`

---

## 🔄 更新部署流程

如果代码有更新，需要重新部署：

1. **更新代码**
2. **重新构建**：`uv build --wheel --out-dir lark_agent/deployement`
3. **重新部署**：执行 `bash deploy.sh`
4. **重新注册**：执行 `scripts/register_to_GE.sh`，脚本会自动根据最新生成的部署 ID 进行注册
5. **删除旧版本**（可选）：可以使用 `scripts/cleanup_deployed.sh`（一键清理当前项目资源）或 `scripts/batch_delete.sh`（交互式清理）
---

## 📦 环境变量说明

### 部署时的环境变量

部署时，`deploy.py` 会从 `.env` 文件读取以下环境变量并设置到 Agent Engine：

- `LARK_AUTH_ID`: 授权资源 ID，用于从 `tool_context.state` 获取 access_token
- `LARK_DOMAIN`: 飞书 API 域名

**重要**：这些环境变量在部署时设置，修改 `.env` 后必须重新部署才能生效。

### 运行时获取 Token

代码中通过以下方式获取 access_token：
```python
access_token = tool_context.state.get(f"{LARK_AUTH_ID}")
```

Gemini Enterprise 会自动管理 OAuth 流程，将 token 存储到 `tool_context.state` 中。

