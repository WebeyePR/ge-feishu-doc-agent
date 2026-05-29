import os
import sys
import pytest

# 引入项目根路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nexus_agent import tools as lark_tools


def test_discover_lark_operations_matches_registry(monkeypatch):
    """
    测试 Lark 发现工具 (discover_lark_operations) 能成功检索本地命令注册表
    并且不触发任何网络或 CLI 请求
    """
    def fail_run_command(*args, **kwargs):
        raise AssertionError("注册表本地发现不应该触发飞书 CLI 请求")

    monkeypatch.setattr(lark_tools.cli_client, "run_command", fail_run_command)

    # 1. 测试搜索文档提取
    result = lark_tools.discover_lark_operations(
        query="fetch documents",
        service="docs",
    )
    assert result["status"] == "success"
    assert result["source"] == "lark_registry"
    assert len(result["matches"]) > 0
    # 查找是否有 docs.+fetch
    doc_fetch_matches = [m for m in result["matches"] if m["command_id"] == "docs.+fetch"]
    assert len(doc_fetch_matches) > 0

    # 2. 测试不支持的服务
    bad_result = lark_tools.discover_lark_operations(service="unsupported_service")
    assert bad_result["status"] == "error"
    assert "Unsupported Feishu/Lark service" in bad_result["message"]


def test_get_lark_command_spec_returns_local_data(monkeypatch):
    """
    测试根据 command_id 查询特定的飞书命令参数 spec 元数据
    """
    def fail_run_command(*args, **kwargs):
        raise AssertionError("获取 Spec 应该走本地注册表，不触发 CLI 运行")

    monkeypatch.setattr(lark_tools.cli_client, "run_command", fail_run_command)

    result = lark_tools.get_lark_command_spec("docs.+fetch")
    assert result["status"] == "success"
    assert result["command_id"] == "docs.+fetch"
    assert result["kind"] == "read"
    assert result["requires_confirmation"] is False
    assert result["argv_template"][:2] == ["docs", "+fetch"]

    # 查询一个不存在的 ID
    bad_result = lark_tools.get_lark_command_spec("non_existent_command_id")
    assert bad_result["status"] == "error"
    assert "was not found in the local registry" in bad_result["message"]


def test_get_lark_operation_schema_executes_cli_schema(monkeypatch):
    """
    测试 get_lark_operation_schema 正常拼装 "schema [path]" 命令发送给 feishu CLI
    """
    captured = {}

    def fake_run_command(args, access_token, client_id="", timeout_seconds=120):
        captured["args"] = args
        captured["access_token"] = access_token
        return {"status": "success", "data": {"$schema": "http://json-schema.org/draft-04/schema#", "type": "object"}}

    monkeypatch.setattr(lark_tools.cli_client, "run_command", fake_run_command)

    result = lark_tools.get_lark_operation_schema(
        method_path="docs.+fetch",
        tool_context="mock-lark-token",
    )

    assert result["status"] == "success"
    assert captured["args"] == ["schema", "docs.+fetch"]
    assert captured["access_token"] == "mock-lark-token"
    assert "schema_json" in result
    assert "$schema" in result["schema_json"]


def test_execute_lark_cli_runs_safe_read_command(monkeypatch):
    """
    测试使用参数数组形式执行安全的飞书只读 CLI 命令
    """
    captured = {}

    def fake_run_command(args, access_token, client_id="", timeout_seconds=120):
        captured["args"] = args
        captured["timeout_seconds"] = timeout_seconds
        return {"status": "success", "data": {"items": []}}

    monkeypatch.setattr(lark_tools.cli_client, "run_command", fake_run_command)

    result = lark_tools.execute_lark_cli(
        args_json='["docs", "+fetch", "--api-version", "v2", "--document-token", "doc123"]',
        tool_context="mock-lark-token",
        timeout_seconds=200,
    )

    assert result["status"] == "success"
    assert result["command_id"] == "docs.+fetch"
    assert result["command_kind"] == "read"
    assert captured["args"][:2] == ["docs", "+fetch"]
    assert captured["timeout_seconds"] == 200


def test_execute_lark_cli_adds_dry_run_for_mutating_command(monkeypatch):
    """
    测试对于写突变命令，在开启干跑 (dry_run=True) 时，自动追加 --dry-run
    """
    captured = {}

    def fake_run_command(args, access_token, client_id="", timeout_seconds=120):
        captured["args"] = args
        return {"status": "success", "data": {"dryRun": True}}

    monkeypatch.setattr(lark_tools.cli_client, "run_command", fake_run_command)

    # 飞书的增加记录是突变写命令
    result = lark_tools.execute_lark_cli(
        args_json='["apps", "+create", "--name", "调研问卷", "--app-type", "HTML"]',
        tool_context="mock-lark-token",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert "--dry-run" in captured["args"]


def test_execute_lark_cli_blocks_mutation_when_disallowed():
    """
    测试如果未开启 data mutating 且 dry_run=False，直接拦截飞书突变命令
    """
    result = lark_tools.execute_lark_cli(
        args_json='["apps", "+create", "--name", "调研问卷", "--app-type", "HTML"]',
        tool_context="mock-lark-token",
        dry_run=False,
        allow_mutating=False,
    )

    assert result["status"] == "error"
    assert "appears to mutate data" in result["message"]


def test_execute_lark_cli_rejects_malicious_inputs():
    """
    测试传入异常和攻击性命令参数被严密防御阻断
    """
    # 1. 绝不允许路径穿越
    result1 = lark_tools.execute_lark_cli(
        args_json='["docs", "+fetch", "../../../etc/passwd"]',
        tool_context="mock-lark-token",
    )
    assert result1["status"] == "error"
    assert "parent-directory traversal are not allowed" in result1["message"]

    # 2. 不支持非法服务名
    result2 = lark_tools.execute_lark_cli(
        args_json='["dangerous_service", "run", "something"]',
        tool_context="mock-lark-token",
    )
    assert result2["status"] == "error"
    assert "Unsupported Feishu/Lark service" in result2["message"]


def test_execute_lark_cli_flat_runs_smoothly(monkeypatch):
    """
    测试优先推荐的扁平接口扁平调用飞书 CLI
    """
    captured = {}

    def fake_run_command(args, access_token, client_id="", timeout_seconds=120):
        captured["args"] = args
        return {"status": "success", "data": {"id": "doc123"}}

    monkeypatch.setattr(lark_tools.cli_client, "run_command", fake_run_command)

    # 1. 只读列表调用
    result1 = lark_tools.execute_lark_cli_flat(
        service="drive",
        resource="file",
        method="list",
        params_json='{"pageSize": 10}',
        tool_context="mock-lark-token",
    )
    assert result1["status"] == "success"
    assert captured["args"][:3] == ["drive", "file", "list"]
    assert "--params" in captured["args"]
    assert '{"pageSize": 10}' in captured["args"]

    # 2. 具有干跑防护的写调用
    result2 = lark_tools.execute_lark_cli_flat(
        service="apps",
        resource="",
        method="+create",
        json_body='\'{"name": "test"}\'',  # 附带单引号脏输入
        tool_context="mock-lark-token",
        dry_run=True,
    )
    assert result2["status"] == "success"
    assert captured["args"][:2] == ["apps", "+create"]
    # 验证是否成功清洗了外层的多余单引号
    assert '{"name": "test"}' in captured["args"]
    assert "--dry-run" in captured["args"]


def test_execute_lark_cli_flat_blocks_unauthorized_mutation():
    """
    测试扁平调用在未开启 mutating 和 dry_run 时拦截突变
    """
    result = lark_tools.execute_lark_cli_flat(
        service="apps",
        resource="",
        method="+create",
        json_body='{"name": "test"}',
        tool_context="mock-lark-token",
        dry_run=False,
        allow_mutating=False,
    )

    assert result["status"] == "error"
    assert "appears to mutate data" in result["message"]


def test_cli_command_registry_multi_instance_extensibility():
    """
    【多平台支持验证】测试 CLICommandRegistry 的多实例自治性。
    通过多实例隔离，未来可以零修改地轻松扩展出 钉钉(DingTalk)、企微(WeCom) 等新平台的注册表服务。
    """
    from nexus_agent.lark_registry.registry import CLICommandRegistry

    # 1. 实例化 Lark 注册表服务
    registry_lark = CLICommandRegistry("nexus_agent.lark_registry")
    
    # 2. 实例化一个不存在的或空的平台注册表服务（用于模拟未来新平台的空状态/缺失状态）
    registry_wecom = CLICommandRegistry("nexus_agent.wecom_registry", config_name="wecom_commands.json")

    # 3. 验证实例间的底层数据完全隔离
    lark_data = registry_lark._load_registry()
    wecom_data = registry_wecom._load_registry()

    assert len(lark_data) > 0
    assert len(wecom_data) == 0  # wecom 因文件不存在降级为空
    assert id(lark_data) != id(wecom_data)

    # 4. 验证面向对象多实例独立的匹配方法
    match_lark = registry_lark.get_command_spec("docs.+fetch")
    match_wecom = registry_wecom.get_command_spec("docs.+fetch")

    assert match_lark is not None
    assert match_wecom is None  # 另一个平台无法跨界检索，完全符合解耦规范


def test_execute_lark_cli_extreme_inputs():
    """
    测试通用执行器拦截各种极端违规、逃逸尝试或格式错误的输入
    """
    # 1. 空 JSON array 参数
    result_empty = lark_tools.execute_lark_cli(
        args_json="[]",
        tool_context="mock-token",
    )
    assert result_empty["status"] == "error"
    assert "args_json must be a non-empty JSON array" in result_empty["message"]

    # 2. 非 string 类型的注入
    result_invalid_type = lark_tools.execute_lark_cli(
        args_json='["docs", "+fetch", 12345]',
        tool_context="mock-token",
    )
    assert result_invalid_type["status"] == "error"
    assert "strings only" in result_invalid_type["message"]

    # 3. 超长超限参数防御
    huge_arg = "A" * 30000
    result_too_long = lark_tools.execute_lark_cli(
        args_json=f'["docs", "+fetch", "{huge_arg}"]',
        tool_context="mock-token",
    )
    assert result_too_long["status"] == "error"
    assert "too long" in result_too_long["message"]

