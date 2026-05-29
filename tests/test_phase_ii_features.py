import os
import sys
import pytest
import asyncio
import re
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lark_agent import tools as lark_tools


def test_get_lark_document_markdown_lazy_loading(monkeypatch):
    """
    Verify that when download_images is False, we do not attempt to download images,
    and we return the Markdown content unchanged.
    """
    # 1. Mock token retrieval
    monkeypatch.setattr(lark_tools, "get_access_token", lambda ctx: "fake-token")

    # 2. Mock document retrieval with markdown containing images
    test_markdown = "Hello World\n\n![Image Logo](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=abc)\n\nEnd"
    monkeypatch.setattr(
        lark_tools.lark_api_repository,
        "get_document_markdown",
        lambda access_token, doc_token: test_markdown,
    )

    # 3. Call get_lark_document_markdown with download_images=False (default)
    mock_tool_context = MagicMock()
    result = asyncio.run(
        lark_tools.get_lark_document_markdown(
            doc_token="docx_token_123",
            tool_context=mock_tool_context,
            download_images=False
        )
    )

    # 4. Verify result is successful and content has original url untouched (no download attempted)
    assert result["status"] == "success"
    assert "https://internal-api-drive-stream.feishu.cn" in result["content"]
    assert "lark_" not in result["content"]


def test_get_lark_document_markdown_soft_failure_on_download_error(monkeypatch):
    """
    Verify that if download_images is True but downloading the image fails,
    we do not throw any exception, and instead preserve the original URL in the markdown.
    """
    # 1. Mock token retrieval
    monkeypatch.setattr(lark_tools, "get_access_token", lambda ctx: "fake-token")

    # 2. Mock document retrieval with markdown containing images
    test_markdown = "Hello World\n\n![Image Logo](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=abc)\n\nEnd"
    monkeypatch.setattr(
        lark_tools.lark_api_repository,
        "get_document_markdown",
        lambda access_token, doc_token: test_markdown,
    )

    # 3. Mock image download to throw an exception
    class MockSession:
        def get(self, url, headers=None, timeout=None):
            raise Exception("Connection timeout")

    import lark_agent.infrastructure.lark_api_repository as repo
    monkeypatch.setattr(repo, "_session", MockSession())

    # 4. Call get_lark_document_markdown with download_images=True
    mock_tool_context = MagicMock()
    result = asyncio.run(
        lark_tools.get_lark_document_markdown(
            doc_token="docx_token_123",
            tool_context=mock_tool_context,
            download_images=True
        )
    )

    # 5. Verify the tool did not crash, returned success, and kept the original URL
    assert result["status"] == "success"
    assert "https://internal-api-drive-stream.feishu.cn" in result["content"]
    assert "lark_" not in result["content"]


def test_create_google_doc_with_text_html_conversion_and_cleanup(monkeypatch):
    """
    Verify that create_google_doc_with_text converts markdown to HTML,
    properly uploads using Drive files create, and cleans up the temporary file in the end.
    """
    calls = []
    temp_files_found = []

    def fake_run_command(args, access_token, project_id="", timeout_seconds=120):
        calls.append(args)
        # Check if the temporary relative path HTML file is provided as argument
        upload_idx = args.index("--upload")
        temp_filename = args[upload_idx + 1]
        temp_files_found.append(temp_filename)
        
        # Verify the temporary file actually exists in Cwd during the execution
        assert os.path.exists(temp_filename)
        with open(temp_filename, "r", encoding="utf-8") as f:
            html = f.read()
            # Ensure it contains h2 and list elements translated from markdown
            assert "<h2>My Subtitle</h2>" in html
            assert "<li>Item 1</li>" in html or "<li>Item 1" in html

        return {"status": "success", "data": {"id": "newdoc_999"}}

    monkeypatch.setattr(lark_tools.gws_cli_client, "run_command", fake_run_command)

    # Call the tool with markdown text
    markdown_text = "# My Title\n## My Subtitle\n- Item 1\n- Item 2\n\nSome text with **bold**."
    result = lark_tools.create_google_doc_with_text(
        title="My Epic Document",
        text=markdown_text,
        tool_context="gws-token"
    )

    # Verify response
    assert result["status"] == "success"
    assert result["document_id"] == "newdoc_999"
    assert "document_url" in result

    # Verify that the temporary relative file was successfully cleaned up afterward
    assert len(temp_files_found) == 1
    temp_file = temp_files_found[0]
    assert not os.path.exists(temp_file)


def test_lark_cli_client_polymorphic_signatures(monkeypatch):
    """
    针对 CLIClient 的全新多态 run_command 方法进行完备的单元测试，
    同时验证历史遗留签名（风格 2）与新加固统一签名（风格 1）在解析和传参上的 100% 正确。
    """
    from lark_agent.infrastructure.cli_client import cli_client

    captured_runs = []

    def mock_run(args, env, tmp_home, timeout_seconds=120):
        captured_runs.append({
            "args": args,
            "env": env,
            "tmp_home": tmp_home,
            "timeout_seconds": timeout_seconds
        })
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(cli_client, "_run", mock_run)

    # 1. 验证历史陈旧 positional 签名（风格 2）
    # run_command(service, command, args, access_token, app_id)
    captured_runs.clear()
    res1 = cli_client.run_command(
        "docs",
        "+create",
        ["--title", "polymorphic-test"],
        "fake-access-token",
        "fake-app-id"
    )
    assert res1["status"] == "success"
    assert len(captured_runs) == 1
    run_info = captured_runs[0]
    assert run_info["args"] == ["docs", "+create", "--title", "polymorphic-test"]
    assert run_info["env"]["LARKSUITE_CLI_APP_ID"] == "fake-app-id"
    assert run_info["env"]["LARKSUITE_CLI_USER_ACCESS_TOKEN"] == "fake-access-token"
    assert run_info["timeout_seconds"] == 120

    # 2. 验证新版加固统一参数签名（风格 1）
    # run_command(args=["docs", "+fetch"], access_token="...", client_id="...", timeout_seconds=150)
    captured_runs.clear()
    res2 = cli_client.run_command(
        args=["docs", "+fetch", "--document-token", "abc"],
        access_token="fake-token-new",
        client_id="fake-client-new",
        timeout_seconds=150
    )
    assert res2["status"] == "success"
    assert len(captured_runs) == 1
    run_info = captured_runs[0]
    assert run_info["args"] == ["docs", "+fetch", "--document-token", "abc"]
    assert run_info["env"]["LARKSUITE_CLI_APP_ID"] == "fake-client-new"
    assert run_info["env"]["LARKSUITE_CLI_USER_ACCESS_TOKEN"] == "fake-token-new"
    assert run_info["timeout_seconds"] == 150

    # 3. 验证混合 keyword-arguments（带有 app_id 作为 client_id 的兜底）
    captured_runs.clear()
    res3 = cli_client.run_command(
        args=["im", "message", "send"],
        access_token="fake-mixed-token",
        app_id="fake-app-id-mixed"
    )
    assert res3["status"] == "success"
    assert captured_runs[0]["env"]["LARKSUITE_CLI_APP_ID"] == "fake-app-id-mixed"
    assert captured_runs[0]["env"]["LARKSUITE_CLI_USER_ACCESS_TOKEN"] == "fake-mixed-token"


def test_extreme_args_length_and_injection():
    """
    极限测试 1：验证超长参数、控制字符（\x00, \n, \r）注入在 Lark CLI 与 GWS CLI 执行层中的先验静态阻断。
    """
    # 1. 验证超长参数拦截（>20000 字节）
    long_arg = "a" * 20001
    bad_args_json = f'["docs", "+fetch", "{long_arg}"]'
    result = lark_tools.execute_lark_cli(
        args_json=bad_args_json,
        tool_context="fake-ctx"
    )
    assert result["status"] == "error"
    assert "too long" in result["message"]

    # 2. 验证控制字符 \x00 (Null Byte) 截断攻击拦截
    null_byte_args_json = '["docs", "+fetch", "some\\u0000arg"]'
    result2 = lark_tools.execute_lark_cli(
        args_json=null_byte_args_json,
        tool_context="fake-ctx"
    )
    assert result2["status"] == "error"
    assert "control characters" in result2["message"]

    # 3. 验证控制字符 \n, \r 换行注入拦截
    newline_args_json = '["docs", "+fetch", "some\\narg"]'
    result3 = lark_tools.execute_lark_cli(
        args_json=newline_args_json,
        tool_context="fake-ctx"
    )
    assert result3["status"] == "error"
    assert "control characters" in result3["message"]


def test_path_traversal_and_blocked_flags():
    """
    极限测试 2：验证绝对路径（如 /etc/passwd）、路径穿越（../）以及高危黑名单 Flag 在静态拦截层被 100% 拒绝。
    """
    # 1. 验证绝对路径拦截
    abs_path_args = '["docs", "+fetch", "/etc/passwd"]'
    result1 = lark_tools.execute_lark_cli(
        args_json=abs_path_args,
        tool_context="fake-ctx"
    )
    assert result1["status"] == "error"
    assert "Absolute paths" in result1["message"]

    # 2. 验证路径穿越拦截
    traversal_args = '["docs", "+fetch", "../../etc/passwd"]'
    result2 = lark_tools.execute_lark_cli(
        args_json=traversal_args,
        tool_context="fake-ctx"
    )
    assert result2["status"] == "error"
    assert "parent-directory traversal" in result2["message"]

    # 3. 验证命令行高危黑名单 Flag 拦截
    blocked_flag_args = '["docs", "+fetch", "--credentials-file", "pass.json"]'
    result3 = lark_tools.execute_lark_cli(
        args_json=blocked_flag_args,
        tool_context="fake-ctx"
    )
    assert result3["status"] == "error"
    assert "not allowed" in result3["message"]


def test_registry_tokenizer_boundary_conditions():
    """
    极限测试 3：对通用 CLICommandRegistry 里的 _tokenize 分词器进行极端边界校验（如 None, "", 纯符号, Unicode 混淆词素等），
    确保其不发生异常崩溃（crash-safe）并具备优秀的降级和分词合并机制。
    """
    from lark_agent.lark_registry.registry import CLICommandRegistry
    
    registry = CLICommandRegistry("lark_agent")

    # 1. 测试 None 输入
    assert registry._tokenize(None) == set()

    # 2. 测试空字符串输入
    assert registry._tokenize("") == set()

    # 3. 测试纯特殊符号（如 !!!, @#$）
    assert registry._tokenize("!!!") == set()
    assert registry._tokenize("   ") == set()

    # 4. 测试包含 '+'、'.'、'_'、'-' 的词素分词及其层级拆分，以增强模糊检索匹配
    token_set = registry._tokenize("docs.+fetch_item-v1")
    # 应保留原样
    assert "docs.+fetch_item-v1" in token_set
    # 应根据特殊符号拆分为纯净字母数字
    assert "docs" in token_set
    assert "fetch" in token_set
    assert "item" in token_set
    assert "v1" in token_set


    # 5. 测试 Unicode 混淆字符/中文分词（由于正则分词是 [^a-z0-9+_.-]+，中文也会作为分隔符或者被剔除，但应保证绝不 crash）
    cn_token_set = registry._tokenize("feishu 飞书 docs.fetch_item")
    assert "feishu" in cn_token_set
    assert "docs.fetch_item" in cn_token_set
    assert "fetch" in cn_token_set


def test_invalid_argument_formats_and_types():
    """
    极限测试 4：验证对于畸形 JSON、不合规的参数元素类型、空参数列表的优雅拒绝及 ValueError 抛出。
    """
    # 1. 验证参数列表非 string（例如含有数字）
    invalid_type_args = '["docs", "+fetch", 12345]'
    result1 = lark_tools.execute_lark_cli(
        args_json=invalid_type_args,
        tool_context="fake-ctx"
    )
    assert result1["status"] == "error"
    assert "strings only" in result1["message"]

    # 2. 验证空 JSON 数组
    empty_args = '[]'
    result2 = lark_tools.execute_lark_cli(
        args_json=empty_args,
        tool_context="fake-ctx"
    )
    assert result2["status"] == "error"
    assert "non-empty JSON array" in result2["message"]

    # 3. 验证非 JSON 格式畸形字符串
    bad_json_string = '{"service": "docs"}'
    result3 = lark_tools.execute_lark_cli(
        args_json=bad_json_string,
        tool_context="fake-ctx"
    )
    assert result3["status"] == "error"
    assert "must be a JSON array string" in result3["message"]


def test_concurrent_execution_isolation(monkeypatch):
    """
    高阶测试 1：验证多线程/多协程并发请求下，HOME 隔离临时目录 tmp_home 的绝对独立分配与执行完毕后的完全清理。
    """
    from lark_agent.infrastructure.cli_client import cli_client
    import concurrent.futures

    allocated_homes = set()
    run_records = []

    # Mock 底层的 _run 捕获被调用的 tmp_home
    def mock_run(args, env, tmp_home, timeout_seconds=120):
        allocated_homes.add(tmp_home)
        # 确保在这个时刻，临时的 tmp_home 目录在文件系统中确实被安全创建了
        assert os.path.isdir(tmp_home)
        run_records.append(tmp_home)
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(cli_client, "_run", mock_run)

    # 模拟 10 个线程并发执行 run_command
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(
                cli_client.run_command,
                args=["docs", "+fetch"],
                access_token=f"token-{i}",
                client_id="app-123"
            )
            for i in range(10)
        ]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # 1. 验证全部成功，且分配了 10 个绝对不同的隔离 tmp_home 目录
    assert len(results) == 10
    assert all(r["status"] == "success" for r in results)
    assert len(allocated_homes) == 10  # 没有任何冲突和交叉

    # 2. 验证多线程任务在 finally 块中，所有的临时隔离 HOME 目录已经全被物理清理干净
    for home in allocated_homes:
        assert not os.path.exists(home)


def test_log_redaction_and_truncation():
    """
    高阶测试 2：验证 _format_args_for_log 方法对敏感命令行 Flag 的无泄漏脱敏以及超长参数安全截断。
    """
    from lark_agent.infrastructure.cli_client import cli_client

    # 1. 验证敏感 flags（如 --json, --body, --text, --params）的内容被重写为 <redacted>
    sensitive_args = ["lark-cli", "im", "message", "send", "--text", "my_secret_token_abc_123"]
    log_output = cli_client._format_args_for_log(sensitive_args)
    assert "my_secret_token_abc_123" not in log_output
    assert "--text <redacted>" in log_output

    # 2. 验证多敏感 flag 并存脱敏
    multi_sensitive = ["gws", "docs", "create", "--title", "safe-title", "--body", "secret_body", "--params", "secret_params"]
    log_output2 = cli_client._format_args_for_log(multi_sensitive)
    assert "secret_body" not in log_output2
    assert "secret_params" not in log_output2
    assert "--body <redacted>" in log_output2
    assert "--params <redacted>" in log_output2
    assert "safe-title" in log_output2  # 正常不敏感的保留

    # 3. 验证长参数（>120 字符）的安全截断缩略
    long_arg = "x" * 200
    log_output3 = cli_client._format_args_for_log(["im", long_arg])
    assert len(log_output3.split()[-1]) == 120
    assert "..." in log_output3


def test_cli_error_handling_and_non_zero_exit(monkeypatch):
    """
    高阶测试 3：验证底层 _run 执行器在遭遇进程非零退出状态码（如 returncode=1）或 stderr 抛错时的优雅容错，不发生 panic。
    """
    from lark_agent.infrastructure.cli_client import cli_client
    import subprocess

    class MockCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "Error: Invalid permission or unauthorized OAuth scope."

    def mock_subprocess_run(full_args, env=None, capture_output=True, text=True, check=False, timeout=120):
        return MockCompletedProcess()

    monkeypatch.setattr(os, "path", MagicMock(exists=lambda p: True))  # 让 bin_path 检查通过
    monkeypatch.setattr(os, "access", lambda p, mode: True)
    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # 运行并确保其被优雅包装为带有错误原因的统一字典，没有直接 raise CalledProcessError
    res = cli_client._run(
        args=["docs", "+fetch"],
        env={},
        tmp_home="/tmp/fake_home"
    )
    assert res["status"] == "error"
    assert res["exit_code"] == 1
    assert "Invalid permission" in res["message"]



def test_raw_plain_text_stdout_fallback():
    """
    高阶测试 4：验证 _parse_stdout 方法在面对非标准 JSON、多行杂乱纯文本、或带空字符的标准输出时的强韧抗震降级表现。
    """
    from lark_agent.infrastructure.cli_client import cli_client

    # 1. 验证常规纯文本
    raw_text = "Some raw description text from command output."
    parsed1 = cli_client._parse_stdout(raw_text)
    assert parsed1["status"] == "success"
    assert parsed1["content"] == raw_text
    assert "data" not in parsed1

    # 2. 验证畸形 JSON
    bad_json = '{"status": "ok", '  # 缺少右半
    parsed2 = cli_client._parse_stdout(bad_json)
    assert parsed2["status"] == "success"
    assert parsed2["content"] == bad_json.strip()


    # 3. 验证多行且部分可解析部分畸形的混合状态
    mixed_lines = '{"line": 1}\nThis is a raw line\n{"line": 3}'
    parsed3 = cli_client._parse_stdout(mixed_lines)
    assert parsed3["status"] == "success"
    assert parsed3["content"] == mixed_lines



