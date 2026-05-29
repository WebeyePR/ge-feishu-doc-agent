import os
import sys
from types import SimpleNamespace

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import google.adk.flows.llm_flows.functions as fn_module
from google.adk.events.event_actions import EventActions

from nexus_agent.callbacks import MULTIMODAL_PARTS_KEY, patch_adk_for_multimodal


def test_multimodal_patch_delegates_plain_tool_results(monkeypatch):
    sentinel = object()
    calls = []

    def fake_original(tool, function_result, tool_context, invocation_context):
        calls.append(function_result)
        return sentinel

    monkeypatch.setattr(fn_module, "_multimodal_patched", False, raising=False)
    monkeypatch.setattr(fn_module, "__build_response_event", fake_original)

    patch_adk_for_multimodal()
    patched = getattr(fn_module, "__build_response_event")
    result = patched(
        SimpleNamespace(name="get_google_workspace_operation_schema"),
        {"status": "success", "response": {"$ref": "File"}},
        SimpleNamespace(function_call_id="call-1"),
        SimpleNamespace(invocation_id="inv-1", agent=SimpleNamespace(name="agent"), branch=None),
    )

    assert result is sentinel
    assert calls == [{"status": "success", "response": {"$ref": "File"}}]


def test_multimodal_patch_handles_marked_results(monkeypatch):
    def fake_original(tool, function_result, tool_context, invocation_context):
        raise AssertionError("multimodal results should not use the original builder")

    monkeypatch.setattr(fn_module, "_multimodal_patched", False, raising=False)
    monkeypatch.setattr(fn_module, "__build_response_event", fake_original)

    patch_adk_for_multimodal()
    patched = getattr(fn_module, "__build_response_event")
    result = patched(
        SimpleNamespace(name="get_lark_document_rich_content"),
        {
            "status": "success",
            MULTIMODAL_PARTS_KEY: [
                {
                    "mime_type": "image/png",
                    "data": "iVBORw0KGgo=",
                }
            ],
        },
        SimpleNamespace(function_call_id="call-1", actions=EventActions()),
        SimpleNamespace(invocation_id="inv-1", agent=SimpleNamespace(name="agent"), branch=None),
    )

    function_response = result.content.parts[0].function_response
    assert function_response.name == "get_lark_document_rich_content"
    assert function_response.parts
    assert MULTIMODAL_PARTS_KEY not in function_response.response
