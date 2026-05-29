# -*- coding: utf-8 -*-
"""
Lark CLI 模块化命令注册表查询逻辑
--------------------------------
本模块实现对 commands.json 索引库的加载、分词、模糊匹配与规范化检索。
代码设计遵循高内聚、低耦合原则，整个检索与过滤算法均作为 CLICommandRegistry 的类方法实现，
支持后续多平台（如 DingTalk, WeCom）通过实例化直接拥有高复用性，避免了代码复制。
同时，我们在模块底部提供了 100% 兼容的模块级函数封装，保持向后兼容。
"""

import json
import re
from importlib import resources
from typing import Any, Dict, List, Optional, Set


class CLICommandRegistry:
    """
    通用 CLI 命令行注册表服务类。
    通过将数据加载、检索排序、逆向推导完全内聚在实例内部，
    支持后续多平台（Lark, GWS, 钉钉, 企微等）零冗余扩展和复用。
    """
    def __init__(self, package_name: str, config_name: str = "commands.json"):
        """
        初始化平台命令行注册表。
        
        Args:
            package_name: 该平台注册表所在的 Python 包名（例如 'nexus_agent.lark_registry'）。
            config_name: 索引配置文件的名称（默认为 'commands.json'）。
        """
        self.package_name = package_name
        self.config_name = config_name
        # 实例级的惰性数据缓存，避免多实例并发时的全局缓存交织
        self._registry_data: Optional[Dict[str, Dict[str, Any]]] = None

    def _load_registry(self) -> Dict[str, Dict[str, Any]]:
        """
        带实例内部缓存的注册表加载，保证极速访问。
        当首次访问时加载文件并将其常驻于内存中，避免重复 I/O。
        """
        if self._registry_data is not None:
            return self._registry_data

        try:
            # 兼容低版本 python，使用 resources.files 从模块包中动态读取 commands.json
            text = resources.files(self.package_name).joinpath(self.config_name).read_text(encoding="utf-8")
            raw_registry = json.loads(text)
        except Exception:
            # 极致容错降级：若读取失败或文件缺失，返回空注册表而不会导致系统崩溃
            self._registry_data = {}
            return self._registry_data

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
            # 自动计算是否需要二次安全确认：非只读(Mutation操作)一律视为需要二次审计
            normalized["requires_confirmation"] = bool(
                normalized.get("requires_confirmation") or normalized["kind"] != "read"
            )
            registry[command_id] = normalized

        self._registry_data = registry
        return self._registry_data

    def _tokenize(self, value: str) -> Set[str]:
        """
        通用检索分词器：过滤特殊字符并将英文分词归一化小写。
        同时，对于包含 '+'、'.'、'_'、'-' 的词，我们也将其纯净字母数字部分作为词素加入分词集合。
        这能极大地提升带有快捷前缀（如 +fetch）和层级点号（如 docs.+fetch）的命令检索召回率。
        """
        raw_tokens = {
            token
            for token in re.split(r"[^a-z0-9+_.-]+", (value or "").lower())
            if token
        }
        refined_tokens = set(raw_tokens)
        for token in raw_tokens:
            if any(char in token for char in ("+", ".", "_", "-")):
                parts = re.split(r"[+._-]+", token)
                for part in parts:
                    if part:
                        refined_tokens.add(part)
        return refined_tokens

    def _public_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        输出过滤：在不影响原有内部规范前提下，将原有的 argv 重命名或克隆为 argv_template，对 LLM 更友好
        """
        public = dict(spec)
        public["argv_template"] = public.pop("argv_template", public["argv"])
        return public

    def get_command_spec(self, command_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 command_id 精确查询单个命令的参数规范和示例
        
        Args:
            command_id: 命令的唯一 ID 标识符
        """
        if not command_id:
            return None
        spec = self._load_registry().get(command_id.strip())
        if not spec:
            return None
        return self._public_spec(spec)

    def search_commands(
        self,
        query: str = "",
        service: str = "",
        intent: str = "",
        resource_parts: Optional[List[str]] = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        多维度多层次模糊搜索与 NLP 分词检索。
        
        Args:
            query: 自然语言查询，如 "send chat message"
            service: 业务域过滤，如 "im"
            intent: 意图过滤，如 "write" 或 "read"
            resource_parts: 资源深度路径过滤，如 ["messages"]
            limit: 返回结果的最大数目
        """
        resource_parts = resource_parts or []
        # 合并多源检索上下文分词
        query_tokens = self._tokenize(" ".join([query, intent, " ".join(resource_parts)]))
        service_name = (service or "").strip().lower()
        scored = []

        for spec in self._load_registry().values():
            score = 0
            # 组装完整的可检索文本，让描述、输入说明和关键字也能参与模糊匹配
            searchable = " ".join(
                [
                    spec["command_id"],
                    spec.get("service", ""),
                    spec.get("skill", ""),
                    " ".join(spec.get("argv", [])),
                    spec.get("description", ""),
                    spec.get("input_notes", ""),
                    " ".join(spec.get("keywords", [])),
                ]
            )
            spec_tokens = self._tokenize(searchable)

            # 1. 业务服务域过滤与加分
            if service_name:
                if spec.get("service") != service_name:
                    continue
                score += 5

            # 2. 深度资源路径前缀对齐与加分
            if resource_parts:
                argv = spec.get("argv", [])
                # argv 的格式为 [service, resource_part1, ...]，因此跳过第 0 个 service 进行段校验
                if len(argv) < 1 + len(resource_parts):
                    continue
                if argv[1 : 1 + len(resource_parts)] != resource_parts:
                    continue
                score += 4

            # 3. 意图类型（读/写）对齐与匹配
            if intent:
                if spec.get("kind") == intent.strip().lower():
                    score += 2

            # 4. 自然语言分词重合度计算
            if query_tokens:
                overlap = query_tokens.intersection(spec_tokens)
                if not overlap:
                    continue
                score += len(overlap)
            elif not service_name and not resource_parts and not intent:
                # 若无任何查询约束条件，为防止返回无用全量数据，一律跳过
                continue

            scored.append((score, spec["command_id"], self._public_spec(spec)))

        # 按照匹配得分从高到低排序，得分相同时按命令 ID 字母顺序升序
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [spec for _, _, spec in scored[:limit]]

    def find_command_for_args(self, args: List[str]) -> Optional[Dict[str, Any]]:
        """
        逆向推导：根据当前物理执行的命令行参数，匹配其在注册表中最长对齐的规范 spec。
        主要用于在拦截通用 flat 执行或传统 CLI 执行时，自动判断 RequiresConfirmation 或 Mutation 操作。
        """
        best: Optional[Dict[str, Any]] = None
        best_len = 0
        for spec in self._load_registry().values():
            argv = spec.get("argv", [])
            # 寻找在 args 中前缀最长匹配的那个 spec
            if len(argv) > best_len and args[: len(argv)] == argv:
                best = spec
                best_len = len(argv)
        if not best:
            return None
        return self._public_spec(best)


# -----------------------------------------------------------------------------
# Lark 专属向后兼容模块级接口层（Wrapper）
# -----------------------------------------------------------------------------
# 创建 Lark 专属注册表服务单例，加载 lark_registry 目录下的 commands.json 
_lark_registry_service = CLICommandRegistry(__package__)


def get_command_spec(command_id: str) -> Optional[Dict[str, Any]]:
    """
    【Lark 兼容接口 1】根据 command_id 查询单个命令 spec
    保持与 tools.py 导入的原函数接口和行为一致
    """
    return _lark_registry_service.get_command_spec(command_id)


def search_commands(
    query: str = "",
    service: str = "",
    intent: str = "",
    resource_parts: Optional[List[str]] = None,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """
    【Lark 兼容接口 2】多维度模糊搜索与检索注册表
    保持与 tools.py 导入的原函数接口和行为一致
    """
    return _lark_registry_service.search_commands(
        query=query,
        service=service,
        intent=intent,
        resource_parts=resource_parts,
        limit=limit,
    )


def find_command_for_args(args: List[str]) -> Optional[Dict[str, Any]]:
    """
    【Lark 兼容接口 3】根据运行参数反向推导最匹配的命令 spec
    保持与 tools.py 导入的原函数接口和行为一致
    """
    return _lark_registry_service.find_command_for_args(args)
