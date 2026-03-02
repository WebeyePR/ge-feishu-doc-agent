# 打包部署注意点总结

## 📦 打包阶段

### 1. 环境要求
- **Python 版本**：推荐使用 Python 3.10-3.14
- **构建工具**：使用 `uv` 进行打包
- **工作目录**：必须在项目根目录执行 `uv build`

### 2. 依赖检查
确保 `pyproject.toml` 包含：
- `google-adk>=1.24.1`
- `google-cloud-aiplatform>=1.135.0`
- `requests>=2.31.0`

### 3. 构建命令
```bash
cd /Users/apple/IdeaProjects/adk-agents
uv build --wheel --out-dir lark_agent/deployement
```

**注意**：
- 输出目录会自动创建
- 生成的 wheel 文件名格式：`adk_agents-0.1.0-py3-none-any.whl`

---

## 🚀 部署阶段

### 1. 环境准备
```bash
# 创建并启用虚拟环境
uv sync && source .venv/bin/activate

# 安装部署依赖
pip install google-cloud-aiplatform>=1.135.0 python-dotenv google-adk>=1.24.1
```

### 2. 配置检查

#### deploy.py 配置
检查以下配置是否正确：
```python
PROJECT_ID = "webeye-internal-test"
LOCATION = "us-central1"
STAGING_BUCKET = "gs://adk-agent-deploy"  # 必须存在
```

#### .env 文件配置
**关键配置项**：
```env
LARK_AUTH_ID=lark-agent-oauth-id  # ⚠️ 必须与授权资源ID一致
LARK_DOMAIN=https://open.feishu.cn                # ⚠️ 必须是完整URL，不能有中文
```

**常见错误**：
- ❌ `LARK_DOMAIN=飞书开放平台域名` → ✅ `LARK_DOMAIN=https://open.feishu.cn`
- ❌ `LARK_AUTH_ID` 与授权资源不一致 → 会导致 "Access token is missing"

### 3. 执行部署
```bash
cd /Users/apple/IdeaProjects/adk-agents
export PYTHONPATH=$(pwd)
cd lark_agent/deployement
python3 deploy.py
```

### 4. 保存部署结果
部署成功后会输出 Reasoning Engine Resource Name：
```
Resource Name: projects/839062387451/locations/us-central1/reasoningEngines/4484236128093732864
```

**⚠️ 重要**：保存这个 ID，后续注册时需要用到。

---

## 📝 注册阶段

### 1. 执行注册脚本
直接运行 `scripts/register_to_GE.sh`。脚本会自动从 `.env` 和 `.deploy_env` 中加载配置和 Reasoning Engine ID。

### 2. 检查授权资源
如果授权资源不存在，脚本会自动创建。如果已存在，会返回 409 错误（可以忽略）。

### 3. 删除旧 Agent（如需要）
如果授权资源被其他 Agent 使用，需要先删除：
```bash
# 查找使用该授权资源的 Agent
./scripts/check_agent_status.sh

# 删除旧 Agent
./scripts/delete_agent.sh <AGENT_NAME>
```

### 4. 执行注册
```bash
./scripts/register_to_GE.sh
```

---

## ⚠️ 常见问题与解决方案


### 问题 2: 授权资源被使用
**错误**：`is used by another agent`
**解决**：
1. 使用 `./scripts/check_agent_status.sh` 查找使用该授权资源的 Agent
2. 使用 `./scripts/delete_agent.sh` 删除旧 Agent
3. 等待几秒后重新注册

### 问题 3: Access token is missing
**错误**：`访问令牌丢失`
**原因**：`.env` 文件中的 `LARK_AUTH_ID` 与授权资源 ID 不一致
**解决**：
1. 检查授权资源 ID：`./scripts/check_agent_status.sh`
2. 更新 `.env` 文件中的 `LARK_AUTH_ID`
3. 重新部署 Agent

### 问题 4: Invalid URL
**错误**：`Invalid URL '飞书开放平台...'`
**原因**：`LARK_DOMAIN` 配置错误
**解决**：确保 `.env` 文件中 `LARK_DOMAIN=https://open.feishu.cn`（完整URL）

### 问题 5: 模块未找到
**错误**：`ModuleNotFoundError: No module named 'google.adk'`
**解决**：在部署虚拟环境中安装依赖：
```bash
pip install google-cloud-aiplatform==1.128.0 python-dotenv google-adk
```

---

## 🔄 更新部署流程

当代码有更新时，需要重新部署：

1. **更新代码**
2. **重新构建**：`uv build --wheel --out-dir lark_agent/deployement`
3. **重新部署**：执行 `deploy.py`，获取新的 Reasoning Engine ID
4. **自动注册**：执行 `scripts/register_to_GE.sh`，脚本会自动识别新 ID 并更新关联。
5. **删除旧 Agent**（可选）：如果不想保留旧版本
6. **注册新 Agent**：使用新的 Reasoning Engine ID

---

## 📋 检查清单

部署前检查：
- [ ] Python 版本是 3.10-3.14
- [ ] `.env` 文件中的 `LARK_AUTH_ID` 与授权资源 ID 一致
- [ ] `.env` 文件中的 `LARK_DOMAIN` 是完整的 URL（`https://open.feishu.cn`）
- [ ] `deploy.py` 中的 `PROJECT_ID`、`LOCATION`、`STAGING_BUCKET` 配置正确
- [ ] `pyproject.toml` 包含 `requests>=2.31.0` 依赖

部署后检查：
- [ ] 保存了 Reasoning Engine ID
- [ ] 运行 `scripts/register_to_GE.sh` 完成注册
- [ ] 检查了授权资源状态（`./scripts/check_agent_status.sh`）
- [ ] 成功注册了 Agent

---

## 🛠️ 有用的脚本工具

所有脚本位于 `scripts/` 目录：

- `check_agent_status.sh` - 检查 Agent 和授权资源状态
- `list_all_resources.sh` - 列出所有资源（格式化输出）
- `batch_delete.sh` - 批量删除工具（交互式）
- `delete_agent.sh` - 删除指定 Agent
- `delete_authorization.sh` - 删除指定授权资源
- `register_to_GE.sh` - 自动化注册 Agent（推荐）
- `cleanup_deployed.sh` - 一键清理已部署的项目资源（Agent + Auth + Engine）
详细说明请参考：[scripts/README.md](../scripts/README.md)
