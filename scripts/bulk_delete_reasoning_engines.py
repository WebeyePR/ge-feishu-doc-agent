#!/usr/bin/env python3
"""
安全检查并批量删除 Vertex AI Reasoning Engine。

默认行为保持保守：
- 不带参数运行时进入逐项交互删除模式
- 未传入 --execute 时只做 dry-run
- 未传入 --allow-all 时必须提供明确过滤条件
- 阻止删除 Gemini Enterprise agent 正在引用的 engine
- 阻止删除 .deploy_env / VERTEX_REASONING_ENGINE_NAME 记录的当前 engine
- 默认阻止删除最近创建的 engine
- 真实删除前必须在交互式终端输入确认短语
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_LOCATION = "us-central1"
DEFAULT_GE_LOCATION = "global"
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_DELETE = 10
DEFAULT_MIN_AGE_HOURS = 24
ENGINE_NAME_RE = re.compile(
    r"^projects/[^/]+/locations/[^/]+/reasoningEngines/[^/]+$"
)


class ChineseHelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    def _get_help_string(self, action: argparse.Action) -> str:
        help_text = action.help or ""
        if "%(default)" not in help_text:
            defaulting_nargs = [argparse.OPTIONAL, argparse.ZERO_OR_MORE]
            if action.default is not argparse.SUPPRESS and (
                action.option_strings or action.nargs in defaulting_nargs
            ):
                help_text += " (默认: %(default)s)"
        return help_text


class ChineseArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        help_text = super().format_help()
        return (
            help_text.replace("usage:", "用法:")
            .replace("options:", "选项:")
            .replace("positional arguments:", "位置参数:")
        )

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法:")

    def error(self, message: str) -> None:
        message = message.replace("unrecognized arguments:", "无法识别的参数:")
        message = message.replace("expected one argument", "需要一个参数值")
        message = message.replace("invalid choice:", "无效选项:")
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 错误: {message}\n")


@dataclass(frozen=True)
class ProtectedEngine:
    source: str
    resource_name: str


@dataclass
class EngineDecision:
    engine: dict[str, Any]
    associations: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_delete(self) -> bool:
        return not self.blocked


@dataclass
class DeleteStats:
    total: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0


def load_env_file(path: Path) -> None:
    """加载简单的 KEY=VALUE env 文件，不覆盖已有环境变量。"""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def load_project_env() -> None:
    load_env_file(ROOT_DIR / ".env")
    load_env_file(ROOT_DIR / ".deploy_env")


def default_reasoning_engine_location() -> str:
    """优先使用区域级 Reasoning Engine 位置，避免误用 GE 的 global。"""
    for env_name in (
        "RE_LOCATION",
        "DEPLOY_LOCATION",
        "VERTEX_LOCATION",
        "VERTEX_AI_LOCATION",
        "GOOGLE_CLOUD_REGION",
        "GOOGLE_CLOUD_LOCATION",
        "LOCATION",
    ):
        value = os.getenv(env_name, "").strip()
        if value and value != "global":
            return value
    return DEFAULT_LOCATION


def parse_google_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def engine_id(resource_name: str) -> str:
    return resource_name.rsplit("/", 1)[-1]


def engine_display_name(engine: dict[str, Any]) -> str:
    return engine.get("displayName") or engine_id(engine.get("name", ""))


def normalize_filter_value(value: str) -> str:
    return value.casefold()


def engine_location(resource_name: str) -> str | None:
    parts = resource_name.split("/")
    if len(parts) >= 4 and parts[2] == "locations":
        return parts[3]
    return None


def normalize_engine_refs(resource_name: str) -> set[str]:
    """返回可比较的引用形式，兼容项目 ID 和项目编号变体。"""
    refs = {resource_name, engine_id(resource_name)}
    location = engine_location(resource_name)
    if location:
        refs.add(f"{location}/{engine_id(resource_name)}")
    return refs


def get_gcloud_token() -> str:
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise SystemExit("未找到 gcloud，请先安装或确认 PATH 配置。")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        raise SystemExit(
            f"获取 gcloud access token 失败，请先运行 `gcloud auth login`。\n{stderr}"
        )

    token = result.stdout.strip()
    if not token:
        raise SystemExit("gcloud 返回了空的 access token。")
    return token


def create_session(use_proxy: bool) -> requests.Session:
    session = requests.Session()
    session.trust_env = use_proxy

    retry_strategy = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "DELETE"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    return session


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    token: str,
    project_id: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = session.request(
        method,
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": project_id,
        },
        params=params,
        timeout=60,
    )
    if response.status_code >= 400:
        body = response.text.strip()
        raise RuntimeError(f"{method} {url} 请求失败: HTTP {response.status_code}\n{body}")
    if not response.text.strip():
        return {}
    return response.json()


def list_reasoning_engines(
    session: requests.Session,
    project_id: str,
    location: str,
    token: str,
) -> list[dict[str, Any]]:
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/"
        f"projects/{project_id}/locations/{location}/reasoningEngines"
    )
    engines: list[dict[str, Any]] = []
    page_token = ""

    while True:
        params = {"pageSize": DEFAULT_PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token
        data = request_json(session, "GET", url, token, project_id, params=params)
        engines.extend(data.get("reasoningEngines", []))
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break

    return sorted(
        engines,
        key=lambda item: parse_google_datetime(item.get("createTime")) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )


def list_ge_referenced_engines(
    session: requests.Session,
    project_id: str,
    ge_location: str,
    ge_app_id: str,
    token: str,
) -> list[ProtectedEngine]:
    url = (
        f"https://{ge_location}-discoveryengine.googleapis.com/v1alpha/"
        f"projects/{project_id}/locations/{ge_location}/collections/default_collection/"
        f"engines/{ge_app_id}/assistants/default_assistant/agents"
    )
    protected: list[ProtectedEngine] = []
    page_token = ""

    while True:
        params: dict[str, Any] = {"pageSize": DEFAULT_PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token
        data = request_json(session, "GET", url, token, project_id, params=params)
        for agent in data.get("agents", []):
            re_name = (
                agent.get("adkAgentDefinition", {})
                .get("provisionedReasoningEngine", {})
                .get("reasoningEngine")
            )
            if not re_name:
                continue
            display_name = agent.get("displayName") or agent.get("name", "未知 agent")
            protected.append(
                ProtectedEngine(
                    source=f"Gemini Enterprise agent 引用: {display_name}",
                    resource_name=re_name,
                )
            )
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break

    return protected


def apply_filters(
    engines: list[dict[str, Any]],
    *,
    prefix: str | None,
    contains: str | None,
    id_prefix: str | None,
    exact_id: str | None,
    exact_name: str | None,
) -> list[dict[str, Any]]:
    filtered = engines
    if exact_name:
        filtered = [engine for engine in filtered if engine.get("name") == exact_name]
    if exact_id:
        filtered = [engine for engine in filtered if engine_id(engine.get("name", "")) == exact_id]
    if prefix:
        normalized_prefix = normalize_filter_value(prefix)
        filtered = [
            engine
            for engine in filtered
            if normalize_filter_value(engine_display_name(engine)).startswith(normalized_prefix)
        ]
    if contains:
        normalized_contains = normalize_filter_value(contains)
        filtered = [
            engine
            for engine in filtered
            if normalized_contains in normalize_filter_value(engine_display_name(engine))
        ]
    if id_prefix:
        normalized_id_prefix = normalize_filter_value(id_prefix)
        filtered = [
            engine
            for engine in filtered
            if normalize_filter_value(engine_id(engine.get("name", ""))).startswith(normalized_id_prefix)
        ]
    return filtered


def build_protection_index(protected: list[ProtectedEngine]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for item in protected:
        for ref in normalize_engine_refs(item.resource_name):
            index.setdefault(ref, []).append(item.source)
    return index


def decide_engine(
    engine: dict[str, Any],
    *,
    protection_index: dict[str, list[str]],
    min_age_hours: int,
    include_recent: bool,
    protect_display_name: bool,
) -> EngineDecision:
    decision = EngineDecision(engine=engine)
    name = engine.get("name", "")
    refs = normalize_engine_refs(name)

    associated_sources: set[str] = set()
    for ref in refs:
        for source in protection_index.get(ref, []):
            associated_sources.add(source)
    for source in sorted(associated_sources):
        decision.associations.append(source)
        decision.blocked.append(f"被 {source} 引用")

    create_time = parse_google_datetime(engine.get("createTime"))
    if create_time:
        age_seconds = max(0, int((datetime.now(UTC) - create_time).total_seconds()))
        age_hours = age_seconds / 3600
        age_text = format_age(create_time)
        if age_hours < min_age_hours and not include_recent:
            decision.blocked.append(
                f"创建于 {age_text} 前；如需允许删除需传入 --include-recent"
            )
        elif age_hours < min_age_hours:
            decision.warnings.append(f"最近创建：已有 {age_text}")
    else:
        decision.blocked.append("缺少 createTime")

    display_name = engine.get("displayName")
    if display_name:
        message = f"已设置 displayName：{display_name}"
        if protect_display_name:
            decision.blocked.append(message)
        else:
            decision.warnings.append(message)

    update_time = parse_google_datetime(engine.get("updateTime"))
    if create_time and update_time and (update_time - create_time).total_seconds() > 300:
        decision.warnings.append("创建后发生过更新")

    return decision


def delete_reasoning_engine(
    session: requests.Session,
    project_id: str,
    token: str,
    resource_name: str,
) -> tuple[bool, str]:
    location = engine_location(resource_name)
    if not location:
        return False, "无效的 Reasoning Engine 资源名"

    url = f"https://{location}-aiplatform.googleapis.com/v1/{resource_name}?force=true"
    response = session.delete(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": project_id,
        },
        timeout=60,
    )
    if response.status_code in (200, 204):
        return True, "已提交删除请求"
    if response.status_code == 404:
        return True, "资源已不存在"
    return False, f"HTTP {response.status_code}: {response.text.strip()}"


def format_time(value: str | None) -> str:
    parsed = parse_google_datetime(value)
    if not parsed:
        return "未知"
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_age(created_at: datetime | None, now: datetime | None = None) -> str:
    if not created_at:
        return "未知"
    now = now or datetime.now(UTC)
    total_seconds = max(0, int((now - created_at).total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def print_decisions(decisions: list[EngineDecision]) -> None:
    for index, decision in enumerate(decisions, 1):
        print_decision(decision, index)


def print_decision(decision: EngineDecision, index: int | None = None) -> None:
    engine = decision.engine
    status = "可删除候选" if decision.can_delete else "已阻止"
    prefix = f"[{index}] " if index is not None else ""
    print(f"\n{prefix}{status} {engine_id(engine.get('name', ''))}")
    print(f"    资源名: {engine.get('name', '')}")
    created_at = parse_google_datetime(engine.get("createTime"))
    print(f"    创建时间: {format_time(engine.get('createTime'))}")
    print(f"    已创建: {format_age(created_at)}")
    if decision.associations:
        print("    引用状态: 已关联")
        for source in sorted(set(decision.associations)):
            print(f"      - {source}")
    else:
        print("    引用状态: 已加载检查中未发现引用")
    if engine.get("updateTime"):
        print(f"    更新时间: {format_time(engine.get('updateTime'))}")
    if engine.get("displayName"):
        print(f"    displayName: {engine['displayName']}")
    for reason in decision.blocked:
        print(f"    阻止原因: {reason}")
    for warning in decision.warnings:
        print(f"    警告: {warning}")


def sort_for_interactive(decisions: list[EngineDecision]) -> list[EngineDecision]:
    def sort_key(decision: EngineDecision) -> tuple[int, datetime]:
        created_at = parse_google_datetime(decision.engine.get("createTime"))
        return (
            0 if decision.can_delete else 1,
            created_at or datetime.max.replace(tzinfo=UTC),
        )

    return sorted(decisions, key=sort_key)


def prompt_interactive_filters() -> dict[str, str | None] | None:
    print("\n请选择过滤方式：")
    print("  1 - 显示名称前缀")
    print("  2 - 显示名称关键词")
    print("  3 - 资源 ID 前缀")
    print("  4 - 全部 Engine")
    print("  q - 退出")

    while True:
        choice = input("过滤方式 [1/2/3/4/q，默认 1]: ").strip().lower() or "1"
        if choice == "q":
            return None
        if choice in {"1", "2", "3"}:
            value = input("请输入过滤文本: ").strip()
            if not value:
                print("过滤文本不能为空。")
                continue
            return {
                "prefix": value if choice == "1" else None,
                "contains": value if choice == "2" else None,
                "id_prefix": value if choice == "3" else None,
                "exact_id": None,
                "exact_name": None,
            }
        if choice == "4":
            confirm_all = input("确认查询全部 Engine？输入 ALL 继续: ").strip()
            if confirm_all == "ALL":
                return {
                    "prefix": None,
                    "contains": None,
                    "id_prefix": None,
                    "exact_id": None,
                    "exact_name": None,
                }
            print("未确认查询全部，请重新选择。")
            continue
        print("无效选择，请重新输入。")


def print_stats(stats: DeleteStats) -> None:
    print("\n" + "=" * 80)
    print("操作统计")
    print(f"总数: {stats.total}")
    print(f"删除数: {stats.deleted}")
    print(f"跳过数: {stats.skipped}")
    print(f"失败数: {stats.failed}")
    print("=" * 80)


def run_interactive_delete(
    session: requests.Session,
    args: argparse.Namespace,
    token: str,
    decisions: list[EngineDecision],
) -> int:
    ordered_decisions = sort_for_interactive(decisions)
    stats = DeleteStats(total=len(ordered_decisions))

    print("\n交互删除将按“可删除且最旧优先”的顺序逐项处理。")
    print("每一项默认跳过；只有输入 y 才会删除。输入 q 可提前退出。")

    for index, decision in enumerate(ordered_decisions, 1):
        print_decision(decision, index)
        if not decision.can_delete:
            stats.skipped += 1
            print("    此项命中保护或限制，自动跳过。")
            continue

        action = input("操作？[y=删除 / Enter=跳过 / q=退出]: ").strip().lower()
        if action == "q":
            remaining = len(ordered_decisions) - index + 1
            stats.skipped += remaining
            print(f"已退出，剩余 {remaining} 项计入跳过。")
            break
        if action != "y":
            stats.skipped += 1
            print("已跳过。")
            continue

        name = decision.engine["name"]
        ok, message = delete_reasoning_engine(session, args.project_id, token, name)
        if ok:
            stats.deleted += 1
            print(f"已删除 {engine_id(name)}: {message}")
        else:
            stats.failed += 1
            print(f"删除失败 {engine_id(name)}: {message}")

    print_stats(stats)
    return 1 if stats.failed else 0


def parse_args() -> argparse.Namespace:
    parser = ChineseArgumentParser(
        description="安全检查并批量删除 Vertex AI Reasoning Engine；不带参数运行会进入逐项交互模式。",
        formatter_class=ChineseHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示帮助信息并退出。")
    parser.add_argument("--project-id", default=os.getenv("PROJECT_ID"), help="Google Cloud 项目 ID。")
    parser.add_argument("--location", default=default_reasoning_engine_location(), help="Vertex AI Reasoning Engine 所在区域。")
    parser.add_argument("--ge-location", default=os.getenv("GE_APP_LOCATION", DEFAULT_GE_LOCATION), help="Gemini Enterprise 所在位置。")
    parser.add_argument("--ge-app-id", default=os.getenv("GE_APP_ID"), help="Gemini Enterprise 应用/引擎 ID，用于检查引用保护。")
    parser.add_argument("--prefix", help="仅包含显示名称使用此前缀的 Engine；无显示名称时回退匹配资源 ID。")
    parser.add_argument("--contains", help="仅包含显示名称中含有此文本的 Engine；无显示名称时回退匹配资源 ID。")
    parser.add_argument("--id-prefix", help="仅包含资源 ID 使用此前缀的 Engine。")
    parser.add_argument("--id", dest="exact_id", help="仅包含此精确 Engine ID。")
    parser.add_argument("--name", dest="exact_name", help="仅包含此完整 Reasoning Engine 资源名。")
    parser.add_argument(
        "--allow-all",
        action="store_true",
        help="允许在没有 --prefix、--contains、--id-prefix、--id 或 --name 过滤条件时列出/删除。",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真实删除符合条件的 Engine；未传入时只执行 dry-run。",
    )
    parser.add_argument(
        "--max-delete",
        type=int,
        default=DEFAULT_MAX_DELETE,
        help="单次执行最多允许删除的 Engine 数量。",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help="阻止删除创建时间小于该小时数的 Engine，除非同时传入 --include-recent。",
    )
    parser.add_argument(
        "--min-age-days",
        type=float,
        help="--min-age-hours 的旧别名，会换算成小时。",
    )
    parser.add_argument(
        "--include-recent",
        action="store_true",
        help="允许删除比 --min-age-hours 更新的 Engine。",
    )
    parser.add_argument(
        "--protect-display-name",
        action="store_true",
        help="阻止删除已设置 displayName 的 Engine。",
    )
    parser.add_argument(
        "--skip-ge-check",
        action="store_true",
        help="跳过 Gemini Enterprise Agent 引用检查；执行删除时 GE 检查失败会中止，除非设置此参数。",
    )
    parser.add_argument(
        "--use-proxy",
        action="store_true",
        help="使用 HTTP(S)_PROXY 环境变量；默认忽略代理设置。",
    )
    return parser.parse_args()


def has_filter(args: argparse.Namespace) -> bool:
    return any([args.prefix, args.contains, args.id_prefix, args.exact_id, args.exact_name])


def validate_args(args: argparse.Namespace, *, require_filter: bool) -> None:
    if not args.project_id:
        raise SystemExit("缺少 --project-id，且 PROJECT_ID 环境变量未设置。")
    if args.location == "global":
        raise SystemExit(
            "Reasoning Engine 位置必须是区域级位置，不能是 global。"
            "Vertex AI Reasoning Engine 请使用 --location us-central1；"
            "--ge-location 才是 Gemini Enterprise 的位置。"
        )
    if args.exact_name and not ENGINE_NAME_RE.match(args.exact_name):
        raise SystemExit(
            "--name 必须是完整资源名，例如 "
            "projects/PROJECT/locations/us-central1/reasoningEngines/ENGINE_ID"
        )
    if require_filter and not has_filter(args) and not args.allow_all:
        raise SystemExit(
            "拒绝在没有过滤条件时运行。请传入 --prefix、--contains、--id-prefix、--id、--name，"
            "或明确传入 --allow-all。"
        )
    if args.max_delete < 1:
        raise SystemExit("--max-delete 必须至少为 1。")
    if args.min_age_days is not None:
        if args.min_age_days < 0:
            raise SystemExit("--min-age-days 不能为负数。")
        args.min_age_hours = args.min_age_days * 24
    if args.min_age_hours < 0:
        raise SystemExit("--min-age-hours 不能为负数。")


def get_protected_engines(
    session: requests.Session,
    args: argparse.Namespace,
    token: str,
) -> list[ProtectedEngine]:
    protected: list[ProtectedEngine] = []
    current_engine = os.getenv("VERTEX_REASONING_ENGINE_NAME", "").strip()
    if current_engine:
        protected.append(
            ProtectedEngine(
                source="本地 .deploy_env VERTEX_REASONING_ENGINE_NAME",
                resource_name=current_engine,
            )
        )

    if args.skip_ge_check:
        return protected

    if not args.ge_app_id:
        message = "未设置 GE_APP_ID，无法校验 Gemini Enterprise Agent 引用。"
        if args.execute:
            raise SystemExit(f"{message} 仅在接受该风险时才使用 --skip-ge-check。")
        print(f"警告: {message}")
        return protected

    try:
        protected.extend(
            list_ge_referenced_engines(
                session,
                args.project_id,
                args.ge_location,
                args.ge_app_id,
                token,
            )
        )
    except Exception as exc:
        if args.execute:
            raise SystemExit(
                f"检查 Gemini Enterprise Agent 引用失败: {exc}\n"
                "删除已中止。仅在接受该风险时才使用 --skip-ge-check。"
            )
        print(f"警告: 检查 Gemini Enterprise Agent 引用失败: {exc}")

    return protected


def main() -> int:
    load_project_env()
    args = parse_args()
    interactive_mode = len(sys.argv) == 1
    validate_args(args, require_filter=not interactive_mode)

    if interactive_mode and not sys.stdin.isatty():
        raise SystemExit("交互模式必须在交互式终端中运行。")

    session = create_session(use_proxy=args.use_proxy)
    token = get_gcloud_token()

    print("=" * 80)
    print("安全批量清理 Vertex AI Reasoning Engine")
    print("=" * 80)
    print(f"项目: {args.project_id}")
    print(f"Reasoning Engine 区域: {args.location}")
    print(f"模式: {'交互删除' if interactive_mode else ('真实删除' if args.execute else 'dry-run')}")
    print(f"最近创建保护: {args.min_age_hours:g} 小时")

    interactive_filters: dict[str, str | None] | None = None
    if interactive_mode:
        args.execute = True
        interactive_filters = prompt_interactive_filters()
        if interactive_filters is None:
            print("已退出。")
            return 0

    protected = get_protected_engines(session, args, token)
    protection_index = build_protection_index(protected)
    if protected:
        print(f"已加载保护清单: {len(protected)} 条；命中保护清单的 Engine 会显示为“已阻止”。")

    engines = list_reasoning_engines(session, args.project_id, args.location, token)
    filter_values = interactive_filters or {
        "prefix": args.prefix,
        "contains": args.contains,
        "id_prefix": args.id_prefix,
        "exact_id": args.exact_id,
        "exact_name": args.exact_name,
    }
    filtered = apply_filters(
        engines,
        prefix=filter_values["prefix"],
        contains=filter_values["contains"],
        id_prefix=filter_values["id_prefix"],
        exact_id=filter_values["exact_id"],
        exact_name=filter_values["exact_name"],
    )

    decisions = [
        decide_engine(
            engine,
            protection_index=protection_index,
            min_age_hours=args.min_age_hours,
            include_recent=args.include_recent,
            protect_display_name=args.protect_display_name,
        )
        for engine in filtered
    ]

    print(f"匹配到的 Engine: {len(decisions)}")

    candidates = [decision for decision in decisions if decision.can_delete]
    blocked_count = len(decisions) - len(candidates)
    if interactive_mode:
        print(f"可删除候选: {len(candidates)}")
        print(f"已阻止: {blocked_count}")
        if not decisions:
            print_stats(DeleteStats(total=0))
            return 0
        start = input("是否开始逐项交互处理？[y/N]: ").strip().lower()
        if start != "y":
            print_stats(DeleteStats(total=len(decisions), skipped=len(decisions)))
            return 0
        return run_interactive_delete(session, args, token, decisions)

    print_decisions(decisions)

    print("\n" + "-" * 80)
    print(f"可删除候选: {len(candidates)}")
    print(f"已阻止: {blocked_count}")

    if not args.execute:
        print("当前仅 dry-run：如需删除符合条件的候选项，请传入 --execute。")
        return 0

    if not candidates:
        print("没有符合条件的可删除候选项。")
        return 0

    if len(candidates) > args.max_delete:
        raise SystemExit(
            f"拒绝删除 {len(candidates)} 个 Engine，因为 --max-delete 为 {args.max_delete}。"
        )

    confirmation = (
        f"确认删除 {args.project_id}/{args.location} 中的 {len(candidates)} 个 ENGINE"
    )
    print(f"\n请输入以下完整短语以继续:\n{confirmation}")
    if not sys.stdin.isatty():
        raise SystemExit("删除操作必须在交互式终端中确认。")
    provided_confirmation = input("> ").strip()
    if provided_confirmation != confirmation:
        raise SystemExit("确认短语不匹配，未删除任何资源。")

    deleted = 0
    failed = 0
    for decision in candidates:
        name = decision.engine["name"]
        ok, message = delete_reasoning_engine(session, args.project_id, token, name)
        if ok:
            deleted += 1
            print(f"已删除 {engine_id(name)}: {message}")
        else:
            failed += 1
            print(f"删除失败 {engine_id(name)}: {message}")

    print("\n" + "=" * 80)
    print(f"已删除: {deleted}")
    print(f"失败: {failed}")
    print(f"已阻止: {blocked_count}")
    print("=" * 80)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
