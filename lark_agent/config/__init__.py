import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,  # 强制重新配置，防止被其他库覆盖
)

logger = logging.getLogger(__name__)

LARK_AUTH_ID = os.getenv("LARK_AUTH_ID", "lark-agent-oauth_v1")
LARK_DOMAIN = os.getenv("LARK_DOMAIN", "https://open.feishu.cn")
LARK_CLIENT_ID = os.getenv("LARK_CLIENT_ID")

GOOGLE_WORKSPACE_AUTH_ID = os.getenv("GOOGLE_WORKSPACE_AUTH_ID", "google-workspace-oauth_v1")
GOOGLE_WORKSPACE_PROJECT_ID = os.getenv("GOOGLE_WORKSPACE_PROJECT_ID", os.getenv("PROJECT_ID", ""))
