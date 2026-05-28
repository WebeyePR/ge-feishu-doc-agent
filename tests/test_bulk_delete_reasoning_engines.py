import argparse
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "bulk_delete_reasoning_engines.py"
)
SPEC = importlib.util.spec_from_file_location(
    "bulk_delete_reasoning_engines", SCRIPT_PATH
)
bulk_delete = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = bulk_delete
SPEC.loader.exec_module(bulk_delete)


def make_args(**overrides):
    defaults = {
        "project_id": "test-project",
        "location": "us-central1",
        "ge_location": "global",
        "ge_app_id": "test-app",
        "prefix": None,
        "contains": None,
        "id_prefix": None,
        "exact_id": None,
        "exact_name": None,
        "allow_all": False,
        "execute": False,
        "confirm": None,
        "max_delete": 10,
        "min_age_hours": 24,
        "min_age_days": None,
        "include_recent": False,
        "protect_display_name": False,
        "skip_ge_check": False,
        "use_proxy": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def make_engine(engine_id="123", *, days_old=30, display_name=None):
    create_time = datetime.now(UTC) - timedelta(days=days_old)
    engine = {
        "name": f"projects/test-project/locations/us-central1/reasoningEngines/{engine_id}",
        "createTime": create_time.isoformat().replace("+00:00", "Z"),
    }
    if display_name:
        engine["displayName"] = display_name
    return engine


def test_validate_args_refuses_unfiltered_run():
    args = make_args()

    with pytest.raises(SystemExit, match="拒绝在没有过滤条件时运行"):
        bulk_delete.validate_args(args, require_filter=True)


def test_apply_filters_matches_exact_id_and_prefix():
    engines = [make_engine("abc123"), make_engine("abc999"), make_engine("def123")]

    exact = bulk_delete.apply_filters(
        engines,
        prefix=None,
        contains=None,
        id_prefix=None,
        exact_id="abc123",
        exact_name=None,
    )
    prefixed = bulk_delete.apply_filters(
        engines,
        prefix="abc",
        contains=None,
        id_prefix=None,
        exact_id=None,
        exact_name=None,
    )

    assert [bulk_delete.engine_id(engine["name"]) for engine in exact] == ["abc123"]
    assert [bulk_delete.engine_id(engine["name"]) for engine in prefixed] == [
        "abc123",
        "abc999",
    ]


def test_decide_engine_blocks_ge_referenced_engine_by_id_even_if_project_differs():
    engine = make_engine("123")
    protected = [
        bulk_delete.ProtectedEngine(
            source="Gemini Enterprise agent: WebEye Nexus",
            resource_name="projects/839062387451/locations/us-central1/reasoningEngines/123",
        )
    ]

    decision = bulk_delete.decide_engine(
        engine,
        protection_index=bulk_delete.build_protection_index(protected),
        min_age_hours=24,
        include_recent=False,
        protect_display_name=False,
    )

    assert not decision.can_delete
    assert decision.associations == ["Gemini Enterprise agent: WebEye Nexus"]
    assert any("Gemini Enterprise agent" in reason for reason in decision.blocked)


def test_decide_engine_blocks_recent_engines_by_hour_threshold():
    engine = make_engine("recent", days_old=0)

    decision = bulk_delete.decide_engine(
        engine,
        protection_index={},
        min_age_hours=24,
        include_recent=False,
        protect_display_name=False,
    )

    assert not decision.can_delete
    assert not decision.associations
    assert any("--include-recent" in reason for reason in decision.blocked)
    assert any("前" in reason for reason in decision.blocked)


def test_decide_engine_can_block_display_names_when_requested():
    engine = make_engine("named", display_name="production")

    decision = bulk_delete.decide_engine(
        engine,
        protection_index={},
        min_age_hours=24,
        include_recent=False,
        protect_display_name=True,
    )

    assert not decision.can_delete
    assert any("displayName" in reason for reason in decision.blocked)


def test_validate_args_converts_deprecated_min_age_days_to_hours():
    args = make_args(prefix="abc", min_age_days=2, min_age_hours=24)

    bulk_delete.validate_args(args, require_filter=True)

    assert args.min_age_hours == 48


def test_format_age_uses_precise_units():
    now = datetime(2026, 5, 26, 12, 30, tzinfo=UTC)
    created_at = now - timedelta(days=2, hours=3, minutes=4)

    assert bulk_delete.format_age(created_at, now) == "2d 03h 04m"
