import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, List, Optional


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9+_.-]+", (value or "").lower())
        if token
    }


@lru_cache(maxsize=1)
def _load_registry() -> Dict[str, Dict[str, Any]]:
    text = resources.files(__package__).joinpath("commands.json").read_text(encoding="utf-8")
    raw_registry = json.loads(text)
    registry: Dict[str, Dict[str, Any]] = {}
    for command_id, spec in raw_registry.items():
        normalized = dict(spec)
        normalized["command_id"] = command_id
        normalized.setdefault("argv", [])
        normalized.setdefault("kind", "read")
        normalized.setdefault("description", "")
        normalized.setdefault("input_notes", "")
        normalized.setdefault("examples", [])
        normalized.setdefault("keywords", [])
        normalized["requires_confirmation"] = bool(
            normalized.get("requires_confirmation") or normalized["kind"] != "read"
        )
        registry[command_id] = normalized
    return registry


def _public_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    public = dict(spec)
    public["argv_template"] = public.pop("argv_template", public["argv"])
    return public


def get_command_spec(command_id: str) -> Optional[Dict[str, Any]]:
    if not command_id:
        return None
    spec = _load_registry().get(command_id.strip())
    if not spec:
        return None
    return _public_spec(spec)


def search_commands(
    query: str = "",
    service: str = "",
    intent: str = "",
    resource_parts: Optional[List[str]] = None,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    resource_parts = resource_parts or []
    query_tokens = _tokenize(" ".join([query, intent, " ".join(resource_parts)]))
    service_name = (service or "").strip().lower()
    scored = []

    for spec in _load_registry().values():
        score = 0
        searchable = " ".join(
            [
                spec["command_id"],
                spec.get("service", ""),
                " ".join(spec.get("argv", [])),
                spec.get("description", ""),
                spec.get("input_notes", ""),
                " ".join(spec.get("keywords", [])),
            ]
        )
        spec_tokens = _tokenize(searchable)

        if service_name:
            if spec.get("service") != service_name:
                continue
            score += 5

        if resource_parts:
            argv = spec.get("argv", [])
            if len(argv) < 1 + len(resource_parts):
                continue
            if argv[1 : 1 + len(resource_parts)] != resource_parts:
                continue
            score += 4

        if query_tokens:
            overlap = query_tokens.intersection(spec_tokens)
            if not overlap:
                continue
            score += len(overlap)
        elif not service_name and not resource_parts:
            continue

        scored.append((score, spec["command_id"], _public_spec(spec)))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [spec for _, _, spec in scored[:limit]]


def find_command_for_args(args: List[str]) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_len = 0
    for spec in _load_registry().values():
        argv = spec.get("argv", [])
        if len(argv) > best_len and args[: len(argv)] == argv:
            best = spec
            best_len = len(argv)
    if not best:
        return None
    return _public_spec(best)
