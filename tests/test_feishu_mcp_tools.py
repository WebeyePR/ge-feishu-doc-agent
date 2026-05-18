import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lark_agent.infrastructure.lark_api_repository import _extract_mcp_result, _normalize_mcp_tool_result
from lark_agent import tools as lark_tools


def test_extract_mcp_result_parses_single_json_text_content():
    raw = {
        "content": [
            {
                "type": "text",
                "text": '{"doc_id":"doxcn123","doc_url":"https://www.feishu.cn/docx/doxcn123","message":"ok"}',
            }
        ]
    }

    result = _extract_mcp_result(raw)

    assert result["doc_id"] == "doxcn123"
    assert result["doc_url"].endswith("/doxcn123")
    assert result["message"] == "ok"


def test_extract_mcp_result_joins_multi_text_content():
    raw = {
        "content": [
            {"type": "text", "text": "line1"},
            {"type": "text", "text": "line2"},
        ]
    }

    assert _extract_mcp_result(raw) == "line1\nline2"


def test_normalize_mcp_tool_result_wraps_plain_text_message():
    raw = {"content": [{"type": "text", "text": "document created"}]}

    assert _normalize_mcp_tool_result(raw) == {"message": "document created"}


def test_normalize_mcp_tool_result_keeps_structured_payload():
    raw = {
        "content": [
            {
                "type": "text",
                "text": '{"success":true,"doc_id":"doxcn123","message":"created"}',
            }
        ]
    }

    result = _normalize_mcp_tool_result(raw)

    assert result["success"] is True
    assert result["doc_id"] == "doxcn123"
    assert result["message"] == "created"


def test_normalize_markdown_for_new_doc_removes_duplicate_leading_h1():
    markdown = "# 项目总结\n\n第一段\n\n## 细节\n内容"

    result = lark_tools._normalize_markdown_for_new_doc("项目总结", markdown)

    assert result == "第一段\n\n## 细节\n内容"


def test_save_ai_output_to_feishu_doc_returns_clean_summary(monkeypatch):
    def fake_create_doc(**kwargs):
        return {
            "status": "success",
            "doc_id": "doxcn123",
            "doc_url": "https://www.feishu.cn/docx/doxcn123",
            "message": "文档创建成功",
        }

    monkeypatch.setattr(lark_tools, "feishu_mcp_create_doc", fake_create_doc)

    result = lark_tools.save_ai_output_to_feishu_doc(
        title="周报",
        markdown_content="## 本周进展\n- 已完成",
        tool_context="fake-token",
    )

    assert result["status"] == "success"
    assert result["doc_id"] == "doxcn123"
    assert result["doc_url"].endswith("/doxcn123")
    assert result["summary"] == "Created document '周报' at https://www.feishu.cn/docx/doxcn123"


def test_save_ai_output_to_existing_feishu_doc_returns_clean_summary(monkeypatch):
    def fake_update_doc(**kwargs):
        return {
            "status": "success",
            "doc_id": "doxcn123",
            "mode": "append",
            "message": "文档更新成功",
        }

    monkeypatch.setattr(lark_tools, "feishu_mcp_update_doc", fake_update_doc)

    result = lark_tools.save_ai_output_to_existing_feishu_doc(
        doc_id="doxcn123",
        markdown_content="## 新内容\n- 已完成",
        tool_context="fake-token",
    )

    assert result["status"] == "success"
    assert result["doc_id"] == "doxcn123"
    assert result["mode"] == "append"
    assert result["summary"] == "Updated document 'doxcn123' with mode 'append'"


def test_save_ai_output_to_existing_feishu_doc_requires_doc_id():
    result = lark_tools.save_ai_output_to_existing_feishu_doc(
        doc_id="",
        markdown_content="content",
        tool_context="fake-token",
    )

    assert result["status"] == "error"
    assert result["message"] == "doc_id is required."


def test_wait_for_feishu_doc_create_task_completes_after_second_poll(monkeypatch):
    calls = {"count": 0}

    def fake_create_doc(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "status": "success",
                "task_id": "task_123",
                "message": "still running",
            }
        return {
            "status": "success",
            "doc_id": "doxcn123",
            "doc_url": "https://www.feishu.cn/docx/doxcn123",
            "message": "done",
        }

    monkeypatch.setattr(lark_tools, "feishu_mcp_create_doc", fake_create_doc)

    result = lark_tools.wait_for_feishu_doc_create_task(
        task_id="task_123",
        tool_context="fake-token",
        max_polls=3,
        poll_interval_seconds=0,
    )

    assert result["status"] == "success"
    assert result["completed"] is True
    assert result["poll_attempts"] == 2
    assert result["doc_id"] == "doxcn123"


def test_wait_for_feishu_doc_update_task_returns_running_after_timeout(monkeypatch):
    def fake_update_doc(**kwargs):
        return {
            "status": "success",
            "task_id": "task_456",
            "message": "still running",
        }

    monkeypatch.setattr(lark_tools, "feishu_mcp_update_doc", fake_update_doc)

    result = lark_tools.wait_for_feishu_doc_update_task(
        task_id="task_456",
        tool_context="fake-token",
        max_polls=2,
        poll_interval_seconds=0,
    )

    assert result["status"] == "success"
    assert result["completed"] is False
    assert result["poll_attempts"] == 2
    assert result["task_id"] == "task_456"
