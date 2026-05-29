#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lark CLI Command Index Builder
------------------------------
此脚本用于扫描并解析 bundled 的 lark-cli 技能文件（包括各个技能主目录下的 SKILL.md
以及 references 目录下的子引用 md 文档），智能提取支持的快捷命令和 API 示例，
并输出为高保真的本地命令注册表 lark_agent/lark_registry/commands.json。

本设计考虑了高内聚、低耦合、模块化，并为未来支持更多平台（如钉钉、企业微信等）
留出了底座设计和扩展接口。
"""

import argparse
import json
import logging
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# 项目根目录与默认路径
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_DIR = ROOT / "lib" / "lark-cli-v1.0.40" / "skills"
DEFAULT_OUTPUT = ROOT / "lark_agent" / "lark_registry" / "commands.json"

# 默认的变动型（写/修改）命令及快捷方法词表，用于安全审计识别
MUTATING_VERBS: Set[str] = {
    "create",
    "delete",
    "update",
    "patch",
    "post",
    "put",
    "send",
    "reply",
    "add",
    "remove",
    "set",
    "upload",
    "publish",
    "rsvp",
    "batch_delete",
    "batch_create",
    "clear",
    "cancel",
    "close",
    "open",
    "agree",
    "reject",
    "transfer",
}

class BaseCommandExtractor:
    """
    通用平台命令提取器基类 (为未来扩展多平台 CLI 提供标准接口)
    """
    def __init__(self, platform_name: str, cli_name: str):
        self.platform_name = platform_name
        self.cli_name = cli_name

    def extract_from_markdown(self, text: str, file_path: Path) -> Dict[str, Dict[str, Any]]:
        """从 Markdown 文本中提取结构化命令字典。子类须重构此方法。"""
        raise NotImplementedError


class LarkCommandExtractor(BaseCommandExtractor):
    """
    飞书/Lark CLI 专属命令提取器
    """
    def __init__(self):
        super().__init__(platform_name="Lark/Feishu", cli_name="lark-cli")

    def _extract_frontmatter_desc(self, text: str) -> str:
        """从 markdown frontmatter 中提取 description"""
        match = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", text, re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _clean_command_line(self, line: str) -> str:
        """清洗单行命令，处理多余的 shell 符号、管道、赋值等包装"""
        line = line.strip()
        # 移除前导的 $ 符号
        if line.startswith("$ "):
            line = line[2:]
        # 移除可能的管道和尾随的处理
        if " | " in line:
            line = line.split(" | ")[0].strip()
        # 移除可能的环境变量前缀或赋值包裹，例如 APP=$(lark-cli ...) -> lark-cli ...
        assign_match = re.match(r"^[A-Z_]+=\$\((lark-cli.*?)\)$", line)
        if assign_match:
            line = assign_match.group(1)
        return line.strip()

    def _iter_markdown_commands(self, text: str) -> Iterable[List[str]]:
        """
        解析并迭代表达式，同时支持：
        1. ```bash ... ``` 代码块中的命令
        2. 行内单反引号包裹的 `lark-cli ...` 命令
        """
        # 1. 扫描代码块
        for block in re.findall(r"```(?:bash|sh|shell)?\n(.*?)```", text, flags=re.DOTALL):
            # 处理反斜杠换行拼接
            normalized_block = block.replace("\\\n", " ").replace("\\\r\n", " ")
            for raw_line in normalized_block.splitlines():
                line = self._clean_command_line(raw_line)
                if not line.startswith("lark-cli "):
                    continue
                # 过滤帮助、自省、认证和配置命令
                if any(x in line for x in (" --help", " -h ", "schema", "auth ", "config ")):
                    continue
                try:
                    yield shlex.split(line)
                except ValueError:
                    continue

        # 2. 扫描行内单反引号
        for inline in re.findall(r"`(lark-cli\s+[^`]+)`", text):
            line = self._clean_command_line(inline)
            if any(x in line for x in (" --help", " -h ", "schema", "auth ", "config ")):
                continue
            try:
                yield shlex.split(line)
            except ValueError:
                continue

    def _get_command_prefix(self, argv: List[str]) -> List[str]:
        """
        提取服务名和路径前缀，例如 ["lark-cli", "calendar", "events", "create"] -> ["calendar", "events"]
        跳过以横杠开头的 flag 和可能包含占位符的参数
        """
        prefix = []
        for token in argv[1:]:
            if token.startswith("-") or token.startswith("<") or token.startswith("[") or "=" in token:
                break
            prefix.append(token)
        return prefix

    def _determine_command_id(self, prefix: List[str]) -> str:
        """
        拼装唯一的 command_id。
        如果是快捷命令（以 + 开头），拼装为 service.+verb，例如 "calendar.+agenda"
        """
        if len(prefix) >= 2 and prefix[1].startswith("+"):
            return ".".join(prefix[:2])
        return ".".join(prefix)

    def _is_mutating_command(self, command_id: str, text: str) -> bool:
        """
        利用启发式规则识别命令是否会产生数据变动（Write/Mutating）
        """
        # 提取最后一个分词，例如 "calendar.events.create" -> "create"
        parts = command_id.split(".")
        last_word = parts[-1].lstrip("+").lower() if parts else ""
        if last_word in MUTATING_VERBS:
            return True
        
        # 针对通用 api 调用（如 lark-cli api POST ...）进行判定
        if "api.post" in command_id or "api.put" in command_id or "api.delete" in command_id or "api.patch" in command_id:
            return True

        # 如果 markdown 中包含警告、高风险等标记
        lowered_text = text.lower()
        if "[!caution]" in lowered_text or "[!warning]" in lowered_text or "risky mutating" in lowered_text:
            return True

        return False

    def extract_from_markdown(self, text: str, file_path: Path) -> Dict[str, Dict[str, Any]]:
        commands: Dict[str, Dict[str, Any]] = {}
        file_desc = self._extract_frontmatter_desc(text)

        for argv in self._iter_markdown_commands(text):
            prefix = self._get_command_prefix(argv)
            if len(prefix) < 2:
                # 至少要有 service 和 method/shortcut，例如 lark-cli im +messages-send
                continue

            command_id = self._determine_command_id(prefix)
            if not command_id or "<" in command_id or "[" in command_id:
                continue

            is_write = self._is_mutating_command(command_id, text)
            service = prefix[0]

            # 提取所属技能名作为分类标签
            skill_name = file_path.parent.name
            if skill_name == "references":
                skill_name = file_path.parent.parent.name

            # 构造注册表单条 Spec
            entry = {
                "service": service,
                "skill": skill_name,
                "argv": prefix,
                "argv_template": argv[1:],
                "kind": "write" if is_write else "read",
                "description": file_desc or f"Lark {command_id} command integration",
                "input_notes": f"Auto-compiled from Lark {skill_name} skill. Validate args carefully.",
                "examples": [{"args": argv[1:]}],
                "requires_confirmation": is_write,
                "keywords": [service, skill_name, *command_id.split(".")],
            }

            # 如果已存在该命令，增量丰富其 examples，不直接覆盖
            if command_id in commands:
                existing = commands[command_id]
                # 检查是否已包含此示例，去重
                if argv[1:] not in [ex["args"] for ex in existing["examples"]]:
                    existing["examples"].append({"args": argv[1:]})
            else:
                commands[command_id] = entry

        return commands


def compile_registry(skills_dir: Path, output_file: Path, replace: bool = False) -> None:
    """
    扫描技能目录下的所有 .md 文件，使用提取器编译生成最终的注册表 json
    """
    logger.info("Initializing Lark CLI Command Registry compiling workflow...")
    extractor = LarkCommandExtractor()
    all_compiled: Dict[str, Dict[str, Any]] = {}

    # 1. 递归扫描 skillsDir 下的所有 Markdown 文件
    md_files = sorted(skills_dir.rglob("*.md"))
    logger.info("Found %d markdown documentation files to scan.", len(md_files))

    for md_path in md_files:
        try:
            text = md_path.read_text(encoding="utf-8")
            file_commands = extractor.extract_from_markdown(text, md_path)
            for cmd_id, spec in file_commands.items():
                if cmd_id in all_compiled:
                    # 增量合并 examples
                    for ex in spec["examples"]:
                        if ex["args"] not in [e["args"] for e in all_compiled[cmd_id]["examples"]]:
                            all_compiled[cmd_id]["examples"].append(ex)
                else:
                    all_compiled[cmd_id] = spec
        except Exception as e:
            logger.error("Failed to parse markdown file %s: %s", md_path, e)

    # 2. 丰富默认的元数据和长尾降级通用命令
    if "api" not in all_compiled:
        all_compiled["api"] = {
            "service": "api",
            "skill": "lark-openapi-explorer",
            "argv": ["api"],
            "argv_template": ["<METHOD>", "<PATH>"],
            "kind": "read",  # 默认，具体执行时动态判定
            "description": "Universal Lark Open API explorer method executor",
            "input_notes": "Use execute_lark_cli_flat for maximum robustness.",
            "examples": [
                {"args": ["GET", "/open-apis/calendar/v4/calendars"]},
                {"args": ["POST", "/open-apis/im/v1/messages", "--params", "{\"receive_id_type\":\"chat_id\"}", "--data", "{\"receive_id\":\"oc_xxx\",\"msg_type\":\"text\",\"content\":\"{\\\"text\\\":\\\"Hello\\\"}\"}"]}
            ],
            "requires_confirmation": False,
            "keywords": ["api", "universal", "lark-cli"]
        }

    # 3. 注入已有人工精修过的高频常用默认命令项（如果有），或读取已有 commands.json 融合
    if not replace and output_file.exists():
        try:
            existing_data = json.loads(output_file.read_text(encoding="utf-8"))
            for cmd_id, spec in existing_data.items():
                # 融合人工精修项
                if cmd_id not in all_compiled:
                    all_compiled[cmd_id] = spec
                else:
                    # 融合 description 和 keywords
                    all_compiled[cmd_id]["description"] = spec.get("description", all_compiled[cmd_id]["description"])
                    all_compiled[cmd_id]["input_notes"] = spec.get("input_notes", all_compiled[cmd_id]["input_notes"])
                    if "keywords" in spec:
                        all_compiled[cmd_id]["keywords"] = list(set(all_compiled[cmd_id]["keywords"] + spec["keywords"]))
        except Exception as e:
            logger.warning("Failed to load existing registry for merging: %s. Re-compiling fresh.", e)

    # 4. 写入输出文件
    output_file.parent.mkdir(parents=True, exist_ok=True)
    sorted_registry = dict(sorted(all_compiled.items()))
    output_file.write_text(
        json.dumps(sorted_registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    logger.info("Success! Wrote %d high-fidelity command specs to %s", len(sorted_registry), output_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile and build local Lark CLI command registry.")
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR, help="Path to bundled lark skills folder.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output commands.json file destination.")
    parser.add_argument("--replace", action="store_true", help="Completely replace existing file without merging.")
    args = parser.parse_args()

    compile_registry(args.skills_dir, args.output, replace=args.replace)
