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
