import os
from concurrent.futures import TimeoutError as FuturesTimeoutError

import dotenv
import vertexai
from google.api_core.future import polling
from vertexai import agent_engines

from lark_agent import root_agent

dotenv.load_dotenv()

PYTHONPATH = os.environ.get("PYTHONPATH", ".")

# 从环境变量读取配置
PROJECT_ID = os.getenv("PROJECT_ID")
# For other options, see https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview#supported-regions
LOCATION = os.getenv("DEPLOY_LOCATION", "us-central1")
STAGING_BUCKET = os.getenv("STAGING_BUCKET", "gs://adk-agent-deploy")
CREATE_TIMEOUT_SECONDS = float(os.getenv("AGENT_ENGINE_CREATE_TIMEOUT_SECONDS", "1800"))

AGENT_WHL_FILE = "adk_agents-0.1.0-py3-none-any.whl"

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# # 优先查找 dist 目录下的文件
# AGENT_WHL_FILE = os.path.join(CURRENT_DIR, WHL_NAME)
# 专门用于存储部署过程中自动生成的参数
# DEPLOY_ENV_FILE = os.path.join(CURRENT_DIR, "../../.deploy_env")
DEPLOY_ENV_FILE = f"{PYTHONPATH}/.deploy_env"

# Initialize the Vertex AI SDK
vertexai.init(
    project=PROJECT_ID,
    location=LOCATION,
    staging_bucket=STAGING_BUCKET,
)

# Vertex SDK internally waits on the LRO with google.api_core.future.polling.DEFAULT_POLLING,
# which defaults to 900 seconds. Large builds can exceed that even when the remote operation
# eventually succeeds, so we extend the polling timeout here.
polling.DEFAULT_POLLING = polling.DEFAULT_POLLING.with_timeout(CREATE_TIMEOUT_SECONDS)

# Wrap the agent in an AdkApp object
app = agent_engines.AdkApp(
    agent=root_agent,
    enable_tracing=False,  # 彻底避开 OpenTelemetry 的 Context 冲突
    app_name="lark-agent-app",
)
print(
    f"Starting Agent Engine deploy with polling timeout {int(CREATE_TIMEOUT_SECONDS)} seconds..."
)

try:
    remote_app = agent_engines.create(
        agent_engine=app,
        requirements=[AGENT_WHL_FILE],
        extra_packages=[AGENT_WHL_FILE],
        display_name=os.getenv("AGENT_DISPLAY_NAME", "Lark Document Agent"),
        env_vars={
            "LARK_AUTH_ID": os.getenv("LARK_AUTH_ID"),
            "LARK_DOMAIN": os.getenv("LARK_DOMAIN"),
        },
    )
except FuturesTimeoutError as e:
    print(
        "Deployment timed out while waiting for the Reasoning Engine LRO to finish. "
        "This often means the remote build is still running rather than a hard failure."
    )
    print(
        "Recommended actions: check Cloud Logging / Vertex AI Agent Engine console, "
        "then rerun with a larger AGENT_ENGINE_CREATE_TIMEOUT_SECONDS if needed."
    )
    raise

print("Deployment finished!")
print(f"Resource Name: {remote_app.resource_name}")

# 自动回写到独立的 .deploy_env 文件
# 不再修改手动维护的 .env 文件
try:
    dotenv.set_key(
        DEPLOY_ENV_FILE,
        "VERTEX_REASONING_ENGINE_NAME",
        remote_app.resource_name,
        quote_mode="always",
    )
    print(f"Successfully updated VERTEX_REASONING_ENGINE_NAME in {DEPLOY_ENV_FILE}")
except Exception as e:
    print(f"Warning: Failed to update {DEPLOY_ENV_FILE}: {e}")
