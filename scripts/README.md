# 脚本工具说明

本目录包含用于管理和部署 Lark Document Agent 的脚本工具。

## 📋 脚本列表

### 1. `check_agent_status.sh`
**功能**：检查 Gemini Enterprise 中的 Agent 状态和授权配置

**使用方法**：
```bash
./scripts/check_agent_status.sh
```

**输出**：
- 所有 Agent 列表
- 所有授权资源列表
- 特定授权资源详情

---

### 2. `list_all_resources.sh`
**功能**：列出所有已部署的 Agent 和授权资源（格式化输出）

**使用方法**：
```bash
./scripts/list_all_resources.sh
```

**输出**：
- 格式化的 Agent 列表（显示名称、状态、Reasoning Engine）
- 格式化的授权资源列表

---

### 3. `batch_delete.sh`
**功能**：批量删除 Agent 和授权资源的交互式工具

**使用方法**：
```bash
./scripts/batch_delete.sh
```

**功能选项**：
1. 删除 Agent
2. 删除授权资源
3. 删除未使用的授权资源
4. 删除所有 Lark Document Agent（保留其他）
5. 退出

---

### 4. `delete_agent.sh`
**功能**：删除指定的 Agent

**使用方法**：
```bash
./scripts/delete_agent.sh <AGENT_NAME>
```

**示例**：
```bash
./scripts/delete_agent.sh projects/839062387451/locations/global/collections/default_collection/engines/webeye-agentspace-app_1742521319182/assistants/default_assistant/agents/123456789
```

**提示**：Agent NAME 可以从 `check_agent_status.sh` 或 `list_all_resources.sh` 的输出中获取

---

### 5. `delete_authorization.sh`
**功能**：删除指定的授权资源

**使用方法**：
```bash
./scripts/delete_authorization.sh <AUTH_ID>
```

**示例**：
```bash
./scripts/delete_authorization.sh lark-agent-oauth-id
```

**注意**：如果授权资源正在被 Agent 使用，删除会失败。需要先删除使用该授权资源的 Agent。

---

### 6. `delete_reasoning_engine.sh`
**功能**：删除指定的 Vertex AI Reasoning Engine

**使用方法**：
```bash
./scripts/delete_reasoning_engine.sh <ENGINE_NAME>
```

**特点**：
- 优先尝试使用 `gcloud`
- 如果 `gcloud` 命令不可用，自动回退到直接调用 REST API
- 自动处理 404 等已删除状态

---

### 7. `register_to_GE.sh`
**功能**：创建授权资源并注册 Agent 到 Gemini Enterprise（自动从环境变量加载配置）

**使用方法**：
```bash
./scripts/register_to_GE.sh
```

**重要**：
1. 注册前需要先完成部署，确保 `.deploy_env` 中已有 `VERTEX_REASONING_ENGINE_NAME`
2. 脚本会自动加载 `.env` 和 `.deploy_env` 中的配置
3. 如果授权资源已存在，会返回 409 错误（可以忽略）
4. 如果 Agent 已存在，建议先使用 `batch_delete.sh` 删除旧 Agent

---

### 8. `cleanup_deployed.sh`
**功能**：一键彻底删除所有远程资源（Agent、授权资源、Reasoning Engine）

**使用方法**：
```bash
./scripts/cleanup_deployed.sh
```

**特点**：
- 自动读取 `.env` 和 `.deploy_env`
- 依次调用 `delete_agent.sh`、`delete_authorization.sh` 和 `delete_reasoning_engine.sh`
- 可选清空本地部署记录文件

---

## 🔧 配置说明

所有脚本都通过 `scripts/load_env.sh` 自动加载以下配置文件：
- `.env`: 包含 `PROJECT_ID`, `GE_APP_ID`, `LARK_CLIENT_ID` 等基础配置
- `.deploy_env`: 包含部署后生成的 `VERTEX_REASONING_ENGINE_NAME` 和 `GE_AGENT_RESOURCE_NAME`

不再需要在脚本中硬编码这些 ID。

---

## 📝 使用流程示例

### 场景 1: 检查当前状态
```bash
./scripts/check_agent_status.sh
```

### 场景 2: 更新部署后重新注册
```bash
# 1. 查看当前 Agent
./scripts/list_all_resources.sh

# 2. 删除旧 Agent（如果需要）
./scripts/delete_agent.sh <OLD_AGENT_NAME>

# 3. 注册新 Agent
./scripts/register_to_GE.sh
```

### 场景 3: 清理所有资源
```bash
# 方式 A：使用交互式批量删除工具
./scripts/batch_delete.sh

# 方式 B：一键清理当前项目已部署的所有远程资源
./scripts/cleanup_deployed.sh
```

---

## ⚠️ 注意事项

1. **授权资源依赖**：删除授权资源前，必须先删除所有使用该授权资源的 Agent
2. **API 延迟**：删除操作后可能需要等待几秒钟，API 才会更新状态
3. **权限要求**：所有脚本都需要 `gcloud auth print-access-token` 有权限访问 Gemini Enterprise API

