import json
import logging
import re
import time


from google.adk.tools import ToolContext

from nexus_agent.config import (
    GOOGLE_WORKSPACE_AUTH_ID,
    GOOGLE_WORKSPACE_PROJECT_ID,
    LARK_AUTH_ID,
    LARK_CLIENT_ID,
)
from nexus_agent.gws_registry import (
    find_command_for_args,
    get_command_spec,
    search_commands,
)
from nexus_agent.lark_registry import (
    find_command_for_args as find_lark_command_for_args,
    get_command_spec as get_lark_command_spec_impl,
    search_commands as search_lark_commands,
)
from nexus_agent.infrastructure import lark_api_repository
from nexus_agent.infrastructure.cli_client import cli_client, gws_cli_client

logger = logging.getLogger(__name__)

STATUS_KEY = "status"
MESSAGE_KEY = "message"
DOCUMENTS_KEY = "documents"
DOCUMENTS_TOKEN = "documents_token"

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

GWS_ALLOWED_SERVICES = {
    "admin",
    "calendar",
    "chat",
    "classroom",
    "docs",
    "drive",
    "events",
    "forms",
    "gmail",
    "keep",
    "meet",
    "modelarmor",
    "people",
    "script",
    "sheets",
    "slides",
    "tasks",
    "workflow",
}
GWS_SAFE_META_COMMANDS = {"schema", "--help", "help", "--version", "version"}
GWS_MUTATING_METHODS = {
    "append",
    "batchclear",
    "batchdelete",
    "batchupdate",
    "clear",
    "copy",
    "create",
    "delete",
    "emptytrash",
    "import",
    "insert",
    "modify",
    "move",
    "patch",
    "remove",
    "send",
    "set",
    "stop",
    "trash",
    "undelete",
    "untrash",
    "update",
    "watch",
}
GWS_MUTATING_HELPERS = {
    "+append",
    "+forward",
    "+insert",
    "+reply",
    "+reply-all",
    "+send",
    "+upload",
    "+write",
}


LARK_ALLOWED_SERVICES = {
    "approval",
    "apps",
    "attendance",
    "base",
    "calendar",
    "contact",
    "doc",
    "docs",
    "drive",
    "event",
    "im",
    "mail",
    "markdown",
    "minutes",
    "okr",
    "api",
    "vc",
    "whiteboard",
    "wiki",
}
LARK_SAFE_META_COMMANDS = {"schema", "--help", "help", "--version", "version"}
LARK_MUTATING_METHODS = {
    "create",
    "delete",
    "update",
    "patch",
    "post",
    "put",
    "send",
    "reply",
    "add",
    "remove",
    "set",
    "upload",
    "publish",
    "rsvp",
    "batch_delete",
    "batch_create",
    "clear",
    "cancel",
    "close",
    "open",
    "agree",
    "reject",
    "transfer",
}
LARK_MUTATING_HELPERS = {
    "+create",
    "+update",
    "+delete",
    "+send",
    "+reply",
    "+rsvp",
    "+html-publish",
    "+access-scope-set",
    "+event-subscribe",
}



def _normalize_markdown_for_new_doc(title: str, markdown: str) -> str:
    """
    Normalize generated markdown before create-doc.

    Feishu MCP create-doc already uses `title` as the document title, so we strip
    a duplicated leading H1 if it matches the title to avoid repeated headings.
    """
    if not markdown:
        return markdown

    normalized = markdown.strip()
    title_clean = (title or "").strip()
    if not title_clean:
        return normalized

    pattern = rf"^#\s+{re.escape(title_clean)}\s*(\r?\n)+"
    normalized = re.sub(pattern, "", normalized, count=1, flags=re.IGNORECASE)
    return normalized.strip()


def get_access_token(tool_context: ToolContext) -> str:
    """
    Get the access token from the tool context.
    """
    if isinstance(tool_context, str):
        return tool_context
    else:
        return tool_context.state.get(f"{LARK_AUTH_ID}")


def get_google_workspace_access_token(tool_context: ToolContext) -> str:
    """
    Get the Google Workspace access token from the tool context.

    The expected token should carry the Workspace scopes needed by the target
    gws command. The CLI only consumes access tokens; refresh must happen in the
    surrounding OAuth integration.
    """
    if isinstance(tool_context, str):
        return tool_context
    return tool_context.state.get(f"{GOOGLE_WORKSPACE_AUTH_ID}")


def _clean_markdown_from_plain_text(text: str) -> str:
    """
    清洗文本中的 Markdown 格式，防止其泄漏在 Plain Text 的邮件中。
    1. 将标题（# 标题）转换为更优雅的商务纯文本：★ 标题 ★ 或 【标题】
    2. 去除加粗/斜体控制符：**文本** -> 文本，*文本* -> 文本
    3. 清洗代码块反引号：``` -> 空白
    4. 保留无害的排版列表符号（如 '- ' 和 '1. '），并将 '\\n' 还原为真正的换行符
    """
    if not isinstance(text, str):
        return text
    
    # 物理洗涤还原：将大模型可能幻觉出的字面量 \\n 强行还原为真实的换行符
    text = text.replace("\\n", "\n")
    
    import re
    # 1. 匹配并清洗 Markdown 标题：如 '\n# Title\n' -> '\n★ TITLE ★\n'
    def replace_header(match):
        level = len(match.group(1))
        content = match.group(2).strip()
        if level == 1:
            return f"\n★ {content.upper()} ★\n"
        else:
            return f"\n【{content}】\n"
            
    text = re.sub(r'^(#{1,6})\s+(.+)$', replace_header, text, flags=re.MULTILINE)
    
    # 2. 清洗加粗/斜体控制符
    text = re.sub(r'\*{3}(.*?)\*{3}', r'\1', text) # ***bold-italic***
    text = re.sub(r'\*{2}(.*?)\*{2}', r'\1', text) # **bold**
    text = re.sub(r'_(.*?)_', r'\1', text)         # _italic_
    text = re.sub(r'\*(.*?)\*', r'\1', text)       # *italic*
    text = re.sub(r'__([\s\S]*?)__', r'\1', text)   # __bold__
    
    # 3. 清洗代码块反引号
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    
    # 4. 去除行尾多余空格
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        cleaned_lines.append(line.rstrip())
        
    return "\n".join(cleaned_lines)


def _run_google_workspace_cli(args: list, tool_context: ToolContext, timeout_seconds: int = 120) -> dict:
    try:
        # 物理洗涤层：对 gmail 发信 `--body` 参数进行无损 Markdown 降维净化洗涤
        for i in range(len(args)):
            if isinstance(args[i], str):
                if args[i] == "--body" and i + 1 < len(args) and isinstance(args[i+1], str):
                    args[i+1] = _clean_markdown_from_plain_text(args[i+1])

        # 物理洗涤层：抗幻觉自愈，将大模型在命令行参数中可能幻觉出的字面量 \\n 强行还原为真实的换行符
        args = [arg.replace("\\n", "\n") if isinstance(arg, str) else arg for arg in args]
        
        access_token = get_google_workspace_access_token(tool_context)
        if not access_token:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: (
                    "Google Workspace authentication required. "
                    f"Missing token in tool_context.state['{GOOGLE_WORKSPACE_AUTH_ID}']."
                ),
            }
        return gws_cli_client.run_command(
            args=args,
            access_token=access_token,
            project_id=GOOGLE_WORKSPACE_PROJECT_ID,
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        logger.exception("Google Workspace CLI execution failed unexpectedly.")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Google Workspace CLI execution failed: {e}",
        }


def _safe_limit(value: int, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _escape_google_query_literal(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("'", "\\'")


def _robust_clean_json(json_str: str, field_name: str = "json") -> str:
    """
    对 AI 传进来的 JSON 字符串进行智能防御性容错清洗，解决多重转义和引号不规范引起的解析错乱。
    """
    if json_str in (None, ""):
        return ""
    
    if not isinstance(json_str, str):
        # 若直接传入 Python 字典或列表，直接序列化返回
        return json.dumps(json_str, ensure_ascii=False)
        
    cleaned = json_str.strip()
    if not cleaned:
        return ""
        
    # 容错 1：去除画蛇添足的外层单/双引号包裹，如 '{"foo": "bar"}' -> {"foo": "bar"}
    if (cleaned.startswith("'") and cleaned.endswith("'")) or (cleaned.startswith('"') and cleaned.endswith('"')):
        candidate = cleaned[1:-1].strip()
        if (candidate.startswith("{") and candidate.endswith("}")) or (candidate.startswith("[") and candidate.endswith("]")):
            cleaned = candidate

    # 容错 2：测试原样标准加载
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    # 容错 3：尝试将单引号替换为双引号，常发生于 AI 混用或漏掉了转义，如 {'pageSize': 10} -> {"pageSize": 10}
    try:
        replaced = cleaned.replace("'", '"')
        json.loads(replaced)
        return replaced
    except json.JSONDecodeError:
        pass

    # 容错 4：如果首尾都不是 { } 且包含了 `\"`，有可能是首尾的双引号被多余反序列化了
    try:
        decoded_once = json.loads(f'"{cleaned}"')
        json.loads(decoded_once)
        return decoded_once
    except:
        pass

    # 修复失败，抛出带有清晰示例和诊断指引的异常，帮助 AI 能够在第二次调用中 100% 自我修正
    try:
        json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"The field '{field_name}' must be a valid, standard JSON string.\n"
            f"Parsing Error: {exc}\n"
            f"Value Received: {json_str}\n"
            f"Please ensure:\n"
            f"1. Keys and values are enclosed in DOUBLE QUOTES (e.g., \"key\": \"value\").\n"
            f"2. Single quotes are NOT used as JSON delimiters.\n"
            f"3. No unnecessary shell escaping (e.g., avoid multiple backslashes \\\\\\) is added."
        )


def _coerce_json_cli_arg(value, field_name: str) -> str:
    """
    Convert a JSON string or Python object into a CLI JSON argument with robust parsing.
    """
    return _robust_clean_json(value, field_name)



def _parse_json_array_arg(value, field_name: str) -> list:
    """
    Parse a JSON array string while keeping direct Python list calls compatible.
    """
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON array string.")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be a valid JSON array string: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} must be a JSON array string.")
    return parsed


def _parse_gws_args(args_json: str) -> list:
    args = _parse_json_array_arg(args_json, "args_json")
    if not args:
        raise ValueError("args_json must be a non-empty JSON array.")
    if len(args) > 40:
        raise ValueError("args_json must contain at most 40 arguments.")
    normalized = []
    for arg in args:
        if not isinstance(arg, str):
            raise ValueError("args_json must contain strings only.")
        if "\x00" in arg or "\n" in arg or "\r" in arg:
            raise ValueError("args_json arguments must not contain control characters.")
        if len(arg) > 20000:
            raise ValueError("args_json contains an argument that is too long.")
        normalized.append(arg)
    return normalized


def _is_gws_mutating_args(args: list) -> bool:
    normalized_args = [arg.lower() for arg in args]
    if any(arg in GWS_MUTATING_HELPERS for arg in normalized_args):
        return True
    if any(arg == "--upload" for arg in normalized_args):
        return True
    return any(arg.split(".")[-1] in GWS_MUTATING_METHODS for arg in normalized_args)


def _parse_gws_resource_path(resource: str) -> list:
    if not resource or not resource.strip():
        return []

    parts = resource.strip().split()
    for part in parts:
        if part.startswith("-") or "/" in part or ".." in part:
            raise ValueError("resource contains an invalid path segment.")
        if "\x00" in part or "\n" in part or "\r" in part:
            raise ValueError("resource must not contain control characters.")
    return parts


def _validate_gws_args(args: list, allow_mutating: bool) -> None:
    first = args[0]
    if first.startswith("-") and first not in GWS_SAFE_META_COMMANDS:
        raise ValueError("First gws argument must be a service name or a safe meta command.")
    if first not in GWS_ALLOWED_SERVICES and first not in GWS_SAFE_META_COMMANDS:
        raise ValueError(
            "Unsupported gws service. Allowed services: "
            + ", ".join(sorted(GWS_ALLOWED_SERVICES))
            + "."
        )

    blocked_flags = {
        "--output",
        "--output-dir",
        "--dir",
        "--credentials",
        "--credentials-file",
    }
    for arg in args:
        if arg.startswith("/") or ".." in arg:
            raise ValueError("Absolute paths and parent-directory traversal are not allowed.")
        if arg in blocked_flags:
            raise ValueError(f"Flag {arg} is not allowed in the generic gws executor.")

    mutating = _is_gws_mutating_args(args)
    if mutating and not allow_mutating and "--dry-run" not in args:
        raise ValueError(
            "This gws command appears to mutate data. Re-run with dry_run=True for preview "
            "or allow_mutating=True after explicit user confirmation."
        )


def discover_google_workspace_operations(
    query: str = "",
    service: str = "",
    intent: str = "",
    resource: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """
    Discover Google Workspace CLI operations from the local command registry.

    Args:
        query: Natural-language capability query, e.g. "list drive files".
        service: Optional gws service name, e.g. drive, sheets, gmail.
        intent: Optional intent filter such as read, write, create, or send.
        resource: Optional resource path under the service, separated by spaces, e.g. "files".
        tool_context: Accepted for ADK compatibility; registry lookup does not require OAuth.
    """
    service_name = service.strip().lower() if service else ""
    if service_name and service_name not in GWS_ALLOWED_SERVICES:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: f"Unsupported gws service: {service_name}"}

    try:
        resource_parts = _parse_gws_resource_path(resource)
    except ValueError as e:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}

    matches = search_commands(
        query=query or "",
        service=service_name,
        intent=intent or "",
        resource_parts=resource_parts,
    )
    return {
        STATUS_KEY: STATUS_SUCCESS,
        "source": "registry",
        "matches": matches,
        MESSAGE_KEY: (
            "Use get_google_workspace_command_spec(command_id) before executing. "
            "If no registry match fits, use get_google_workspace_operation_schema(method_path) "
            "with a real path such as drive.files.list."
        ),
    }


def get_google_workspace_command_spec(command_id: str) -> dict:
    """
    Return a registry-backed gws command specification for one command_id.
    """
    spec = get_command_spec(command_id)
    if not spec:
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: (
                "Google Workspace command_id was not found in the local registry. "
                "Use discover_google_workspace_operations first, or fall back to "
                "get_google_workspace_operation_schema for a real gws schema path."
            ),
        }
    return {
        STATUS_KEY: STATUS_SUCCESS,
        **spec,
        MESSAGE_KEY: (
            "Registry command spec returned. Build execute_google_workspace_cli args "
            "from argv_template and examples; do not invent shell commands."
        ),
    }


def get_google_workspace_operation_schema(method_path: str, tool_context: ToolContext) -> dict:
    """
    Fetch a gws operation schema, e.g. drive.files.list or sheets.spreadsheets.create.
    """
    if not method_path or not method_path.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "method_path is required."}
    safe_method = method_path.strip()
    if safe_method.startswith("-") or "/" in safe_method or ".." in safe_method:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "method_path is invalid."}
    service = safe_method.split(".", 1)[0]
    if service not in GWS_ALLOWED_SERVICES:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: f"Unsupported gws service: {service}"}
    result = _run_google_workspace_cli(["schema", safe_method], tool_context, timeout_seconds=60)
    if result.get(STATUS_KEY) != STATUS_SUCCESS:
        return result

    schema_payload = result.get("data", result.get("content"))
    if not isinstance(schema_payload, str):
        schema_payload = json.dumps(schema_payload, ensure_ascii=False)

    return {
        STATUS_KEY: STATUS_SUCCESS,
        "method_path": safe_method,
        "schema_json": schema_payload,
        MESSAGE_KEY: (
            "Schema fetched successfully. schema_json is a JSON string; parse it before "
            "constructing a generic gws command."
        ),
    }


def execute_google_workspace_cli(
    args_json: str,
    tool_context: ToolContext,
    dry_run: bool = True,
    allow_mutating: bool = False,
    timeout_seconds: int = 120,
) -> dict:
    """
    Execute a controlled Google Workspace CLI command.

    Args:
        args_json: JSON array of gws arguments, without the binary name. Example:
            ["drive", "files", "list", "--params", "{\"pageSize\":10}", "--fields", "files(id,name)"]
        tool_context: Tool execution context containing the Google Workspace OAuth token.
        dry_run: If True, append --dry-run to mutating commands that do not already include it.
        allow_mutating: Must be True to run mutating commands without --dry-run.
        timeout_seconds: Command timeout, capped to 300 seconds.
    """
    try:
        args = _parse_gws_args(args_json)
        command_spec = find_command_for_args(args)
        registry_marks_mutating = bool(command_spec and command_spec.get("kind") != "read")
        if dry_run and (registry_marks_mutating or _is_gws_mutating_args(args)) and "--dry-run" not in args:
            args.append("--dry-run")
        _validate_gws_args(args, allow_mutating=allow_mutating)
    except ValueError as e:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}

    timeout_seconds = _safe_limit(timeout_seconds, default=120, minimum=10, maximum=300)
    result = _run_google_workspace_cli(args, tool_context, timeout_seconds=timeout_seconds)
    if command_spec:
        result = dict(result)
        result["command_id"] = command_spec["command_id"]
        result["command_kind"] = command_spec["kind"]
        result["requires_confirmation"] = command_spec["requires_confirmation"]
    return result


def execute_google_workspace_cli_flat(
    service: str,
    resource: str,
    method: str,
    tool_context: ToolContext,
    params_json: str = "",
    json_body: str = "",
    upload_file: str = "",
    page_all: bool = False,
    dry_run: bool = True,
    allow_mutating: bool = False,
    timeout_seconds: int = 120,
) -> dict:
    """
    [Universal Tool] Execute a generic Google Workspace CLI command safely by providing flat arguments.
    **CRITICAL**: Prefer this tool over the traditional array tool to prevent nested JSON shell escaping issues!
    This tool safely packages your parameters into a physical argv array and runs 'gws' in the background.

    Args:
        service: Google service name (e.g., 'drive', 'sheets', 'gmail', 'calendar').
        resource: Resource type path (e.g., 'files', 'spreadsheets', 'users messages', 'events').
        method: Action name (e.g., 'list', 'get', 'create', 'update', 'delete').
        tool_context: Tool execution context containing the OAuth token.
        params_json: Optional. Flat JSON string of query parameters, e.g., '{"pageSize": 10}'. NO nested double quotes!
        json_body: Optional. Flat JSON string of the request body, e.g., '{"properties": {"title": "My Sheet"}}'. NO nested double quotes!
        upload_file: Optional. Local file path to upload (e.g., for drive.files.create).
        page_all: Optional. Set to True to retrieve all pages for paginated queries.
        dry_run: Optional. If True, append --dry-run to mutating commands.
        allow_mutating: Optional. Must be True to run mutating commands without dry_run.
        timeout_seconds: Optional. Command execution timeout, capped to 300s.
    """
    try:
        service_clean = service.strip().lower()
        if service_clean not in GWS_ALLOWED_SERVICES:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: f"Unsupported gws service: {service_clean}"}

        args = [service_clean]
        
        # 解析 resource (如 "users messages" -> ["users", "messages"])
        resource_parts = _parse_gws_resource_path(resource)
        args.extend(resource_parts)
        
        # 添加 method
        method_clean = method.strip()
        if not method_clean or method_clean.startswith("-"):
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: f"Invalid method name: {method}"}
        args.append(method_clean)
        
        # 验证并添加 params
        if params_json and params_json.strip():
            params_arg = _coerce_json_cli_arg(params_json, "params_json")
            args.extend(["--params", params_arg])
            
        # 验证并添加 json_body
        if json_body and json_body.strip():
            json_arg = _coerce_json_cli_arg(json_body, "json_body")
            args.extend(["--json", json_arg])
            
        # 验证并添加 upload_file
        if upload_file and upload_file.strip():
            file_clean = upload_file.strip()
            if "/" in file_clean or ".." in file_clean:
                return {
                    STATUS_KEY: STATUS_ERROR, 
                    MESSAGE_KEY: "Absolute paths and parent-directory traversal are not allowed for upload_file."
                }
            args.extend(["--upload", file_clean])
            
        if page_all:
            args.append("--page-all")
            
        # 校验和自动 dry-run
        command_spec = find_command_for_args(args)
        registry_marks_mutating = bool(command_spec and command_spec.get("kind") != "read")
        if dry_run and (registry_marks_mutating or _is_gws_mutating_args(args)) and "--dry-run" not in args:
            args.append("--dry-run")
            
        _validate_gws_args(args, allow_mutating=allow_mutating)
        
    except Exception as e:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}

    timeout_seconds = _safe_limit(timeout_seconds, default=120, minimum=10, maximum=300)
    result = _run_google_workspace_cli(args, tool_context, timeout_seconds=timeout_seconds)
    
    if command_spec:
        result = dict(result)
        result["command_id"] = command_spec["command_id"]
        result["command_kind"] = command_spec["kind"]
        result["requires_confirmation"] = command_spec["requires_confirmation"]
    return result


def search_google_drive_files(query: str, tool_context: ToolContext, page_size: int = 10) -> dict:
    """
    Search Google Drive files for the authenticated Google Workspace user.

    Args:
        query: Keyword to match in file names. Empty query lists recent non-trashed files.
        tool_context: Tool execution context containing the Google Workspace OAuth token.
        page_size: Maximum number of files to return. Capped to 25 for context safety.

    Returns:
        dict: Normalized gws JSON result.
    """
    page_size = _safe_limit(page_size, default=10, minimum=1, maximum=25)
    drive_query = "trashed = false"
    if query and query.strip():
        drive_query = f"name contains '{_escape_google_query_literal(query.strip())}' and trashed = false"

    fields = "files(id,name,mimeType,webViewLink,modifiedTime),nextPageToken"
    params = {"q": drive_query, "pageSize": page_size}
    return _run_google_workspace_cli(
        [
            "drive",
            "files",
            "list",
            "--params",
            json.dumps(params, ensure_ascii=False),
            "--fields",
            fields,
        ],
        tool_context,
    )


def append_google_doc_text(document_id: str, text: str, tool_context: ToolContext) -> dict:
    """
    Append plain text to a Google Docs document using gws docs +write.

    This helper intentionally supports plain text only. Rich formatting should
    be implemented later with a dedicated, reviewed Docs batchUpdate wrapper.
    """
    if not document_id or not document_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "document_id is required."}
    if not text:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "text is required."}

    return _run_google_workspace_cli(
        ["docs", "+write", "--document", document_id.strip(), "--text", text],
        tool_context,
    )


def create_google_doc_with_text(title: str, text: str, tool_context: ToolContext) -> dict:
    """
    Create a Google Docs document with high-fidelity formatting.
    This converts input Markdown text into standard HTML, uploads it to Google Drive
    specifying target Doc conversions to automatically render perfect rich-text.
    """
    if not title or not title.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "title is required."}
    if not text:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "text is required."}

    import uuid
    import os
    import re
    temp_filename = f"temp_gdoc_import_{uuid.uuid4().hex[:8]}.html"
    try:
        # 1. 图像预清洗（Image Pre-cleaning）：
        # 将本地相对产物图和私有/有鉴权的飞书图转换成设计精美的企业安全隔离提示卡，以防在 Google Docs 中出现损坏裂图；
        # 公开公网直链图保留为标准 HTML <img>，支持 Google 官方服务器原生抓取并嵌入。
        img_pattern = r'!\[(.*?)\]\((https?://[^\s)]+|[^\s)]+)\)'
        
        def replace_img_tag_for_gdoc(match):
            alt = match.group(1) or "图片"
            url = match.group(2)
            
            is_private = False
            if not (url.startswith("http://") or url.startswith("https://")):
                is_private = True
            elif any(domain in url for domain in ["feishu.cn", "larksuite.com", "feishu-open.cn"]):
                is_private = True
            elif "authcode" in url:
                is_private = True
                
            if is_private:
                filename = url.split("/")[-1] if "/" in url else url
                if "?" in filename:
                    filename = filename.split("?")[0]
                if not filename or len(filename) < 3 or filename == "authcode":
                    filename = "lark_embedded_image.png"
                    
                original_link_html = f'<p style="margin: 6px 0 0 0; font-size: 12px;"><a href="{url}" target="_blank" style="color: #1a73e8; text-decoration: underline;">点击安全总线外部通道查看原图 (Open Original Link)</a></p>' if url.startswith("http") else ""
                
                return f'''
<table cellpadding="12" cellspacing="0" border="1" style="border: 1px dashed #4a90e2; background-color: #f4f8fa; width: 100%; border-collapse: collapse;">
  <tr>
    <td>
      <p style="color: #2c3e50; font-weight: bold; font-size: 14px; margin: 0 0 8px 0;">📷 [企业安全隔离图片 / Secure Embedded Image]</p>
      <p style="font-size: 12px; color: #555555; margin: 0 0 8px 0; line-height: 1.5;">该图片包含非公开权限或带动态鉴权参数。为保障多端预览安全，已自动隔离并托管。您可以在聊天会话的右侧「产物/Artifacts」面板或工作区中，直接查看高保真大图：</p>
      <p style="font-size: 12px; font-weight: bold; color: #1a73e8; margin: 0 0 8px 0;">📂 产物名称：<code>{filename}</code></p>
      {original_link_html}
    </td>
  </tr>
</table>
'''
            else:
                return f'<img src="{url}" alt="{alt}" style="max-width: 100%; height: auto; margin: 10px 0; border-radius: 4px;" />'

        cleaned_text = re.sub(img_pattern, replace_img_tag_for_gdoc, text)

        # 2. Markdown 排版智能纠偏（Markdown Preprocessor）：
        # 针对大模型经常漏掉空行导致 python-markdown 解析表格/列表/水平线失败的问题，
        # 在块级元素（表格、水平线、列表项、引用块、多级标题）之前，如果缺失空行则智能自动补全，
        # 确保 100% 完美触发 HTML 格式转换。
        lines = cleaned_text.split("\n")
        preprocessed_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if i > 0:
                prev_line = preprocessed_lines[-1]
                prev_stripped = prev_line.strip()
                
                # a) 表格起始行补空行
                if stripped.startswith("|") and stripped.endswith("|"):
                    if prev_stripped and not (prev_stripped.startswith("|") and prev_stripped.endswith("|")):
                        preprocessed_lines.append("")
                # b) 水平线起始行补空行
                elif re.match(r"^(\-{3,}|\*{3,}|\_{3,})$", stripped):
                    if prev_stripped:
                        preprocessed_lines.append("")
                # d) 引用块起始行补空行
                elif stripped.startswith(">"):
                    if prev_stripped and not prev_stripped.startswith(">"):
                        preprocessed_lines.append("")
                # e) 标题起始行补空行
                elif stripped.startswith("#"):
                    if prev_stripped:
                        preprocessed_lines.append("")
            preprocessed_lines.append(line)
        final_markdown_text = "\n".join(preprocessed_lines)

        # 3. 编译为高保真 HTML
        html_content = ""
        try:
            import markdown
            # 引入 standard extensions 保证高品质翻译
            html_content = markdown.markdown(final_markdown_text, extensions=['tables', 'fenced_code', 'nl2br'])
        except ImportError:
            logger.warning("[create_google_doc_with_text] markdown module not found. Falling back to robust custom parser.")
            
            # 🌟 极致增强的高阶内置 Markdown Fallback 编译器
            # 即使在沙箱中缺失编译包，也能高品质、完整地将多级标题、列表、多维表格、引用块、水平线和行内富格式全部编译
            lines = final_markdown_text.split("\n")
            converted_lines = []
            
            in_list = False
            in_ordered_list = False
            in_quote = False
            in_code = False
            in_table = False
            table_header_parsed = False
            
            def inline_replace(t):
                # 1. 优先解析超链接 Markdown 格式 [text](url) -> <a href="url">text</a>
                t = re.sub(r'\[(.*?)\]\((https?://[^\s)]+|[^\s)]+)\)', r'<a href="\2">\1</a>', t)
                # 2. 解析加粗 **text** 或 __text__
                t = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', t)
                t = re.sub(r'__(.*?)__', r'<strong>\1</strong>', t)
                # 3. 解析斜体 *text* 或 _text_
                t = re.sub(r'\*(.*?)\*', r'<em>\1</em>', t)
                t = re.sub(r'_(.*?)_', r'<em>\1</em>', t)
                # 4. 解析行内代码 `code`
                t = re.sub(r'`(.*?)`', r'<code>\1</code>', t)
                return t

            for line in lines:
                stripped = line.strip()
                
                # a) 代码块
                if stripped.startswith("```"):
                    if in_code:
                        converted_lines.append("</code></pre>")
                        in_code = False
                    else:
                        if in_list: converted_lines.append("</ul>"); in_list = False
                        if in_ordered_list: converted_lines.append("</ol>"); in_ordered_list = False
                        if in_quote: converted_lines.append("</blockquote>"); in_quote = False
                        if in_table: converted_lines.append("</table>"); in_table = False; table_header_parsed = False
                        
                        lang = stripped[3:].strip()
                        converted_lines.append(f'<pre><code class="{lang}">' if lang else "<pre><code>")
                        in_code = True
                    continue
                    
                if in_code:
                    escaped_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    converted_lines.append(escaped_line)
                    continue

                # b) 水平分割线
                if re.match(r"^(\-{3,}|\*{3,}|\_{3,})$", stripped):
                    if in_list: converted_lines.append("</ul>"); in_list = False
                    if in_ordered_list: converted_lines.append("</ol>"); in_ordered_list = False
                    if in_quote: converted_lines.append("</blockquote>"); in_quote = False
                    if in_table: converted_lines.append("</table>"); in_table = False; table_header_parsed = False
                    
                    converted_lines.append("<hr />")
                    continue

                # c) 多级标题
                header_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
                if header_match:
                    if in_list: converted_lines.append("</ul>"); in_list = False
                    if in_ordered_list: converted_lines.append("</ol>"); in_ordered_list = False
                    if in_quote: converted_lines.append("</blockquote>"); in_quote = False
                    if in_table: converted_lines.append("</table>"); in_table = False; table_header_parsed = False
                    
                    level = len(header_match.group(1))
                    content = inline_replace(header_match.group(2))
                    converted_lines.append(f"<h{level}>{content}</h{level}>")
                    continue

                # d) 多维表格 (兼容首尾可能多余空格或未完全闭合的变体)
                if stripped.startswith("|") and (stripped.endswith("|") or stripped.count("|") >= 2):
                    if in_list: converted_lines.append("</ul>"); in_list = False
                    if in_ordered_list: converted_lines.append("</ol>"); in_ordered_list = False
                    if in_quote: converted_lines.append("</blockquote>"); in_quote = False
                    
                    if re.match(r"^\|[\s\-\|:]+\|$", stripped):
                        continue
                        
                    if not in_table:
                        converted_lines.append('<table border="1" style="border-collapse: collapse; width: 100%;">')
                        in_table = True
                        table_header_parsed = False
                        
                    columns = [col.strip() for col in stripped.split("|")[1:-1]]
                    col_tag = "th" if not table_header_parsed else "td"
                    bg_style = ' style="background-color: #f2f2f2; padding: 8px;"' if not table_header_parsed else ' style="padding: 8px;"'
                    row_html = "<tr>" + "".join(f"<{col_tag}{bg_style}>{inline_replace(col)}</{col_tag}>" for col in columns) + "</tr>"
                    converted_lines.append(row_html)
                    
                    if not table_header_parsed:
                        table_header_parsed = True
                    continue
                else:
                    if in_table:
                        converted_lines.append("</table>")
                        in_table = False
                        table_header_parsed = False

                # e) 无序列表
                list_match = re.match(r"^([\-\*\+])\s+(.*)$", stripped)
                if list_match:
                    if in_ordered_list: converted_lines.append("</ol>"); in_ordered_list = False
                    if in_quote: converted_lines.append("</blockquote>"); in_quote = False
                    
                    if not in_list:
                        converted_lines.append("<ul>")
                        in_list = True
                    content = inline_replace(list_match.group(2))
                    converted_lines.append(f"<li>{content}</li>")
                    continue

                # f) 有序列表
                olist_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
                if olist_match:
                    if in_list: converted_lines.append("</ul>"); in_list = False
                    if in_quote: converted_lines.append("</blockquote>"); in_quote = False
                    
                    if not in_ordered_list:
                        converted_lines.append("<ol>")
                        in_ordered_list = True
                    content = inline_replace(olist_match.group(2))
                    converted_lines.append(f"<li>{content}</li>")
                    continue

                # g) 引用块
                if stripped.startswith(">"):
                    if in_list: converted_lines.append("</ul>"); in_list = False
                    if in_ordered_list: converted_lines.append("</ol>"); in_ordered_list = False
                    
                    if not in_quote:
                        converted_lines.append('<blockquote style="border-left: 3px solid #ccc; padding-left: 10px; color: #555; margin-left: 0;">')
                        in_quote = True
                    content = stripped[1:].strip()
                    converted_lines.append(f"<p>{inline_replace(content)}</p>")
                    continue
                else:
                    if in_quote:
                        converted_lines.append("</blockquote>")
                        in_quote = False

                # h) 普通段落
                if in_list: converted_lines.append("</ul>"); in_list = False
                if in_ordered_list: converted_lines.append("</ol>"); in_ordered_list = False
                
                if not stripped:
                    continue
                
                # Check for image or raw elements inside html tags that were bypassed during cleaning
                if stripped.startswith("<div") or stripped.startswith("</div") or stripped.startswith("<p") or stripped.startswith("<img"):
                    converted_lines.append(line)
                else:
                    converted_lines.append(f"<p>{inline_replace(stripped)}</p>")
                
            if in_code: converted_lines.append("</code></pre>")
            if in_list: converted_lines.append("</ul>")
            if in_ordered_list: converted_lines.append("</ol>")
            if in_quote: converted_lines.append("</blockquote>")
            if in_table: converted_lines.append("</table>")
            
            html_content = "<html><body>\n" + "\n".join(converted_lines) + "\n</body></html>"

        # 4. 在当前 Cwd (工作空间) 相对路径创建临时文件，完美避开绝对路径拦截
        with open(temp_filename, "w", encoding="utf-8") as f:
            f.write(html_content)

        # 5. 组织 Drive files.create 的参数，上传并触发 Google 云端自动转换 Docs 样式
        params = {
            "name": title.strip(),
            "mimeType": "application/vnd.google-apps.document"
        }
        
        create_result = _run_google_workspace_cli(
            [
                "drive",
                "files",
                "create",
                "--json",
                json.dumps(params, ensure_ascii=False),
                "--upload",
                temp_filename
            ],
            tool_context,
            timeout_seconds=120
        )

        if create_result.get(STATUS_KEY) != STATUS_SUCCESS:
            return {
                STATUS_KEY: STATUS_ERROR,
                "step": "upload_and_convert",
                MESSAGE_KEY: "Failed to upload and convert HTML to Google Doc via Drive API.",
                "details": create_result,
            }

        created = create_result.get("data") or {}
        document_id = created.get("id") or created.get("documentId")
        if not document_id:
            return {
                STATUS_KEY: STATUS_ERROR,
                "step": "extract_google_doc_id",
                MESSAGE_KEY: "Google Drive conversion response did not include ID.",
                "details": create_result,
            }

        return {
            STATUS_KEY: STATUS_SUCCESS,
            "document_id": document_id,
            "document_url": f"https://docs.google.com/document/d/{document_id}/edit",
            "message": "🎉 完美高保真排版 Google Docs 文档已创建并导入成功！Markdown 格式的大标题、多维表格、列表和引用等效果已原生转换并渲染，您可以通过链接直接编辑。"
        }

    except Exception as e:
        import traceback
        logger.error(f"Failed to create Google Doc: {traceback.format_exc()}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Failed to create Google Doc via high-fidelity conversion pipeline: {str(e)}"
        }
    finally:
        # 6. 100% 确保在任何情况下都会将临时相对文件安全删除，保持干净
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
                logger.info(f"Cleaned up temp import file: {temp_filename}")
            except Exception as ce:
                logger.warning(f"Failed to delete temp file {temp_filename}: {ce}")


def read_google_sheet_range(spreadsheet_id: str, range_name: str, tool_context: ToolContext) -> dict:
    """
    Read values from a Google Sheets range using gws sheets +read.
    """
    if not spreadsheet_id or not spreadsheet_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "spreadsheet_id is required."}
    if not range_name or not range_name.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "range_name is required."}

    return _run_google_workspace_cli(
        [
            "sheets",
            "+read",
            "--spreadsheet",
            spreadsheet_id.strip(),
            "--range",
            range_name.strip(),
        ],
        tool_context,
    )


def append_google_sheet_rows(spreadsheet_id: str, values_json: str, tool_context: ToolContext) -> dict:
    """
    Append rows to a Google Sheet using gws sheets +append.

    Args:
        spreadsheet_id: Google Spreadsheet ID.
        values_json: JSON array string. Use one row like ["a", "b"] or multiple rows like [["a", "b"]].
        tool_context: Tool execution context containing the Google Workspace OAuth token.
    """
    if not spreadsheet_id or not spreadsheet_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "spreadsheet_id is required."}
    try:
        values = _parse_json_array_arg(values_json, "values_json")
    except ValueError as e:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}
    if not values:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "values_json must be a non-empty JSON array."}

    rows = values if isinstance(values[0], list) else [values]
    return _run_google_workspace_cli(
        [
            "sheets",
            "+append",
            "--spreadsheet",
            spreadsheet_id.strip(),
            "--json-values",
            json.dumps(rows, ensure_ascii=False),
        ],
        tool_context,
    )


def create_google_sheet_with_rows(title: str, values_json: str, tool_context: ToolContext) -> dict:
    """
    Create a Google Sheets spreadsheet and append rows.

    values_json accepts one row like ["a", "b"] or multiple rows like
    [["a", "b"], ["c", "d"]].
    """
    if not title or not title.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "title is required."}
    try:
        values = _parse_json_array_arg(values_json, "values_json")
    except ValueError as e:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}
    if not values:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "values_json must be a non-empty JSON array."}

    create_result = _run_google_workspace_cli(
        [
            "sheets",
            "spreadsheets",
            "create",
            "--json",
            json.dumps({"properties": {"title": title.strip()}}, ensure_ascii=False),
        ],
        tool_context,
    )
    if create_result.get(STATUS_KEY) != STATUS_SUCCESS:
        return {
            STATUS_KEY: STATUS_ERROR,
            "step": "create_google_sheet",
            MESSAGE_KEY: "Failed to create Google Sheets spreadsheet.",
            "details": create_result,
        }

    created = create_result.get("data") or {}
    spreadsheet_id = created.get("spreadsheetId") or created.get("id")
    if not spreadsheet_id:
        return {
            STATUS_KEY: STATUS_ERROR,
            "step": "extract_google_sheet_id",
            MESSAGE_KEY: "Google Sheets create response did not include spreadsheetId.",
            "details": create_result,
        }

    append_result = append_google_sheet_rows(
        spreadsheet_id=spreadsheet_id,
        values_json=json.dumps(values, ensure_ascii=False),
        tool_context=tool_context,
    )
    if append_result.get(STATUS_KEY) != STATUS_SUCCESS:
        return {
            STATUS_KEY: STATUS_ERROR,
            "step": "append_google_sheet_rows",
            MESSAGE_KEY: "Google Sheets spreadsheet was created, but appending rows failed.",
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": created.get(
                "spreadsheetUrl",
                f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            ),
            "details": append_result,
        }

    return {
        STATUS_KEY: STATUS_SUCCESS,
        MESSAGE_KEY: "Google Sheets spreadsheet created and populated successfully.",
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": created.get(
            "spreadsheetUrl",
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        ),
        "create_result": create_result,
        "append_result": append_result,
    }


def list_google_calendar_events(
    tool_context: ToolContext,
    days: int = 7,
    calendar: str = "",
    timezone: str = "",
) -> dict:
    """
    List upcoming Google Calendar events using gws calendar +agenda.
    """
    try:
        days = _safe_limit(days, default=7, minimum=1, maximum=31)
        args = ["calendar", "+agenda", "--days", str(days), "--format", "json"]
        if calendar and isinstance(calendar, str) and calendar.strip():
            args += ["--calendar", calendar.strip()]
        if timezone and isinstance(timezone, str) and timezone.strip():
            args += ["--timezone", timezone.strip()]
        return _run_google_workspace_cli(args, tool_context)
    except Exception as e:
        logger.exception("list_google_calendar_events failed unexpectedly.")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Failed to list calendar events: {e}",
        }


def create_google_calendar_event(
    summary: str,
    start: str,
    end: str,
    tool_context: ToolContext,
    calendar: str = "primary",
    description: str = "",
    location: str = "",
    attendees_json: str = "",
    add_meet: bool = False,
) -> dict:
    """
    Create a Google Calendar event using gws calendar +insert.

    start and end must be RFC3339 timestamps, for example
    2026-06-17T09:00:00+08:00.
    """
    try:
        # 强类型与非空校验守护
        if not summary or not isinstance(summary, str) or not summary.strip():
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "summary is required and must be a non-empty string."}
        if not start or not isinstance(start, str) or not start.strip():
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "start is required and must be a non-empty string."}
        if not end or not isinstance(end, str) or not end.strip():
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "end is required and must be a non-empty string."}

        # 确保可选参数传入非空和非字符串时的安全过渡
        cal_str = calendar if isinstance(calendar, str) else "primary"
        desc_str = description if isinstance(description, str) else ""
        loc_str = location if isinstance(location, str) else ""

        args = [
            "calendar",
            "+insert",
            "--calendar",
            (cal_str or "primary").strip(),
            "--summary",
            summary.strip(),
            "--start",
            start.strip(),
            "--end",
            end.strip(),
        ]
        if desc_str and desc_str.strip():
            args += ["--description", desc_str.strip()]
        if loc_str and loc_str.strip():
            args += ["--location", loc_str.strip()]
        try:
            attendees = _parse_json_array_arg(attendees_json, "attendees_json")
        except ValueError as e:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}
        if attendees:
            for attendee in attendees:
                if attendee:
                    args += ["--attendee", str(attendee).strip()]
        if add_meet:
            args.append("--meet")

        return _run_google_workspace_cli(args, tool_context)
    except Exception as e:
        logger.exception("create_google_calendar_event failed unexpectedly.")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Failed to create calendar event: {e}",
        }


def _run_lark_cli(args: list, tool_context: ToolContext, timeout_seconds: int = 120) -> dict:
    """
    运行 Feishu/Lark CLI 命令行工具。
    使用 cli_client 隔离执行，并传递 Lark 的 access_token。
    """
    try:
        # 物理洗涤层：对飞书发消息等 `--text` 参数进行无损 Markdown 降维净化洗涤
        for i in range(len(args)):
            if isinstance(args[i], str):
                if args[i] == "--text" and i + 1 < len(args) and isinstance(args[i+1], str):
                    args[i+1] = _clean_markdown_from_plain_text(args[i+1])

        # 物理洗涤层：抗幻觉自愈，将大模型在命令行参数中可能幻觉出的字面量 \\n 强行还原为真实的换行符
        args = [arg.replace("\\n", "\n") if isinstance(arg, str) else arg for arg in args]
        
        access_token = get_access_token(tool_context)
        if not access_token:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: (
                    "Feishu/Lark authentication required. "
                    f"Missing token in tool_context.state['{LARK_AUTH_ID}']."
                ),
            }
        return cli_client.run_command(
            args=args,
            access_token=access_token,
            client_id=LARK_CLIENT_ID,
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        logger.exception("Feishu/Lark CLI execution failed unexpectedly.")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Feishu/Lark CLI execution failed: {e}",
        }


def _parse_lark_args(args_json: str) -> list:
    """
    解析并验证 Lark CLI 传入的 args_json 数组。
    """
    args = _parse_json_array_arg(args_json, "args_json")
    if not args:
        raise ValueError("args_json must be a non-empty JSON array.")
    if len(args) > 40:
        raise ValueError("args_json must contain at most 40 arguments.")
    normalized = []
    for arg in args:
        if not isinstance(arg, str):
            raise ValueError("args_json must contain strings only.")
        if "\x00" in arg or "\n" in arg or "\r" in arg:
            raise ValueError("args_json arguments must not contain control characters.")
        if len(arg) > 20000:
            raise ValueError("args_json contains an argument that is too long.")
        normalized.append(arg)
    return normalized


def _is_lark_mutating_args(args: list) -> bool:
    """
    检测 Feishu/Lark 命令行参数是否具有写操作 (Mutation) 属性。
    """
    normalized_args = [arg.lower() for arg in args]
    if any(arg in LARK_MUTATING_HELPERS for arg in normalized_args):
        return True
    return any(arg.split(".")[-1] in LARK_MUTATING_METHODS for arg in normalized_args)


def _parse_lark_resource_path(resource: str) -> list:
    """
    解析 Feishu/Lark 的资源路径段，如 "drive.file" -> ["drive", "file"]。
    """
    if not resource or not resource.strip():
        return []

    parts = resource.strip().replace(".", " ").split()
    for part in parts:
        if part.startswith("-") or "/" in part or ".." in part:
            raise ValueError("resource contains an invalid path segment.")
        if "\x00" in part or "\n" in part or "\r" in part:
            raise ValueError("resource must not contain control characters.")
    return parts


def _validate_lark_args(args: list, allow_mutating: bool) -> None:
    """
    对 Lark CLI 传入的参数进行极其严格的安全性、合规性及服务权限校验。
    """
    first = args[0]
    if first.startswith("-") and first not in LARK_SAFE_META_COMMANDS:
        raise ValueError("First Lark argument must be a service name or a safe meta command.")
    if first not in LARK_ALLOWED_SERVICES and first not in LARK_SAFE_META_COMMANDS:
        raise ValueError(
            "Unsupported Feishu/Lark service. Allowed services: "
            + ", ".join(sorted(LARK_ALLOWED_SERVICES))
            + "."
        )

    blocked_flags = {
        "--output",
        "--output-dir",
        "--dir",
        "--credentials",
        "--credentials-file",
    }
    for arg in args:
        if arg.startswith("/") or ".." in arg:
            raise ValueError("Absolute paths and parent-directory traversal are not allowed.")
        if arg in blocked_flags:
            raise ValueError(f"Flag {arg} is not allowed in the generic Lark executor.")

    mutating = _is_lark_mutating_args(args)
    if mutating and not allow_mutating and "--dry-run" not in args:
        raise ValueError(
            "This Feishu/Lark command appears to mutate data. Re-run with dry_run=True for preview "
            "or allow_mutating=True after explicit user confirmation."
        )


def discover_lark_operations(
    query: str = "",
    service: str = "",
    intent: str = "",
    resource: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """
    [Discovery Tool] 从本地 Feishu/Lark CLI 注册表中模糊搜索或发现 Feishu/Lark CLI 操作技能（无网络延迟、无需凭证授权）。

    Args:
        query: 自然语言描述的飞书接口能力（例如 "list documents" 或 "create record"），采用分词模糊搜索匹配。
        service: 飞书模块服务名称过滤器，例如 drive, im, base, doc, contact, approval 等。
        intent: 意图过滤器（可选 'read' 只读，或 'write' 突变修改），过滤只读和写入类操作。
        resource: 被操作的飞书资源路径标识符，支持空格或点分隔（例如 "document block" 或 "base.record"）。
        tool_context: 仅作为 ADK 工具执行器的兼容占位参数。
    """
    service_name = service.strip().lower() if service else ""
    if service_name and service_name not in LARK_ALLOWED_SERVICES:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: f"Unsupported Feishu/Lark service: {service_name}"}

    try:
        resource_parts = _parse_lark_resource_path(resource)
    except ValueError as e:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}

    matches = search_lark_commands(
        query=query or "",
        service=service_name,
        intent=intent or "",
        resource_parts=resource_parts,
    )
    return {
        STATUS_KEY: STATUS_SUCCESS,
        "source": "lark_registry",
        "matches": matches,
        MESSAGE_KEY: (
            "Use get_lark_command_spec(command_id) before executing. "
            "If no registry match fits, use get_lark_operation_schema(method_path) "
            "with a real path such as doc.raw.get."
        ),
    }


def get_lark_command_spec(command_id: str) -> dict:
    """
    [Spec Tool] 根据唯一的 command_id 从本地注册表中获取飞书命令的入参、范例及结构定义。

    Args:
        command_id: 来自 discover_lark_operations 匹配得出的飞书命令唯一 ID（例如 "feishu:drive:file:list"）。
    """
    spec = get_lark_command_spec_impl(command_id)
    if not spec:
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: (
                "Feishu/Lark command_id was not found in the local registry. "
                "Use discover_lark_operations first, or fall back to "
                "get_lark_operation_schema for a real Lark schema path."
            ),
        }
    return {
        STATUS_KEY: STATUS_SUCCESS,
        **spec,
        MESSAGE_KEY: (
            "Registry command spec returned. Build execute_lark_cli args "
            "from argv_template and examples; do not invent shell commands."
        ),
    }


def get_lark_operation_schema(method_path: str, tool_context: ToolContext) -> dict:
    """
    [Schema Introspection] 实时调用飞书 CLI 执行 'feishu schema [path]'，拉取飞书特定 API 底层标准的 JSON Schema 元数据。

    Args:
        method_path: 飞书 API 完整服务与接口名，例如 doc.raw.get 或 base.record.create。
        tool_context: 工具执行上下文，提供安全受控的 OAuth Token 注入支持。
    """
    if not method_path or not method_path.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "method_path is required."}
    safe_method = method_path.strip()
    if safe_method.startswith("-") or "/" in safe_method or ".." in safe_method:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "method_path is invalid."}
    service = safe_method.split(".", 1)[0]
    if service not in LARK_ALLOWED_SERVICES:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: f"Unsupported Feishu/Lark service: {service}"}
    
    result = _run_lark_cli(["schema", safe_method], tool_context, timeout_seconds=60)
    if result.get(STATUS_KEY) != STATUS_SUCCESS:
        return result

    schema_payload = result.get("data", result.get("content"))
    if not isinstance(schema_payload, str):
        schema_payload = json.dumps(schema_payload, ensure_ascii=False)

    return {
        STATUS_KEY: STATUS_SUCCESS,
        "method_path": safe_method,
        "schema_json": schema_payload,
        MESSAGE_KEY: (
            "Schema fetched successfully. schema_json is a JSON string; parse it before "
            "constructing a generic Lark command."
        ),
    }


def execute_lark_cli(
    args_json: str,
    tool_context: ToolContext,
    dry_run: bool = True,
    allow_mutating: bool = False,
    timeout_seconds: int = 120,
) -> dict:
    """
    [Registry-Backed Executor] 传入序列化的飞书参数数组，调用高度隔离且自带干跑保护机制的飞书通用命令行执行器。

    Args:
        args_json: 飞书命令行参数的 JSON 格式字符串数组，例如：["drive", "file", "list", "--params", "{\\"pageSize\\": 10}"]
        tool_context: 工具执行上下文，包含飞书用户 OAuth 的 access_token。
        dry_run: 默认安全使能（True）。如果判断为写突变操作且缺失 '--dry-run'，会自动补全。
        allow_mutating: 默认不使能（False）。突变修改类操作不加 '--dry-run' 时，此项必须声明为 True。
        timeout_seconds: 执行超时秒数，取值 10s ~ 300s，默认 120s。
    """
    try:
        args = _parse_lark_args(args_json)
        command_spec = find_lark_command_for_args(args)
        registry_marks_mutating = bool(command_spec and command_spec.get("kind") != "read")
        if dry_run and (registry_marks_mutating or _is_lark_mutating_args(args)) and "--dry-run" not in args:
            args.append("--dry-run")
        _validate_lark_args(args, allow_mutating=allow_mutating)
    except ValueError as e:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}

    timeout_seconds = _safe_limit(timeout_seconds, default=120, minimum=10, maximum=300)
    result = _run_lark_cli(args, tool_context, timeout_seconds=timeout_seconds)
    
    if command_spec:
        result = dict(result)
        result["command_id"] = command_spec["command_id"]
        result["command_kind"] = command_spec["kind"]
        result["requires_confirmation"] = command_spec["requires_confirmation"]
    return result


def execute_lark_cli_flat(
    service: str,
    resource: str,
    method: str,
    tool_context: ToolContext,
    params_json: str = "",
    json_body: str = "",
    upload_file: str = "",
    page_all: bool = False,
    dry_run: bool = True,
    allow_mutating: bool = False,
    timeout_seconds: int = 120,
) -> dict:
    """
    [Universal Flat Executor] 【优先推荐此工具】使用高层扁平结构传递服务、资源和方法，完美杜绝因多重 Shell 嵌套及 JSON 转义导致的不稳定性和注入风险。

    Args:
        service: 飞书底层服务模块（例如 'doc', 'drive', 'im', 'base', 'calendar', 'contact' 等）。
        resource: 被操作的飞书实体资源路径（例如 'document', 'file', 'message', 'record' 等）。
        method: 调用的操作名（例如 'list', 'get', 'create', 'update', 'delete' 等）。
        tool_context: 工具执行上下文，携带飞书 OAuth 的安全凭证。
        params_json: 可选。扁平的 Query 过滤参数 JSON 字符串，例如 '{"pageSize": 15}'。
        json_body: 可选。请求体的 JSON Payload 字符串，例如 '{"title": "企业安全白皮书"}'。
        upload_file: 可选。本地拟上载文件的绝对/相对路径名（文件直传功能）。
        page_all: 可选。若为 True，则拉取列表数据时会自动追加 '--page-all' 分页抓取所有行。
        dry_run: 默认安全使能（True）。写命令如果缺少安全只读模拟预览标记，会自动追加。
        allow_mutating: 默认拦截保护（False）。突变修改操作执行（即 dry_run=False）必须传入此项为 True，方能生效。
        timeout_seconds: 执行超时秒数上限（取值区间：10s ~ 300s，默认 120s）。
    """
    try:
        service_clean = service.strip().lower()
        if service_clean not in LARK_ALLOWED_SERVICES:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: f"Unsupported Feishu/Lark service: {service_clean}"}

        args = [service_clean]
        
        resource_parts = _parse_lark_resource_path(resource)
        args.extend(resource_parts)
        
        method_clean = method.strip()
        if not method_clean or method_clean.startswith("-"):
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: f"Invalid method name: {method}"}
        args.append(method_clean)
        
        if params_json and params_json.strip():
            params_arg = _coerce_json_cli_arg(params_json, "params_json")
            args.extend(["--params", params_arg])
            
        if json_body and json_body.strip():
            json_arg = _coerce_json_cli_arg(json_body, "json_body")
            args.extend(["--json", json_arg])
            
        if upload_file and upload_file.strip():
            file_clean = upload_file.strip()
            if "/" in file_clean or ".." in file_clean:
                return {
                    STATUS_KEY: STATUS_ERROR, 
                    MESSAGE_KEY: "Absolute paths and parent-directory traversal are not allowed for upload_file."
                }
            args.extend(["--upload", file_clean])
            
        if page_all:
            args.append("--page-all")
            
        command_spec = find_lark_command_for_args(args)
        registry_marks_mutating = bool(command_spec and command_spec.get("kind") != "read")
        if dry_run and (registry_marks_mutating or _is_lark_mutating_args(args)) and "--dry-run" not in args:
            args.append("--dry-run")
            
        _validate_lark_args(args, allow_mutating=allow_mutating)
        
    except Exception as e:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}

    timeout_seconds = _safe_limit(timeout_seconds, default=120, minimum=10, maximum=300)
    result = _run_lark_cli(args, tool_context, timeout_seconds=timeout_seconds)
    
    if command_spec:
        result = dict(result)
        result["command_id"] = command_spec["command_id"]
        result["command_kind"] = command_spec["kind"]
        result["requires_confirmation"] = command_spec["requires_confirmation"]
    return result


def query_lark_documents(query: str, tool_context: ToolContext) -> dict:
    """
    Queries Lark documents for the authenticated user.

    It performs a real-time search against the Lark Cloud Documents API.
    Before searching, it verifies the user's authentication status.
    The search results are provided in two formats: one for display and one for further processing.

    Args:
        query: The user's search query (keyword).
        tool_context: The tool execution context for accessing session state and tokens.

    Returns:
        dict: A dictionary containing the search results if successful, or an error/auth_required status.
              - On success:
                {
                    'status': 'success',
                    'documents': ['<a href="...">Title</a>', ...], # For User Display
                    'documents_token': [{'title': '...', 'doc_token': '...'}, ...] # For Tool Use (e.g. fetching content)
                }
              - On auth required: {'status': 'auth_required', ...}
              - On error: {'status': 'error', 'message': ...}
    """
    try:
        access_token = get_access_token(tool_context)

        if not access_token:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Access token is missing. Please ensure OAuth is configured.",
            }

        documents = lark_api_repository.search_documents(access_token, query)

        # 生成带预览的文档列表（美观格式）
        # 格式：每个文档包含标题、链接和内容预览（如果有）
        display_docs = []
        for idx, doc in enumerate(documents):
            title = doc.get("title", "Untitled")
            url = doc.get("url", "")
            doc_token = doc.get("docs_token", "")
            doc_type = doc.get("docs_type", "").lower()
            summary_highlighted = doc.get("summary_highlighted", "")
            search_query = doc.get("search_query", query)  # 使用保存的搜索关键词

            # 预览内容生成策略：
            # 1. 优先使用搜索结果中的高亮摘要（summary_highlighted）
            #    优点：无需额外API调用，性能好
            #    缺点：可能内容较短，上下文不够丰富
            # 2. 如果没有高亮摘要，从文档内容中提取关键字附近的文本
            #    优点：上下文更丰富，可以提取更多内容
            #    缺点：需要额外的API调用（get_document_content），性能开销大
            preview_text = ""
            if summary_highlighted and summary_highlighted.strip():
                # 清理高亮标签，转换为 Markdown 加粗格式
                # 飞书API返回的格式：<h>关键字</h> -> Markdown格式：**关键字**
                preview_text = summary_highlighted.replace("<h>", "**").replace(
                    "</h>", "**"
                )
                # 限制长度，避免预览过长
                if len(preview_text) > 300:
                    preview_text = preview_text[:300] + "..."
            else:
                # 如果没有高亮摘要，从文档内容中提取关键字附近的文本
                # 性能优化：只为前5个文档获取预览，避免过多API调用
                # 只处理文档类型（doc/docx），其他类型（sheet、file等）不支持内容预览
                if idx < 5 and doc_token and doc_type in ["doc", "docx"]:
                    try:
                        preview_text = lark_api_repository.get_document_preview(
                            access_token,
                            doc_token,
                            doc_type,
                            search_query=search_query,  # 传入搜索关键词，用于提取关键字上下文
                            max_length=300,  # 预览长度限制
                        )
                    except Exception:
                        # 预览获取失败不影响主流程，静默处理
                        pass

            # 构建结构化的 Markdown 格式输出
            # 使用 Markdown 语法创建美观的文档卡片样式

            # 构建文档标题和链接
            if url:
                doc_display = f"### 📄 {idx + 1}. [{title}]({url})\n\n"
            else:
                doc_display = f"### 📄 {idx + 1}. {title}\n\n"

            # 如果有预览内容，添加到显示中
            if preview_text and preview_text.strip():
                # 清理预览文本
                preview_clean = preview_text.strip()
                # 限制预览行数（最多3行）
                preview_lines = preview_clean.split("\n")
                if len(preview_lines) > 3:
                    preview_clean = "\n".join(preview_lines[:3]) + "..."
                # 限制总长度
                if len(preview_clean) > 250:
                    preview_clean = preview_clean[:250] + "..."

                # 使用引用块来显示预览内容，增强视觉效果
                # 将预览文本按行分割，每行作为引用块的一部分
                preview_lines_formatted = preview_clean.split("\n")
                preview_markdown = "\n".join(
                    [
                        f"> {line}" if line.strip() else ">"
                        for line in preview_lines_formatted
                    ]
                )

                doc_display += f"**内容预览：**\n\n{preview_markdown}\n\n"

            # 如果有URL，添加链接提示
            if url:
                doc_display += f"🔗 [打开文档 →]({url})\n"

            # 添加分隔线（最后一个文档不加）
            if idx < len(documents) - 1:
                doc_display += "\n---\n\n"

            display_docs.append(doc_display)

        return {
            STATUS_KEY: STATUS_SUCCESS,
            DOCUMENTS_KEY: display_docs,
            DOCUMENTS_TOKEN: [
                {
                    "title": doc.get("title"),
                    "doc_token": doc.get("docs_token"),
                    "doc_type": doc.get("docs_type"),
                }
                for doc in documents
            ],
        }
    except Exception as e:
        logger.error(f"Error querying Lark documents: {str(e)}", exc_info=True)
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Failed to query Lark documents: {str(e)}",
        }


def get_lark_document_content(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Retrieves the content of a specific Lark document in Markdown format.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        doc_type: The document type (doc, docx, sheet, bitable). Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the document content if successful.
              - On success: {'status': 'success', 'content': '...markdown content...'}
              - On error: {'status': 'error', 'message': ...}
    """
    try:
        access_token = get_access_token(tool_context)

        if not access_token:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Access token is missing. Please ensure OAuth is configured.",
            }

        content = lark_api_repository.get_document_content(
            access_token, doc_token, doc_type
        )

        return {STATUS_KEY: STATUS_SUCCESS, "content": content}

    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to get document content: {error_detail}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Failed to get document content for token {doc_token} (type: {doc_type}): {str(e)}",
            "debug_info": error_detail,
        }


async def get_lark_document_markdown(
    doc_token: str, tool_context: ToolContext, download_images: bool = False
) -> dict:
    """
    Retrieves the content of a specific Lark document (docx only) in high-quality Lark-flavored Markdown format.
    This uses Feishu's V2 Docs AI fetch API to return extremely high-fidelity Markdown, including tables, lists, and callout blocks.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        download_images: Optional. Set to True ONLY when the user explicitly requests high-fidelity document layout rendering, viewing visual details, or analyzing charts inside the document. Keep False (default) for fast textual summarization or text searching to avoid unnecessary network latency and download issues.

    Returns:
        dict: A dictionary containing the document content if successful.
              - On success: {'status': 'success', 'content': '...markdown content...'}
              - On error: {'status': 'error', 'message': ...}
    """
    try:
        # 获取用户授权的 Access Token (OAuth流程或UAT)
        access_token = get_access_token(tool_context)

        if not access_token:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Access token is missing. Please ensure OAuth is configured.",
            }

        # 调用底层 docs_ai fetch API 拉取 Markdown
        content = lark_api_repository.get_document_markdown(
            access_token, doc_token
        )

        if not content:
            return {STATUS_KEY: STATUS_SUCCESS, "content": ""}

        # 🌟 自动解析 Markdown 中的图片链接并注册为 ADK Artifacts 🌟
        if download_images:
            img_pattern = r'!\[(.*?)\]\((https?://[^\s)]+)\)'
            matches = re.findall(img_pattern, content)

            if matches:
                logger.info(f"[get_lark_document_markdown] Found {len(matches)} images in markdown. Attempting to save as artifacts...")
                from nexus_agent.infrastructure.lark_api_repository import _session
                from google.genai import types
                
                url_to_artifact = {}
                headers = {"Authorization": f"Bearer {access_token}"}

                for idx, (alt_text, img_url) in enumerate(matches, 1):
                    if img_url in url_to_artifact:
                        continue

                    try:
                        req_headers = {}
                        if any(domain in img_url for domain in ["feishu.cn", "larksuite.com", "feishu-open.cn"]):
                            req_headers = headers

                        # 🌟 5秒极短超时，防止由于网络原因或飞书流失效拖垮主流程
                        response = _session.get(img_url, headers=req_headers, timeout=5)
                        if response.status_code == 200:
                            img_bytes = response.content
                            mime_type = response.headers.get("Content-Type", "image/jpeg")
                            if not mime_type.startswith("image/"):
                                mime_type = "image/jpeg"

                            doc_hash = doc_token[:8]
                            safe_alt = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', alt_text) if alt_text else f"img_{idx}"
                            if not safe_alt or safe_alt == "_":
                                safe_alt = f"img_{idx}"

                            ext_map = {
                                "image/png": ".png",
                                "image/jpeg": ".jpg",
                                "image/jpg": ".jpg",
                                "image/gif": ".gif",
                                "image/webp": ".webp",
                                "image/svg+xml": ".svg",
                                "image/bmp": ".bmp",
                            }
                            target_ext = ext_map.get(mime_type, ".jpg")
                            if not any(safe_alt.lower().endswith(ext) for ext in ext_map.values()):
                                safe_alt = f"{safe_alt}{target_ext}"

                            artifact_filename = f"lark_{doc_hash}_{safe_alt}"
                            artifact_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
                            version = await tool_context.save_artifact(filename=artifact_filename, artifact=artifact_part)
                            
                            url_to_artifact[img_url] = (artifact_filename, version)
                        else:
                            logger.warning(f"[get_lark_document_markdown] Skip image download. Status code: {response.status_code}")
                    except Exception as ex:
                        logger.warning(f"[get_lark_document_markdown] Skip failed image download for {img_url}: {ex}")

                # 替换 Markdown 中的图片标注，添加相对路径引用，触发 GE Inline 嵌入渲染，并辅以 Artifact 指引
                def replace_img_tag(match):
                    alt = match.group(1)
                    url = match.group(2)
                    if url in url_to_artifact:
                        artifact_filename, version = url_to_artifact[url]
                        return (
                            f"![{alt}]({artifact_filename})\n"
                            f"*(📷 该图片已作为本地 ADK 产物成功渲染。如果未能内联显示，请在右侧‘产物/Artifacts’面板中查看：`{artifact_filename}`)*"
                        )
                    # 🌟 兜底：如果下载失败，Markdown 中保留原生的原始直链，使得大模型依然可以识别
                    return match.group(0)

                content = re.sub(img_pattern, replace_img_tag, content)

        return {STATUS_KEY: STATUS_SUCCESS, "content": content}

    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to get document markdown: {error_detail}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Failed to get document markdown for token {doc_token}: {str(e)}",
            "debug_info": error_detail,
        }


def show_user_auth_info(tool_context: ToolContext) -> str:
    """
    Displays the current user authentication information using the standard ADK auth response format.

    Args:
        tool_context: The tool execution context.

    Returns:
        The authentication information.
    """
    token = ""
    try:
        token = get_access_token(tool_context)
        if not token:
            token = f"""tool_context.state[f"temp:{LARK_AUTH_ID}"] is None"""
    except Exception as e:
        token = (
            """tool_context.state[f"temp:{LARK_AUTH_ID}"] raised exception: """ + str(e)
        )

    tool_context.state["LARK_AUTH_INFO"] = token
    return str(tool_context.state.to_dict())


def get_lark_document_content_pdf(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Exports a Lark document directly to PDF format.
    Use this when you want the model to see the document exactly as it would appear when printed/viewed.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        doc_type: The source document type. Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the description and the PDF data in multimodal parts.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        # 1. 权限预检
        perm_result = lark_api_repository.check_document_permission(
            access_token, doc_token, doc_type
        )
        if not perm_result.get("has_export_permission"):
            error_msg = (
                perm_result.get("error")
                or "Export permission is explicitly denied for this document."
            )
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: f"⚠️ [PERMISSION DENIED] Cannot export PDF. {error_msg}",
                "debug_info": perm_result,
            }

        # 2. 导出为 PDF (现在返回原始 bytes)
        pdf_bytes = lark_api_repository.get_document_as_pdf(
            access_token, doc_token, doc_type
        )

        if not pdf_bytes:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Failed to export PDF content (or document is too large). Please try 'get_lark_document_content' for text-only access.",
            }

        # 将 PDF bytes 通过 __multimodal_parts__ 传递给模型视觉通道
        from .callbacks import MULTIMODAL_PARTS_KEY

        return {
            "status": "success",
            "text_content": "以下是该文档的 PDF 多模态视图。请直接阅读 PDF 内容回答用户问题。",
            "pdf_size_bytes": len(pdf_bytes),
            MULTIMODAL_PARTS_KEY: [
                {
                    "mime_type": "application/pdf",
                    "data": pdf_bytes,
                }
            ],
        }
    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to export PDF: {error_detail}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"⚠️ [TECHNICAL ERROR] Failed to export PDF for token {doc_token} (type: {doc_type}). Reason: {str(e)}",
            "debug_info": error_detail,
        }


def get_lark_document_content_docx(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Exports a Lark document directly to Word (docx) format.
    Use this when you want the model to analyze the document in its native Word structure.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        doc_type: The source document type. Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the status and the Word data in multimodal parts.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        # 1. 权限预检
        perm_result = lark_api_repository.check_document_permission(
            access_token, doc_token, doc_type
        )
        if not perm_result.get("has_export_permission"):
            error_msg = (
                perm_result.get("error")
                or "Export permission is explicitly denied for this document."
            )
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: f"⚠️ [PERMISSION DENIED] Cannot export Word. {error_msg}",
                "debug_info": perm_result,
            }

        # 2. 导出为 Word
        docx_bytes = lark_api_repository.get_document_as_docx(
            access_token, doc_token, doc_type
        )

        if not docx_bytes:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Failed to export Word content (or document is too large).",
            }

        # 将 Word bytes 通过 __multimodal_parts__ 传递给模型
        from .callbacks import MULTIMODAL_PARTS_KEY

        return {
            "status": "success",
            "text_content": "以下是该文档的 Word (docx) 多模态数据。请分析其内容回答用户问题。",
            "docx_size_bytes": len(docx_bytes),
            MULTIMODAL_PARTS_KEY: [
                {
                    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "data": docx_bytes,
                }
            ],
        }
    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to export Word: {error_detail}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"⚠️ [TECHNICAL ERROR] Failed to export Word for token {doc_token} (type: {doc_type}). Reason: {str(e)}",
            "debug_info": error_detail,
        }


async def get_lark_document_rich_content(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Exports a Lark document and extracts its rich content, including text and images.
    Use this when you need to analyze the document's structure or inspect embedded images.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        doc_type: The source document type (e.g., 'docx', 'doc', 'sheet'). Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the document's text and image parts.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        # 1. 权限预检
        perm_result = lark_api_repository.check_document_permission(
            access_token, doc_token, doc_type
        )
        if not perm_result.get("has_export_permission"):
            error_msg = (
                perm_result.get("error")
                or "Export permission is explicitly denied for this document."
            )
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: f"⚠️ [PERMISSION DENIED] Cannot get rich content. {error_msg}",
                "debug_info": perm_result,
            }

        # 2. 获取富文本内容
        result = lark_api_repository.get_document_rich_text_by_block(
            access_token, doc_token, doc_type
        )

        repo_parts = result.get("parts", [])
        text_segments = []  # 文本内容
        multimodal_parts = []  # 多媒体数据（图片/PDF），将通过 FunctionResponse.parts 传递

        for item in repo_parts:
            # 处理结构化数据 (来自 Repository 的 dict)
            if isinstance(item, dict):
                p_type = item.get("type")

                if p_type == "text":
                    text_segments.append(item.get("content", ""))

                elif p_type == "image":
                    img_name = item.get("name", "unknown")
                    img_data = item.get(
                        "data"
                    )  # raw bytes (来自 _process_image_to_bytes)
                    mime = item.get("mime_type", "image/jpeg")

                    if img_data:
                        # 将图片数据收集到 multimodal_parts（通过 __multimodal_parts__ 传递）
                        multimodal_parts.append(
                            {
                                "mime_type": mime,
                                "data": img_data,
                            }
                        )
                        # 🌟 新增：直接注册为原生 ADK Artifact，确保用户前端 100% 渲染展示
                        try:
                            from google.genai import types
                            artifact_part = types.Part.from_bytes(data=img_data, mime_type=mime)
                            # 清洗文件名
                            safe_img_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', img_name)
                            ext_map = {
                                "image/png": ".png",
                                "image/jpeg": ".jpg",
                                "image/jpg": ".jpg",
                                "image/gif": ".gif",
                                "image/webp": ".webp",
                                "image/svg+xml": ".svg",
                                "image/bmp": ".bmp",
                            }
                            target_ext = ext_map.get(mime, ".jpg")
                            if not any(safe_img_name.lower().endswith(ext) for ext in ext_map.values()):
                                safe_img_name = f"{safe_img_name}{target_ext}"
                            
                            filename_in_service = f"lark_rich_{doc_token[:8]}_{safe_img_name}"
                            version = await tool_context.save_artifact(filename=filename_in_service, artifact=artifact_part)
                            
                            text_segments.append(
                                f"\n[📷 图片 '{img_name}' 已作为 ADK 产物成功渲染，请在侧边栏中预览：'{filename_in_service}' (版本 {version})，亦可通过下方视觉通道感知]\n"
                            )
                        except Exception as ae:
                            logger.warning(f"Failed to save document image as artifact: {ae}")
                            text_segments.append(
                                f"\n[📷 图片 {img_name} - 见下方视觉输入 #{len(multimodal_parts)}]\n"
                            )
                    else:
                        text_segments.append(f"\n[图片数据缺失: {img_name}]\n")

            # 处理纯文本 (兼容旧格式)
            elif isinstance(item, str):
                text_segments.append(item)

        # 如果处理的图片数量少于总数，添加提示
        if result.get("processed_image_count", 0) < result.get("image_count", 0):
            text_segments.append(
                f"\n\n> *注：文档包含更多图片（共 {result.get('image_count')} 张），"
                f"已自动展示前 {result.get('processed_image_count')} 张。*"
            )

        # 构造返回值：
        # - text_content: 合并后的文本，放入 FunctionResponse.response dict
        # - __multimodal_parts__: 图片数据，被 patch 后的 ADK 提取
        #   到 FunctionResponse.parts 中，让模型真正"看到"
        from .callbacks import MULTIMODAL_PARTS_KEY

        response = {
            "status": "success",
            "text_content": "\n".join(text_segments),
            "image_count": len(multimodal_parts),
            "total_image_count": result.get("image_count", 0),
        }

        if multimodal_parts:
            response[MULTIMODAL_PARTS_KEY] = multimodal_parts
            logger.info(
                f"[get_lark_document_rich_content] 返回 {len(multimodal_parts)} 张图片 "
                f"(通过 FunctionResponse.parts 视觉通道)"
            )

        return response
    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to get rich content: {error_detail}")
        # 将具体错误信息合并到 message 中，确保用户在界面能感知到具体原因（如权限不足、Token错误等）
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"⚠️ [TECHNICAL ERROR] Failed to get rich content for token {doc_token} (type: {doc_type}). Reason: {str(e)}",
            "debug_info": error_detail,
        }


def create_lark_document(
    title: str, markdown_content: str, tool_context: ToolContext, folder_token: str = ""
) -> dict:
    """
    Creates a new Lark document with the specified title and Markdown content.

    Args:
        title: The title of the new document.
        markdown_content: The content of the document in Lark-flavored Markdown.
        tool_context: The tool execution context.
        folder_token: Optional. The token of the parent folder where the document should be created.

    Returns:
        dict: A dictionary containing the status and document info (doc_token, url).
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        args = ["--title", title, "--markdown", markdown_content]
        if folder_token:
            args += ["--folder-token", folder_token]

        result = cli_client.run_command("docs", "+create", args, access_token, LARK_CLIENT_ID)
        return result
    except Exception as e:
        logger.error(f"Failed to create Lark document: {str(e)}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def update_lark_document(
    doc_token: str, markdown_content: str, tool_context: ToolContext, mode: str = "append"
) -> dict:
    """
    Updates an existing Lark document.

    Args:
        doc_token: The unique identifier or URL of the document.
        markdown_content: The new content or content to append in Lark-flavored Markdown.
        tool_context: The tool execution context.
        mode: The update mode. Options: 'append' (default), 'overwrite', 'replace_range', 'replace_all', 'insert_before', 'insert_after', 'delete_range'.

    Returns:
        dict: A dictionary containing the update status.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        args = ["--doc", doc_token, "--markdown", markdown_content, "--mode", mode]
        result = cli_client.run_command("docs", "+update", args, access_token, LARK_CLIENT_ID)
        return result
    except Exception as e:
        logger.error(f"Failed to update Lark document: {str(e)}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def delete_lark_document(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Deletes a Lark document or file.

    Args:
        doc_token: The unique identifier or URL of the document/file.
        tool_context: The tool execution context.
        doc_type: Optional. The type of the file (e.g., 'docx', 'doc', 'sheet', 'bitable', 'folder'). Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the deletion status.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        # 转换 doc_type 为 CLI 预期的格式
        type_map = {
            "doc": "doc",
            "docx": "docx",
            "sheet": "sheet",
            "bitable": "bitable",
            "folder": "folder",
            "file": "file",
            "slides": "slides",
            "mindnote": "mindnote",
        }
        cli_type = type_map.get(doc_type.lower(), "docx")

        # 使用正确的参数名 --file-token，并添加 --yes 跳过交互式确认
        args = ["--file-token", doc_token, "--type", cli_type, "--yes"]
        result = cli_client.run_command(
            "drive", "+delete", args, access_token, LARK_CLIENT_ID
        )
        return result
    except Exception as e:
        logger.error(f"Failed to delete Lark document: {str(e)}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def execute_lark_api(
    method: str,
    path: str,
    tool_context: ToolContext,
    params_json: str = "",
    data_json: str = "",
    file_path: str = "",
) -> dict:
    """
    [Universal Tool] Executes any Lark Open API command via the Lark CLI. 
    Use this when no specific tool is available for a desired Lark feature (e.g., Calendar, Bitable, Task, Message).
    **CRITICAL**: Keep your `params_json` and `data_json` as flat, clean single-layer JSON strings to avoid nesting quote errors!

    ### 100% REAL & VERIFIED LARK API EXAMPLES TO PREVENT HALLUCINATION:
    
    1. **Create a Calendar Event (创建日历日程)**:
       - **method**: 'POST'
       - **path**: '/open-apis/calendar/v4/calendars/feishu.cn_xxxxxxxx_xxxx/events'
       - **data_json**: '{"summary": "Project Sync", "start_time": {"timestamp": "1779866173"}, "end_time": {"timestamp": "1779869773"}}'
       
    2. **Add a Bitable Record (向多维表格添加单条记录)**:
       - **method**: 'POST'
       - **path**: '/open-apis/bitable/v1/apps/bascnxxxxxxxxx/tables/tblxxxxxxxxx/records'
       - **data_json**: '{"fields": {"Task Name": "Refactor Code", "Status": "In Progress"}}'
       
    3. **Send a Group Message (发送群组消息/单聊消息)**:
       - **method**: 'POST'
       - **path**: '/open-apis/im/v1/messages?receive_id_type=chat_id'
       - **data_json**: '{"receive_id": "oc_xxxxxxxxxxxxxxxx", "msg_type": "text", "content": "{\\"text\\": \\"Hello World\\"}"}'

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH).
        path: API endpoint path (e.g., '/open-apis/calendar/v4/calendars').
        tool_context: The tool execution context.
        params_json: Optional JSON string of query parameters, e.g., '{"receive_id_type": "chat_id"}'.
        data_json: Optional JSON string for the request body. No nested double-quotes except standard escaping!
        file_path: Optional. Local path to a file for multipart/form-data uploads. Absolute paths and directory traversal are blocked.

    Returns:
        dict: The JSON response from the Lark API.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        args = [method.upper(), path]
        params_arg = _coerce_json_cli_arg(params_json, "params_json")
        data_arg = _coerce_json_cli_arg(data_json, "data_json")
        if params_arg:
            args += ["--params", params_arg]
        if data_arg:
            args += ["--data", data_arg]
        if file_path and file_path.strip():
            file_clean = file_path.strip()
            if "/" in file_clean or ".." in file_clean:
                return {
                    STATUS_KEY: STATUS_ERROR,
                    MESSAGE_KEY: "Absolute paths and parent-directory traversal are blocked for file_path."
                }
            args += ["--file", file_clean]

        return cli_client.run_command("api", "", args, access_token, LARK_CLIENT_ID)
    except Exception as e:
        logger.error(f"Universal API call failed: {str(e)}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}



def feishu_mcp_create_doc(
    title: str = "",
    markdown: str = "",
    tool_context: ToolContext = None,
    folder_token: str = "",
    wiki_node: str = "",
    wiki_space: str = "",
    task_id: str = "",
) -> dict:
    """
    Creates a new Lark Cloud Document using the official MCP logic.

    Args:
        title: Document title. Required when task_id is not provided.
        markdown: Content in Lark-flavored Markdown. Required when task_id is not provided.
        tool_context: Tool execution context.
        folder_token: Optional folder token.
        wiki_node: Optional wiki node token or URL.
        wiki_space: Optional wiki space ID, supports "my_library".
        task_id: Optional async task ID for polling a previous create-doc request.

    Returns:
        dict: Structured result with status and doc fields.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Auth required."}

        args = {}
        if task_id:
            args["task_id"] = task_id
        else:
            if not title.strip():
                return {
                    STATUS_KEY: STATUS_ERROR,
                    MESSAGE_KEY: "title is required when task_id is not provided.",
                }
            if not markdown.strip():
                return {
                    STATUS_KEY: STATUS_ERROR,
                    MESSAGE_KEY: "markdown is required when task_id is not provided.",
                }
            args["title"] = title
            args["markdown"] = _normalize_markdown_for_new_doc(title, markdown)

        if folder_token:
            args["folder_token"] = folder_token
        if wiki_node:
            args["wiki_node"] = wiki_node
        if wiki_space:
            args["wiki_space"] = wiki_space

        result = lark_api_repository.call_feishu_mcp_tool(access_token, "create-doc", args)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        response = {STATUS_KEY: STATUS_SUCCESS, "raw_result": result}
        for key in ("doc_id", "doc_url", "message", "task_id", "log_id", "warnings"):
            if key in data:
                response[key] = data[key]
        if "message" not in response:
            response["message"] = "Document create request completed."
        return response
    except Exception as e:
        logger.error(f"MCP Create Doc Failed: {e}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def save_ai_output_to_feishu_doc(
    title: str,
    markdown_content: str,
    tool_context: ToolContext,
    folder_token: str = "",
    wiki_node: str = "",
    wiki_space: str = "",
) -> dict:
    """
    Save generated AI output into a new Feishu cloud document.

    This is a business-level wrapper around `feishu_mcp_create_doc` intended for
    agent use when the user asks to write the answer into Feishu.
    """
    if not title.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "title is required."}
    if not markdown_content.strip():
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: "markdown_content is required.",
        }

    result = feishu_mcp_create_doc(
        title=title,
        markdown=markdown_content,
        tool_context=tool_context,
        folder_token=folder_token,
        wiki_node=wiki_node,
        wiki_space=wiki_space,
    )
    if result.get(STATUS_KEY) != STATUS_SUCCESS:
        return result

    message = result.get("message", "Document created.")
    doc_url = result.get("doc_url", "")
    doc_id = result.get("doc_id", "")
    normalized = {
        STATUS_KEY: STATUS_SUCCESS,
        "title": title,
        "doc_id": doc_id,
        "doc_url": doc_url,
        "message": message,
    }
    if doc_url:
        normalized["summary"] = f"Created document '{title}' at {doc_url}"
    elif doc_id:
        normalized["summary"] = f"Created document '{title}' (doc_id: {doc_id})"
    else:
        normalized["summary"] = f"Created document '{title}'"
    return normalized


def save_ai_output_to_existing_feishu_doc(
    doc_id: str,
    markdown_content: str,
    tool_context: ToolContext,
    mode: str = "append",
    selection_with_ellipsis: str = "",
    selection_by_title: str = "",
    new_title: str = "",
) -> dict:
    """
    Save generated AI output into an existing Feishu cloud document.

    This is a business-level wrapper around `feishu_mcp_update_doc`.
    """
    if not doc_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "doc_id is required."}

    requires_markdown = mode != "delete_range"
    if requires_markdown and not markdown_content.strip():
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: "markdown_content is required for the selected mode.",
        }

    result = feishu_mcp_update_doc(
        doc_id=doc_id,
        markdown=markdown_content,
        tool_context=tool_context,
        mode=mode,
        selection_with_ellipsis=selection_with_ellipsis,
        selection_by_title=selection_by_title,
        new_title=new_title,
    )
    if result.get(STATUS_KEY) != STATUS_SUCCESS:
        return result

    normalized = {
        STATUS_KEY: STATUS_SUCCESS,
        "doc_id": result.get("doc_id", doc_id),
        "mode": result.get("mode", mode),
        "message": result.get("message", "Document update request completed."),
    }
    for key in ("task_id", "warnings", "log_id", "replace_count", "success"):
        if key in result:
            normalized[key] = result[key]

    normalized["summary"] = (
        f"Updated document '{normalized['doc_id']}' with mode '{normalized['mode']}'"
    )
    return normalized


def wait_for_feishu_doc_create_task(
    task_id: str,
    tool_context: ToolContext,
    max_polls: int = 10,
    poll_interval_seconds: float = 1.0,
) -> dict:
    """
    Poll a previously submitted create-doc async task until it completes or the
    polling window is exhausted.
    """
    if not task_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "task_id is required."}
    if max_polls < 1:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "max_polls must be >= 1."}

    last_result = None
    for attempt in range(1, max_polls + 1):
        result = feishu_mcp_create_doc(
            task_id=task_id,
            tool_context=tool_context,
        )
        last_result = result
        if result.get(STATUS_KEY) != STATUS_SUCCESS:
            return result
        if not result.get("task_id"):
            result["completed"] = True
            result["poll_attempts"] = attempt
            result["summary"] = (
                f"Create task '{task_id}' completed after {attempt} poll(s)."
            )
            return result
        if attempt < max_polls:
            time.sleep(poll_interval_seconds)

    return {
        STATUS_KEY: STATUS_SUCCESS,
        "completed": False,
        "task_id": task_id,
        "poll_attempts": max_polls,
        "message": last_result.get("message", "Create task is still running.")
        if isinstance(last_result, dict)
        else "Create task is still running.",
        "summary": f"Create task '{task_id}' is still running after {max_polls} poll(s).",
        "raw_result": last_result,
    }


def wait_for_feishu_doc_update_task(
    task_id: str,
    tool_context: ToolContext,
    max_polls: int = 10,
    poll_interval_seconds: float = 1.0,
) -> dict:
    """
    Poll a previously submitted update-doc async task until it completes or the
    polling window is exhausted.
    """
    if not task_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "task_id is required."}
    if max_polls < 1:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "max_polls must be >= 1."}

    last_result = None
    for attempt in range(1, max_polls + 1):
        result = feishu_mcp_update_doc(
            task_id=task_id,
            tool_context=tool_context,
        )
        last_result = result
        if result.get(STATUS_KEY) != STATUS_SUCCESS:
            return result
        if not result.get("task_id"):
            result["completed"] = True
            result["poll_attempts"] = attempt
            result["summary"] = (
                f"Update task '{task_id}' completed after {attempt} poll(s)."
            )
            return result
        if attempt < max_polls:
            time.sleep(poll_interval_seconds)

    return {
        STATUS_KEY: STATUS_SUCCESS,
        "completed": False,
        "task_id": task_id,
        "poll_attempts": max_polls,
        "message": last_result.get("message", "Update task is still running.")
        if isinstance(last_result, dict)
        else "Update task is still running.",
        "summary": f"Update task '{task_id}' is still running after {max_polls} poll(s).",
        "raw_result": last_result,
    }


def feishu_mcp_update_doc(
    doc_id: str = "",
    markdown: str = "",
    tool_context: ToolContext = None,
    mode: str = "append",
    selection_with_ellipsis: str = "",
    selection_by_title: str = "",
    new_title: str = "",
    task_id: str = "",
) -> dict:
    """
    Updates a Lark Cloud Document using the official MCP logic.

    Args:
        doc_id: Document ID or URL.
        markdown: Content to update.
        tool_context: Tool execution context.
        mode: Update mode (append, overwrite, replace_range, etc.). Default is 'append'.
        selection_with_ellipsis: Optional content-based locator.
        selection_by_title: Optional heading-based locator.
        new_title: Optional new document title.
        task_id: Optional async task ID for polling a previous update-doc request.

    Returns:
        dict: Success or error message.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Auth required."}

        args = {"mode": mode}
        if task_id:
            args["task_id"] = task_id
        else:
            if not doc_id.strip():
                return {
                    STATUS_KEY: STATUS_ERROR,
                    MESSAGE_KEY: "doc_id is required when task_id is not provided.",
                }
            args["doc_id"] = doc_id
            if markdown:
                args["markdown"] = markdown
            if selection_with_ellipsis:
                args["selection_with_ellipsis"] = selection_with_ellipsis
            if selection_by_title:
                args["selection_by_title"] = selection_by_title
            if new_title:
                args["new_title"] = new_title

        result = lark_api_repository.call_feishu_mcp_tool(access_token, "update-doc", args)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        response = {STATUS_KEY: STATUS_SUCCESS, "raw_result": result}
        for key in (
            "doc_id",
            "task_id",
            "message",
            "mode",
            "warnings",
            "log_id",
            "replace_count",
            "success",
        ):
            if key in data:
                response[key] = data[key]
        if "message" not in response:
            response["message"] = "Document update request completed."
        return response
    except Exception as e:
        logger.error(f"MCP Update Doc Failed: {e}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def feishu_mcp_fetch_doc(
    doc_id: str, tool_context: ToolContext, offset: int = 0, limit: int = 50000
) -> dict:
    """
    Fetches content of a Lark Cloud Document as Markdown via MCP.

    Args:
        doc_id: Document ID or URL.
        tool_context: Tool execution context.
        offset: Character offset for large docs.
        limit: Max characters to return.

    Returns:
        dict: Title and markdown content.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Auth required."}

        args = {"doc_id": doc_id, "offset": offset, "limit": limit}
        result = lark_api_repository.call_feishu_mcp_tool(access_token, "fetch-doc", args)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        response = {STATUS_KEY: STATUS_SUCCESS, "raw_result": result}
        for key in ("doc_id", "title", "markdown", "content", "message", "has_more", "next_offset"):
            if key in data:
                response[key] = data[key]
        if "message" not in response and "markdown" in response:
            response["message"] = "Document fetched successfully."
        return response
    except Exception as e:
        logger.error(f"MCP Fetch Doc Failed: {e}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


async def render_image_as_artifact(
    image_url: str, tool_context: ToolContext
) -> dict:
    """
    Downloads any external image (including Feishu authenticated images with authcode)
    and saves it securely as an ADK Artifact. This bypasses browser CSP blockages
    and makes the image instantly render in the Gemini Enterprise (GE) side-panel.

    Args:
        image_url: The full HTTP/HTTPS URL of the image to render.
        tool_context: The tool execution context.

    Returns:
        dict: A dictionary containing status, artifact_name, and user-friendly messages.
    """
    try:
        from google.genai import types
        import hashlib
        from urllib.parse import urlparse
        
        # 1. 针对飞书链接，添加 Lark access_token 鉴权头以确保下载成功
        headers = {}
        is_feishu = any(domain in image_url for domain in ["feishu.cn", "larksuite.com", "feishu-open.cn"])
        if is_feishu:
            access_token = get_access_token(tool_context)
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
                logger.info("[render_image_as_artifact] Adding Lark bearer token for downloading image.")

        # 2. 从临时链接下载
        from nexus_agent.infrastructure.lark_api_repository import _session
        response = _session.get(image_url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"[render_image_as_artifact] Failed to download image from {image_url}. Status: {response.status_code}")
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: f"Failed to download image. HTTP Status: {response.status_code}"
            }

        img_bytes = response.content
        mime_type = response.headers.get("Content-Type", "image/jpeg")
        if not mime_type.startswith("image/"):
            logger.warning(f"[render_image_as_artifact] Content-Type is {mime_type}, not starting with image/")

        # 3. 提取清洁的文件名并规范化
        parsed_url = urlparse(image_url)
        path_segments = parsed_url.path.strip("/").split("/")
        base_filename = path_segments[-1] if path_segments else ""
        if not base_filename or len(base_filename) < 3:
            url_hash = hashlib.md5(image_url.encode("utf-8")).hexdigest()[:8]
            base_filename = f"img_{url_hash}"

        # 只保留字母、数字、点、减号和下划线，防止非合法文件名字符导致 save_artifact 失败
        base_filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_filename)

        # 根据 MIME 强制匹配正确的后缀
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
            "image/bmp": ".bmp",
        }
        target_ext = ext_map.get(mime_type, "")
        if target_ext and not base_filename.lower().endswith(target_ext):
            base_filename = f"{base_filename}{target_ext}"

        # 加上 render_ 前缀以示区别
        artifact_filename = f"render_{base_filename}"

        # 4. 创建 ADK types.Part 并调用 save_artifact 保存
        artifact_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
        logger.info(f"[render_image_as_artifact] Saving image {artifact_filename} to ADK Artifact Service...")
        version = await tool_context.save_artifact(filename=artifact_filename, artifact=artifact_part)
        logger.info(f"[render_image_as_artifact] Successfully saved {artifact_filename} version {version}")

        return {
            "status": "success",
            "artifact_name": artifact_filename,
            "version": version,
            "message": (
                f"🎉 图片已成功加载，并已注册为安全产物！\n"
                f"- **文件名**：{artifact_filename}\n"
                f"- **版本**：{version}\n"
                f"请在界面查看或直接下载此图片。此外，前端渲染器如果支持，您还可以直接在界面中进行高保真预览。"
            )
        }
    except Exception as e:
        import traceback
        err_msg = f"Failed to render image as artifact. Error: {str(e)}"
        logger.error(f"{err_msg}\n{traceback.format_exc()}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: err_msg,
            "debug_info": traceback.format_exc()
        }

