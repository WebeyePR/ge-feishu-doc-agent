import os
import stat
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lark_agent import tools as lark_tools
from lark_agent.infrastructure.cli_client import GoogleWorkspaceCLIClient, PackagedCLIClient


def test_search_google_drive_files_builds_safe_gws_command(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        captured["access_token"] = access_token
        return {"status": "success", "data": {"files": []}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.search_google_drive_files(
        query="quarterly report",
        tool_context="google-token",
        page_size=100,
    )

    assert result["status"] == "success"
    assert captured["access_token"] == "google-token"
    assert captured["args"][:3] == ["drive", "files", "list"]
    assert '"pageSize": 25' in captured["args"][4]
    assert "name contains 'quarterly report'" in captured["args"][4]
    assert "--fields" in captured["args"]


def test_google_workspace_tool_reports_missing_auth_token():
    class Context:
        state = {}

    result = lark_tools.search_google_drive_files("report", Context())

    assert result["status"] == "error"
    assert "Google Workspace authentication required" in result["message"]


def test_append_google_sheet_rows_accepts_single_row(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.append_google_sheet_rows(
        spreadsheet_id="sheet123",
        values_json='["name", "score"]',
        tool_context="google-token",
    )

    assert result["status"] == "success"
    assert captured["args"][:2] == ["sheets", "+append"]
    assert captured["args"][-1] == '[["name", "score"]]'


def test_create_google_calendar_event_builds_attendee_and_meet_args(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        return {"status": "success", "data": {"id": "evt123"}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.create_google_calendar_event(
        summary="Review",
        start="2026-06-17T09:00:00+08:00",
        end="2026-06-17T09:30:00+08:00",
        attendees_json='["a@example.com", "b@example.com"]',
        add_meet=True,
        tool_context="google-token",
    )

    assert result["status"] == "success"
    assert captured["args"][:2] == ["calendar", "+insert"]
    assert captured["args"].count("--attendee") == 2
    assert "--meet" in captured["args"]


def test_google_workspace_cli_client_injects_token_and_parses_json(tmp_path):
    script_path = tmp_path / "gws"
    script_path.write_text(
        "#!/bin/sh\n"
        "printf '{\"token\":\"%s\",\"config\":\"%s\",\"ca\":\"%s\",\"args\":\"%s\"}' "
        "\"$GOOGLE_WORKSPACE_CLI_TOKEN\" "
        "\"$GOOGLE_WORKSPACE_CLI_CONFIG_DIR\" "
        "\"$SSL_CERT_FILE\" "
        "\"$*\"\n",
        encoding="utf-8",
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

    client = GoogleWorkspaceCLIClient(bin_path=str(script_path))
    result = client.run_command(["drive", "files", "list"], "token-123")

    assert result["status"] == "success"
    assert result["data"]["token"] == "token-123"
    assert result["data"]["config"].endswith(".config/gws")
    assert result["data"]["ca"]
    assert result["data"]["args"] == "drive files list"


def test_google_workspace_cli_client_preserves_existing_ca_bundle(tmp_path, monkeypatch):
    script_path = tmp_path / "gws"
    script_path.write_text(
        "#!/bin/sh\n"
        "printf '{\"ssl\":\"%s\",\"requests\":\"%s\"}' "
        "\"$SSL_CERT_FILE\" "
        "\"$REQUESTS_CA_BUNDLE\"\n",
        encoding="utf-8",
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

    custom_ca = str(tmp_path / "custom-ca.pem")
    monkeypatch.setenv("SSL_CERT_FILE", custom_ca)

    client = GoogleWorkspaceCLIClient(bin_path=str(script_path))
    result = client.run_command(["drive", "files", "list"], "token-123")

    assert result["status"] == "success"
    assert result["data"]["ssl"] == custom_ca
    assert result["data"]["requests"] == custom_ca


def test_packaged_cli_resolves_package_bin_only(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    package_root = project_root / "lark_agent"
    infra_root = package_root / "infrastructure"
    project_bin = project_root / "bin"
    package_bin = package_root / "bin"
    infra_root.mkdir(parents=True)
    project_bin.mkdir()
    package_bin.mkdir()
    (project_bin / "gws").write_text("local", encoding="utf-8")
    (package_bin / "gws").write_text("packaged", encoding="utf-8")

    fake_file = infra_root / "cli_client.py"
    monkeypatch.setattr(
        "lark_agent.infrastructure.cli_client.__file__",
        str(fake_file),
    )

    client = PackagedCLIClient("gws")

    assert client.bin_path == str(package_bin / "gws")


def test_append_google_sheet_rows_rejects_invalid_json_array():
    result = lark_tools.append_google_sheet_rows(
        spreadsheet_id="sheet123",
        values_json="{bad-json}",
        tool_context="google-token",
    )

    assert result["status"] == "error"
    assert "values_json must be a valid JSON array string" in result["message"]


def test_discover_google_workspace_operations_uses_help(monkeypatch):
    def fail_run_command(args, access_token, project_id="", timeout_seconds=120):
        raise AssertionError("registry discovery should not execute gws help")

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fail_run_command)

    result = lark_tools.discover_google_workspace_operations(
        query="list drive files",
        service="drive",
        resource="files",
    )

    assert result["status"] == "success"
    assert result["source"] == "registry"
    assert result["matches"][0]["command_id"] == "drive.files.list"
    assert result["matches"][0]["argv_template"][:3] == ["drive", "files", "list"]


def test_get_google_workspace_command_spec_returns_registry_entry(monkeypatch):
    def fail_run_command(args, access_token, project_id="", timeout_seconds=120):
        raise AssertionError("command spec lookup should not execute gws")

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fail_run_command)

    result = lark_tools.get_google_workspace_command_spec("docs.documents.create")

    assert result["status"] == "success"
    assert result["command_id"] == "docs.documents.create"
    assert result["kind"] == "write"
    assert result["requires_confirmation"] is True
    assert result["argv_template"] == ["docs", "documents", "create", "--json", "<json-body>"]
    assert "schema_json" not in result


def test_get_google_workspace_operation_schema_builds_schema_command(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        return {"status": "success", "data": {"response": {"$ref": "File"}}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.get_google_workspace_operation_schema(
        method_path="drive.files.list",
        tool_context="google-token",
    )

    assert result["status"] == "success"
    assert captured["args"] == ["schema", "drive.files.list"]
    assert "schema_json" in result
    assert "$ref" in result["schema_json"]
    assert "response" not in result


def test_create_google_doc_with_text_creates_then_writes(monkeypatch):
    calls = []

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        calls.append(args)
        if args[:3] == ["drive", "files", "create"]:
            return {"status": "success", "data": {"id": "doc123"}}
        return {"status": "error", "message": "unexpected command"}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.create_google_doc_with_text(
        title="Test Doc",
        text="测试内容",
        tool_context="google-token",
    )

    assert result["status"] == "success"
    assert result["document_id"] == "doc123"
    assert calls[0][:3] == ["drive", "files", "create"]


def test_create_google_doc_with_text_wraps_write_failure(monkeypatch):
    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        return {"status": "error", "message": "upload and convert failed"}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.create_google_doc_with_text(
        title="Test Doc",
        text="测试内容",
        tool_context="google-token",
    )

    assert result["status"] == "error"
    assert result["step"] == "upload_and_convert"


def test_create_google_sheet_with_rows_creates_then_appends(monkeypatch):
    calls = []

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        calls.append(args)
        if args[:3] == ["sheets", "spreadsheets", "create"]:
            return {
                "status": "success",
                "data": {
                    "spreadsheetId": "sheet123",
                    "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet123/edit",
                },
            }
        if args[:2] == ["sheets", "+append"]:
            return {"status": "success", "data": {"ok": True}}
        return {"status": "error", "message": "unexpected command"}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.create_google_sheet_with_rows(
        title="Test Sheet",
        values_json='[["字段","值"],["测试","通过"]]',
        tool_context="google-token",
    )

    assert result["status"] == "success"
    assert result["spreadsheet_id"] == "sheet123"
    assert calls[0][:3] == ["sheets", "spreadsheets", "create"]
    assert calls[1][:4] == ["sheets", "+append", "--spreadsheet", "sheet123"]
    assert '[["字段", "值"], ["测试", "通过"]]' in calls[1]


def test_execute_google_workspace_cli_runs_safe_read_command(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        captured["timeout_seconds"] = timeout_seconds
        return {"status": "success", "data": {"files": []}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.execute_google_workspace_cli(
        args_json='["drive","files","list","--params","{\\"pageSize\\":10}","--fields","files(id,name)"]',
        tool_context="google-token",
        timeout_seconds=999,
    )

    assert result["status"] == "success"
    assert result["command_id"] == "drive.files.list"
    assert captured["args"][:3] == ["drive", "files", "list"]
    assert captured["timeout_seconds"] == 300


def test_execute_google_workspace_cli_adds_dry_run_for_mutating_command(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        return {"status": "success", "data": {"dryRun": True}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.execute_google_workspace_cli(
        args_json='["sheets","spreadsheets","create","--json","{\\"properties\\":{\\"title\\":\\"Demo\\"}}"]',
        tool_context="google-token",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert "--dry-run" in captured["args"]


def test_execute_google_workspace_cli_adds_dry_run_for_write_verbs(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        return {"status": "success", "data": {"dryRun": True}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.execute_google_workspace_cli(
        args_json='["drive","files","copy","--file-id","file123"]',
        tool_context="google-token",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert "--dry-run" in captured["args"]


def test_execute_google_workspace_cli_blocks_mutating_command_without_confirmation():
    result = lark_tools.execute_google_workspace_cli(
        args_json='["gmail","+send","--to","a@example.com","--subject","Hi","--body","Hello"]',
        tool_context="google-token",
        dry_run=False,
        allow_mutating=False,
    )

    assert result["status"] == "error"
    assert "appears to mutate data" in result["message"]


def test_execute_google_workspace_cli_blocks_unsupported_service():
    result = lark_tools.execute_google_workspace_cli(
        args_json='["storage","buckets","list"]',
        tool_context="google-token",
    )

    assert result["status"] == "error"
    assert "Unsupported gws service" in result["message"]


def test_discover_google_workspace_operations_rejects_flag_resource():
    result = lark_tools.discover_google_workspace_operations(
        service="drive",
        resource="files --output /tmp/out",
        tool_context="google-token",
    )

    assert result["status"] == "error"
    assert "resource contains an invalid path segment" in result["message"]


def test_execute_google_workspace_cli_rejects_non_string_args():
    result = lark_tools.execute_google_workspace_cli(
        args_json='["drive", 123]',
        tool_context="google-token",
    )

    assert result["status"] == "error"
    assert "strings only" in result["message"]


def test_packaged_cli_log_redacts_sensitive_argument_values(tmp_path):
    client = PackagedCLIClient("gws", bin_path=str(tmp_path / "gws"))

    formatted = client._format_args_for_log(
        [
            "/tmp/gws",
            "docs",
            "+write",
            "--document",
            "doc123",
            "--text",
            "confidential body",
            "--params",
            '{"q":"secret"}',
        ]
    )

    assert "confidential body" not in formatted
    assert '{"q":"secret"}' not in formatted
    assert formatted.count("<redacted>") == 2


def test_execute_google_workspace_cli_flat_runs_safe_command(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        captured["timeout_seconds"] = timeout_seconds
        return {"status": "success", "data": {"files": []}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    result = lark_tools.execute_google_workspace_cli_flat(
        service="drive",
        resource="files",
        method="list",
        params_json='{"pageSize": 10}',
        tool_context="google-token",
    )

    assert result["status"] == "success"
    assert captured["args"][:3] == ["drive", "files", "list"]
    assert "--params" in captured["args"]
    assert '{"pageSize": 10}' in captured["args"]


def test_execute_google_workspace_cli_flat_cleans_json_input(monkeypatch):
    captured = {}

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        captured["args"] = args
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    # 包含外层多余引号的 JSON 参数
    result = lark_tools.execute_google_workspace_cli_flat(
        service="sheets",
        resource="spreadsheets",
        method="create",
        json_body='\'{"properties": {"title": "Demo"}}\'',
        tool_context="google-token",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert "--json" in captured["args"]
    # 验证是否成功清洗了外层的单引号，变回合法的 JSON
    assert '{"properties": {"title": "Demo"}}' in captured["args"]
    assert "--dry-run" in captured["args"]


def test_execute_google_workspace_cli_flat_blocks_mutating_command(monkeypatch):
    result = lark_tools.execute_google_workspace_cli_flat(
        service="gmail",
        resource="users messages",
        method="send",
        json_body='{"raw": "xyz"}',
        tool_context="google-token",
        dry_run=False,
        allow_mutating=False,
    )

    assert result["status"] == "error"
    assert "appears to mutate data" in result["message"]


def test_list_google_calendar_events_anticrash():
    # 测试 tool_context = None 时，能够平稳返回 error 字典而不崩溃
    result = lark_tools.list_google_calendar_events(
        tool_context=None,
        days=7,
        calendar=None,  # 传入非 string，测试其对 strip() 的类型守卫
        timezone=123,   # 传入非 string，测试其对 strip() 的类型守卫
    )
    assert result["status"] == "error"
    assert "Google Workspace CLI execution failed" in result["message"] or "authentication" in result["message"]


def test_create_google_calendar_event_anticrash():
    # 1. 测试 summary, start, end 缺失或为 None 等各种非法输入时的类型守卫与防崩拦截
    result = lark_tools.create_google_calendar_event(
        summary=None,
        start="2026-06-17T09:00:00+08:00",
        end="2026-06-17T10:00:00+08:00",
        tool_context="google-token"
    )
    assert result["status"] == "error"
    assert "summary is required" in result["message"]

    result2 = lark_tools.create_google_calendar_event(
        summary="会议",
        start=123,  # 非 str
        end="2026-06-17T10:00:00+08:00",
        tool_context="google-token"
    )
    assert result2["status"] == "error"
    assert "start is required" in result2["message"]

    # 2. 测试 tool_context 为 None 时，即使参数正确也绝对不崩溃
    result3 = lark_tools.create_google_calendar_event(
        summary="会议",
        start="2026-06-17T09:00:00+08:00",
        end="2026-06-17T10:00:00+08:00",
        tool_context=None,
        calendar={"invalid": "type"},  # 非 str
        description=["invalid", "desc"],  # 非 str
        location=None,  # None
    )
    assert result3["status"] == "error"
    assert "Google Workspace CLI execution failed" in result3["message"] or "authentication" in result3["message"]


