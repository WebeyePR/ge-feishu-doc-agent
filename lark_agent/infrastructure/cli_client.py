import os
import subprocess
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class CLIClient:
    """
    Wrapper for lark-cli (Go binary).
    Handles authentication via environment variables and executes commands.
    """
    
    def __init__(self, bin_path: Optional[str] = None):
        # 默认在项目根目录下的 bin 目录查找二进制文件
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        if bin_path:
            self.bin_path = bin_path
        else:
            # 自动识别环境选择二进制文件
            # 如果是 Linux 环境，优先使用 lark-cli-linux
            linux_bin = os.path.join(project_root, "bin", "lark-cli-linux")
            default_bin = os.path.join(project_root, "bin", "lark-cli")
            
            if os.path.exists(linux_bin):
                self.bin_path = linux_bin
            else:
                self.bin_path = default_bin
        
        # self._check_binary()

    def _check_binary(self):
        if not os.path.exists(self.bin_path):
            logger.warning(f"lark-cli binary not found at {self.bin_path}. "
                           "Please ensure it is compiled and placed in the bin/ directory.")

    def run_command(self, service: str, command: str, args: list, access_token: str, app_id: str) -> Dict[str, Any]:
        """
        Runs a lark-cli command and returns the parsed JSON output.
        """
        if not os.path.exists(self.bin_path):
            return {
                "status": "error",
                "message": f"CLI binary not found at {self.bin_path}. Please compile it first."
            }

        # 设置 CLI 识别的环境变量
        env = os.environ.copy()
        env["LARKSUITE_CLI_APP_ID"] = app_id
        env["LARKSUITE_CLI_USER_ACCESS_TOKEN"] = access_token
        # 禁用更新检查和技能同步通知，确保输出纯净
        env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"

        full_args = [self.bin_path, service, command] + args
        
        try:
            logger.info(f"Executing CLI command: {' '.join(full_args)}")
            result = subprocess.run(
                full_args,
                env=env,
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode != 0:
                return {
                    "status": "error",
                    "message": result.stderr.strip() or f"CLI exited with code {result.returncode}"
                }

            # CLI 正常输出应该是 JSON
            try:
                output = json.loads(result.stdout)
                return {"status": "success", "data": output}
            except json.JSONDecodeError:
                # 如果不是 JSON，尝试直接返回文本
                return {"status": "success", "content": result.stdout.strip()}

        except Exception as e:
            logger.error(f"Failed to execute CLI command: {str(e)}")
            return {"status": "error", "message": str(e)}

cli_client = CLIClient()
