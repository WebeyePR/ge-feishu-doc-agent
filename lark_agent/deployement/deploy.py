import os
import socket
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
CREATE_TIMEOUT_SECONDS = float(os.getenv("AGENT_ENGINE_CREATE_TIMEOUT_SECONDS", "2400"))

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# .whl 文件就在脚本所在目录 (由 deploy.sh 生成)
AGENT_WHL_FILE_NAME = "adk_agents-0.1.0-py3-none-any.whl"
AGENT_WHL_FILE = os.path.join(CURRENT_DIR, AGENT_WHL_FILE_NAME)

# 专门用于存储部署过程中自动生成的参数，位于项目根目录
ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
DEPLOY_ENV_FILE = os.path.join(ROOT_DIR, ".deploy_env")

# 增加全局网络超时时间，处理大包上传（如 lark-cli 二进制文件）
socket.setdefaulttimeout(600)  # 设置为 10 分钟

# 尝试通过猴子补丁 (Monkey Patch) 解决 google-cloud-storage 内部上传超时问题
try:
    from google.cloud import storage
    from google.api_core import retry
    from google.cloud.storage.retry import DEFAULT_RETRY

    # 这里的 120s 往往是 google-api-core 的默认重试截止时间
    # 我们尝试覆盖 Blob 对象的默认上传行为或重试策略
    def patched_upload_from_string(self, data, *args, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = 600  # 强制设置 10 分钟超时
        if "retry" not in kwargs or kwargs["retry"] is DEFAULT_RETRY:
            kwargs["retry"] = DEFAULT_RETRY.with_timeout(600)
        return self._old_upload_from_string(data, *args, **kwargs)

    def patched_upload_from_file(self, file_obj, *args, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = 600
        if "retry" not in kwargs or kwargs["retry"] is DEFAULT_RETRY:
            kwargs["retry"] = DEFAULT_RETRY.with_timeout(600)
        return self._old_upload_from_file(file_obj, *args, **kwargs)

    if not hasattr(storage.blob.Blob, "_old_upload_from_string"):
        storage.blob.Blob._old_upload_from_string = storage.blob.Blob.upload_from_string
        storage.blob.Blob.upload_from_string = patched_upload_from_string
        print("Patched google.cloud.storage.blob.Blob.upload_from_string with 600s timeout.")

    if not hasattr(storage.blob.Blob, "_old_upload_from_file"):
        storage.blob.Blob._old_upload_from_file = storage.blob.Blob.upload_from_file
        storage.blob.Blob.upload_from_file = patched_upload_from_file
        print("Patched google.cloud.storage.blob.Blob.upload_from_file with 600s timeout.")

    # 针对 Retry 策略的截止时间进行补丁
    # vertex ai sdk 内部可能使用了带有默认 deadline 的 retry
    _old_retry_init = retry.Retry.__init__
    def patched_retry_init(self, *args, **kwargs):
        if "deadline" in kwargs and kwargs["deadline"] == 120.0:
            kwargs["deadline"] = 600.0
        _old_retry_init(self, *args, **kwargs)
    retry.Retry.__init__ = patched_retry_init
    print("Patched google.api_core.retry.Retry deadline to 600s.")
except ImportError:
    pass

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

# 打印文件大小供调试
if os.path.exists(AGENT_WHL_FILE):
    size_mb = os.path.getsize(AGENT_WHL_FILE) / (1024 * 1024)
    print(f"Preparing to upload {AGENT_WHL_FILE} ({size_mb:.2f} MB)...")
else:
    print(f"Error: {AGENT_WHL_FILE} not found!")
    exit(1)

print(
    f"Starting Agent Engine deploy with polling timeout {int(CREATE_TIMEOUT_SECONDS)} seconds..."
)

try:
    # 强制重新加载环境变量，确保 deploy.sh 加载的变量生效
    dotenv.load_dotenv(override=True)

    # Install the project wheel remotely. Its METADATA is generated from
    # pyproject.toml, so runtime dependencies still have a single source of
    # truth while the lark_agent package itself is installed before unpickling.
    os.chdir(CURRENT_DIR)
    requirements_source = [AGENT_WHL_FILE_NAME]
    extra_packages_source = [AGENT_WHL_FILE_NAME]
    print(f"Deploying using local wheel requirement: {requirements_source}")

    remote_app = agent_engines.create(
        agent_engine=app,
        requirements=requirements_source,
        extra_packages=extra_packages_source,
        display_name=os.getenv("AGENT_DISPLAY_NAME", "Lark Document Agent"),
        env_vars={
            "LARK_AUTH_ID": os.getenv("LARK_AUTH_ID"),
            "LARK_DOMAIN": os.getenv("LARK_DOMAIN"),
            "LARK_CLIENT_ID": os.getenv("LARK_CLIENT_ID"),
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
