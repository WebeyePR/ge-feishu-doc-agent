import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.generate_auth_link import build_auth_url, build_length_warning


def test_build_auth_url_keeps_scope_colons_and_encodes_spaces():
    url = build_auth_url(
        "https://accounts.feishu.cn/open-apis/authen/v1/authorize",
        {
            "client_id": "cli_test",
            "response_type": "code",
            "scope": "bitable:app docs:document:import docx:document",
        },
    )

    assert "scope=bitable:app%20docs:document:import%20docx:document" in url
    assert "%3A" not in url
    assert "+" not in url


def test_build_length_warning_warns_for_long_auth_url():
    warning = build_length_warning("https://example.com/?" + "x" * 4100, scope_count=189)

    assert "HTTP 431" in warning
    assert "189" in warning


def test_lark_scopes_file_is_reduced_to_current_oauth_surface():
    scopes_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "lark_scopes.json")
    with open(scopes_path, encoding="utf-8") as f:
        scopes_data = json.load(f)

    user_scopes = scopes_data["scopes"]["user"]
    tenant_scopes = scopes_data["scopes"]["tenant"]

    assert tenant_scopes == []
    # 采用不小于阈值的防御性测试，目前飞书具有 27 个 scopes，防止未来范围继续扩大造成断言崩溃
    assert len(user_scopes) >= 20
    assert "docs:document.content:read" in user_scopes
    assert "docx:document" in user_scopes
    assert "drive:drive" in user_scopes
    assert "search:docs:read" in user_scopes
    assert "im:message" in user_scopes
