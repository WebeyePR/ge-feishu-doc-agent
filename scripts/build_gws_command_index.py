#!/usr/bin/env python3
import argparse
import json
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_DIR = ROOT / "lib" / "gws-cli-v0.22.5" / "skills"
DEFAULT_OUTPUT = ROOT / "lark_agent" / "gws_registry" / "commands.json"

MUTATING_METHODS = {
    "append",
    "batchclear",
    "batchdelete",
    "batchupdate",
    "clear",
    "copy",
    "create",
    "delete",
    "emptytrash",
    "forward",
    "import",
    "insert",
    "modify",
    "move",
    "patch",
    "reply",
    "reply-all",
    "remove",
    "send",
    "set",
    "stop",
    "trash",
    "undelete",
    "untrash",
    "update",
    "upload",
    "watch",
    "write",
}


def _extract_frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*[\"']?(.+?)[\"']?\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _iter_bash_commands(text: str) -> Iterable[List[str]]:
    for block in re.findall(r"```(?:bash|sh)?\n(.*?)```", text, flags=re.DOTALL):
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line.startswith("gws "):
                continue
            if " --help" in line or line.startswith("gws schema ") or "generate-skills" in line:
                continue
            try:
                yield shlex.split(line)
            except ValueError:
                continue


def _command_prefix(argv: List[str]) -> List[str]:
    prefix = []
    for token in argv[1:]:
        if token.startswith("-") or token.startswith("<") or token.startswith("["):
            break
        prefix.append(token)
    return prefix


def _command_id(prefix: List[str]) -> str:
    if len(prefix) >= 2 and prefix[1].startswith("+"):
        return ".".join(prefix[:2])
    return ".".join(prefix)


def _is_mutating(command_id: str, skill_text: str) -> bool:
    last = command_id.rsplit(".", 1)[-1].lstrip("+").lower()
    if last in MUTATING_METHODS:
        return True
    lowered = skill_text.lower()
    return "[!caution]" in lowered or "write command" in lowered


def _build_entry(command_id: str, prefix: List[str], argv: List[str], text: str) -> Dict[str, Any]:
    description = _extract_frontmatter_value(text, "description") or command_id
    kind = "write" if _is_mutating(command_id, text) else "read"
    service = prefix[0]
    return {
        "service": service,
        "argv": prefix,
        "argv_template": argv[1:],
        "kind": kind,
        "description": description,
        "input_notes": "Generated from gws SKILL.md. Confirm exact parameters with get_google_workspace_command_spec or gws schema when needed.",
        "examples": [{"args": argv[1:]}],
        "requires_confirmation": kind != "read",
        "keywords": [service, *command_id.split("."), description],
    }


def discover_from_skills(skills_dir: Path) -> Dict[str, Dict[str, Any]]:
    commands: Dict[str, Dict[str, Any]] = {}
    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_path.read_text(encoding="utf-8")
        for argv in _iter_bash_commands(text):
            prefix = _command_prefix(argv)
            if len(prefix) < 2:
                continue
            command_id = _command_id(prefix)
            if not command_id or "<" in command_id or "[" in command_id:
                continue
            commands.setdefault(command_id, _build_entry(command_id, prefix, argv, text))
    return commands


def load_existing(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the local gws command registry from bundled gws skills.")
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace the current registry instead of preserving curated existing entries.",
    )
    args = parser.parse_args()

    discovered = discover_from_skills(args.skills_dir)
    registry = discovered if args.replace else {**discovered, **load_existing(args.output)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dict(sorted(registry.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(registry)} gws command specs to {args.output}")


if __name__ == "__main__":
    main()
