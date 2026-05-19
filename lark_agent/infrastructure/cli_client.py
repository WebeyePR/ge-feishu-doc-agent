import os
import subprocess
import json
import logging
import tempfile
import shutil
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class CLIClient:
    """
    Wrapper for lark-cli (Go binary).
    Handles authentication via environment variables and executes commands.
    """
    
    def __init__(self, bin_path: Optional[str] = None):
        if bin_path:
            self.bin_path = bin_path
            return

        # 1. 尝试在当前包的 bin 目录下查找 (部署后的结构)
        # lark_agent/infrastructure/cli_client.py -> lark_agent/bin/
        package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        package_bin_path = os.path.join(package_root, "bin", "lark-cli")
        
        # 2. 尝试在项目根目录的 bin 目录下查找 (本地开发结构)
        project_root = os.path.dirname(package_root)
        project_bin_path = os.path.join(project_root, "bin", "lark-cli")

        if os.path.exists(package_bin_path):
            self.bin_path = package_bin_path
        elif os.path.exists(project_bin_path):
            self.bin_path = project_bin_path
        else:
            # 默认路径
            self.bin_path = package_bin_path
        
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

        # 确保二进制文件具有可执行权限 (处理从 .whl 解压后权限丢失的情况)
        try:
            if not os.access(self.bin_path, os.X_OK):
                os.chmod(self.bin_path, 0o755)
        except Exception as e:
            logger.warning(f"Failed to set executable permission on {self.bin_path}: {e}")

        # 设置 CLI 识别的环境变量
        if not app_id:
            return {"status": "error", "message": "Missing LARK_CLIENT_ID configuration."}
        if not access_token:
            return {"status": "error", "message": "Missing User Access Token."}

        # 创建临时的 HOME 目录以实现请求间的完全隔离
        tmp_home = tempfile.mkdtemp(prefix="lark_cli_")
        
        env = os.environ.copy()
        env["HOME"] = tmp_home
        env["LARKSUITE_CLI_APP_ID"] = str(app_id)
        env["LARKSUITE_CLI_USER_ACCESS_TOKEN"] = str(access_token)
        # 禁用更新检查和技能同步通知，确保输出纯净
        env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"

        # 构建命令参数
        full_args = [self.bin_path]
        if service:
            full_args.append(service)
        if command:
            full_args.append(command)
        full_args.extend(args)
        
        try:
            logger.info(f"Executing CLI command in isolated home {tmp_home}: {' '.join(full_args)}")
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
        finally:
            # 执行完毕后清理临时目录
            try:
                shutil.rmtree(tmp_home, ignore_errors=True)
            except:
                pass

cli_client = CLIClient()
