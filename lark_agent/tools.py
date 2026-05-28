import json
import logging
import re
import time


from google.adk.tools import ToolContext

from lark_agent.config import (
    GOOGLE_WORKSPACE_AUTH_ID,
    GOOGLE_WORKSPACE_PROJECT_ID,
    LARK_AUTH_ID,
    LARK_CLIENT_ID,
)
from lark_agent.gws_registry import (
    find_command_for_args,
    get_command_spec,
    search_commands,
)
from lark_agent.infrastructure import lark_api_repository
from lark_agent.infrastructure.cli_client import cli_client, gws_cli_client

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


def _run_google_workspace_cli(args: list, tool_context: ToolContext, timeout_seconds: int = 120) -> dict:
    access_token = get_google_workspace_access_token(tool_context)
    if not access_token:
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: (
                "Google Workspace authentication required. "
                f"Missing token in tool_context.state['{GOOGLE_WORKSPACE_AUTH_ID}']."
            ),
        }
    try:
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
    Create a Google Docs document and append plain text content.
    """
    if not title or not title.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "title is required."}
    if not text:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "text is required."}

    create_result = _run_google_workspace_cli(
        [
            "docs",
            "documents",
            "create",
            "--json",
            json.dumps({"title": title.strip()}, ensure_ascii=False),
        ],
        tool_context,
    )
    if create_result.get(STATUS_KEY) != STATUS_SUCCESS:
        return {
            STATUS_KEY: STATUS_ERROR,
            "step": "create_google_doc",
            MESSAGE_KEY: "Failed to create Google Docs document.",
            "details": create_result,
        }

    created = create_result.get("data") or {}
    document_id = created.get("documentId") or created.get("id")
    if not document_id:
        return {
            STATUS_KEY: STATUS_ERROR,
            "step": "extract_google_doc_id",
            MESSAGE_KEY: "Google Docs create response did not include documentId.",
            "details": create_result,
        }

    write_result = append_google_doc_text(document_id=document_id, text=text, tool_context=tool_context)
    if write_result.get(STATUS_KEY) != STATUS_SUCCESS:
        return {
            STATUS_KEY: STATUS_ERROR,
            "step": "write_google_doc",
            MESSAGE_KEY: "Google Docs document was created, but writing content failed.",
            "document_id": document_id,
            "document_url": f"https://docs.google.com/document/d/{document_id}/edit",
            "details": write_result,
        }

    return {
        STATUS_KEY: STATUS_SUCCESS,
        MESSAGE_KEY: "Google Docs document created and populated successfully.",
        "document_id": document_id,
        "document_url": f"https://docs.google.com/document/d/{document_id}/edit",
        "create_result": create_result,
        "write_result": write_result,
    }


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
    days = _safe_limit(days, default=7, minimum=1, maximum=31)
    args = ["calendar", "+agenda", "--days", str(days), "--format", "json"]
    if calendar and calendar.strip():
        args += ["--calendar", calendar.strip()]
    if timezone and timezone.strip():
        args += ["--timezone", timezone.strip()]
    return _run_google_workspace_cli(args, tool_context)


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
    if not summary or not summary.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "summary is required."}
    if not start or not start.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "start is required."}
    if not end or not end.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "end is required."}

    args = [
        "calendar",
        "+insert",
        "--calendar",
        (calendar or "primary").strip(),
        "--summary",
        summary.strip(),
        "--start",
        start.strip(),
        "--end",
        end.strip(),
    ]
    if description and description.strip():
        args += ["--description", description.strip()]
    if location and location.strip():
        args += ["--location", location.strip()]
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
    doc_token: str, tool_context: ToolContext
) -> dict:
    """
    Retrieves the content of a specific Lark document (docx only) in high-quality Lark-flavored Markdown format.
    This uses Feishu's V2 Docs AI fetch API to return extremely high-fidelity Markdown, including tables, lists, and callout blocks.
    It automatically detects embedded images, downloads them in the background, and registers them as secure ADK Artifacts.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.

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
        img_pattern = r'!\[(.*?)\]\((https?://[^\s)]+)\)'
        matches = re.findall(img_pattern, content)

        if matches:
            logger.info(f"[get_lark_document_markdown] Found {len(matches)} images in markdown. Saving as artifacts...")
            from lark_agent.infrastructure.lark_api_repository import _session
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

                    response = _session.get(img_url, headers=req_headers, timeout=20)
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
                except Exception as ex:
                    logger.error(f"[get_lark_document_markdown] Failed to save image {img_url} as artifact: {ex}")

            # 替换 Markdown 中的图片标注，添加指向 Artifact 的高保真提示词
            def replace_img_tag(match):
                alt = match.group(1)
                url = match.group(2)
                if url in url_to_artifact:
                    artifact_filename, version = url_to_artifact[url]
                    return (
                        f"![{alt}]({url})\n"
                        f"*(📷 该图片已作为 ADK 产物成功渲染，请在右侧‘产物/Artifacts’面板中查看：`{artifact_filename}`)*"
                    )
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
        from lark_agent.infrastructure.lark_api_repository import _session
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
                f"🎉 图片已成功通过后台下载，并已注册为 ADK Artifact 在侧边栏/预览面板中渲染展示！\n"
                f"- **文件名**：{artifact_filename}\n"
                f"- **版本**：{version}\n"
                f"请在界面右侧的 'Artifacts' (或‘产物’) 标签下查看或直接下载此图片。此外，前端渲染器如果支持，您还可以直接在界面中进行高保真预览。"
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

