import os
import subprocess
import json
import logging
import tempfile
import shutil
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class PackagedCLIClient:
    """
    Base wrapper for packaged CLI binaries.

    The wrapper intentionally avoids shell=True. Callers pass argv fragments and
    this class handles binary resolution, isolated HOME, timeout, and JSON output
    normalization.
    """

    def __init__(self, bin_name: str, bin_path: Optional[str] = None):
        self.bin_name = bin_name
        self.bin_path = bin_path or self._resolve_bin_path(bin_name)

    def _resolve_bin_path(self, bin_name: str) -> str:
        package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(package_root, "bin", bin_name)

    def _ensure_executable(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.bin_path):
            return {
                "status": "error",
                "message": (
                    f"CLI binary '{self.bin_name}' not found at {self.bin_path}. "
                    "Please download/build it and place it in the packaged bin/ directory."
                ),
            }

        try:
            if not os.access(self.bin_path, os.X_OK):
                os.chmod(self.bin_path, 0o755)
        except Exception as e:
            logger.warning(f"Failed to set executable permission on {self.bin_path}: {e}")

        return None

    def _parse_stdout(self, stdout: str) -> Dict[str, Any]:
        stdout = stdout.strip()
        if not stdout:
            return {"status": "success", "data": None}

        try:
            return {"status": "success", "data": json.loads(stdout)}
        except json.JSONDecodeError:
            pass

        # gws can emit NDJSON for paginated operations. Preserve line order.
        lines = [line for line in stdout.splitlines() if line.strip()]
        if len(lines) > 1:
            parsed_lines = []
            for line in lines:
                try:
                    parsed_lines.append(json.loads(line))
                except json.JSONDecodeError:
                    return {"status": "success", "content": stdout}
            return {"status": "success", "data": parsed_lines, "format": "ndjson"}

        return {"status": "success", "content": stdout}

    def _format_args_for_log(self, args: List[str]) -> str:
        redacted_flags = {
            "--body",
            "--data",
            "--description",
            "--json",
            "--json-values",
            "--markdown",
            "--params",
            "--subject",
            "--text",
        }
        safe_args = []
        redact_next = False
        for arg in args:
            if redact_next:
                safe_args.append("<redacted>")
                redact_next = False
                continue

            lower_arg = arg.lower()
            safe_args.append(arg if len(arg) <= 120 else f"{arg[:117]}...")
            if lower_arg in redacted_flags:
                redact_next = True

        return " ".join(safe_args)

    def _run(
        self,
        args: List[str],
        env: Dict[str, str],
        tmp_home: str,
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        binary_error = self._ensure_executable()
        if binary_error:
            return binary_error

        full_args = [self.bin_path] + args
        try:
            logger.info(
                "Executing packaged CLI in isolated home %s: %s",
                tmp_home,
                self._format_args_for_log(full_args),
            )
            result = subprocess.run(
                full_args,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )

            if result.returncode != 0:
                return {
                    "status": "error",
                    "exit_code": result.returncode,
                    "message": result.stderr.strip()
                    or result.stdout.strip()
                    or f"CLI exited with code {result.returncode}",
                }

            return self._parse_stdout(result.stdout)

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"CLI execution timed out after {timeout_seconds} seconds.",
            }
        except Exception as e:
            logger.error(f"Failed to execute CLI command: {str(e)}")
            return {"status": "error", "message": str(e)}


class CLIClient(PackagedCLIClient):
    """
    Wrapper for lark-cli (Go binary).
    Handles authentication via environment variables and executes commands.
    """

    def __init__(self, bin_path: Optional[str] = None):
        super().__init__("lark-cli", bin_path)

    def run_command(self, service: str, command: str, args: list, access_token: str, app_id: str) -> Dict[str, Any]:
        """
        Runs a lark-cli command and returns the parsed JSON output.
        """
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
            return self._run(full_args[1:], env, tmp_home, timeout_seconds=120)
        finally:
            # 执行完毕后清理临时目录
            try:
                shutil.rmtree(tmp_home, ignore_errors=True)
            except:
                pass


class GoogleWorkspaceCLIClient(PackagedCLIClient):
    """
    Wrapper for Google Workspace CLI (`gws`).

    Authentication is intentionally token-injection first. The caller should
    obtain a user OAuth access token from the surrounding ADK/GE auth context
    and pass it via GOOGLE_WORKSPACE_CLI_TOKEN. Interactive `gws auth login`
    is not suitable for Agent Engine / Cloud Run runtime.
    """

    def __init__(self, bin_path: Optional[str] = None):
        super().__init__("gws", bin_path)

    def _configure_ca_bundle(self, env: Dict[str, str]) -> None:
        ca_bundle = env.get("SSL_CERT_FILE") or env.get("REQUESTS_CA_BUNDLE")
        if not ca_bundle:
            try:
                import certifi

                ca_bundle = certifi.where()
            except Exception as e:
                logger.warning("Failed to resolve certifi CA bundle for gws: %s", e)

        if ca_bundle:
            env["SSL_CERT_FILE"] = ca_bundle
            env["REQUESTS_CA_BUNDLE"] = ca_bundle

    def run_command(
        self,
        args: list,
        access_token: str,
        project_id: str = "",
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        if not access_token:
            return {"status": "error", "message": "Missing Google Workspace access token."}

        tmp_home = tempfile.mkdtemp(prefix="gws_cli_")
        env = os.environ.copy()
        env["HOME"] = tmp_home
        env["NO_COLOR"] = "1"
        env["GOOGLE_WORKSPACE_CLI_TOKEN"] = str(access_token)
        env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = os.path.join(tmp_home, ".config", "gws")
        env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] = "file"
        self._configure_ca_bundle(env)
        if project_id:
            env["GOOGLE_WORKSPACE_PROJECT_ID"] = str(project_id)

        try:
            return self._run(args, env, tmp_home, timeout_seconds=timeout_seconds)
        finally:
            try:
                shutil.rmtree(tmp_home, ignore_errors=True)
            except:
                pass


cli_client = CLIClient()
gws_cli_client = GoogleWorkspaceCLIClient()
