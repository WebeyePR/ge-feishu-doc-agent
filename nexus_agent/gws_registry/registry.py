# -*- coding: utf-8 -*-
"""
Google Workspace (GWS) CLI 模块化命令注册表查询逻辑
--------------------------------------------------
本模块实现对 GWS 注册表 index 的查询，通过继承与实例化 lark_registry 中的 CLICommandRegistry 核心类，
实现了跨平台命令注册表在算法、缓存、分词逻辑上的 100% 代码复用。
消除了原有 GWS 模块和 Lark 模块之间的代码重复，并且无缝为 GWS CLI 赋予了先进的“多层次递归分词（支持+、.拆分）”能力，
大幅度提升了 GWS 复杂命令（如 docs.+create 等）的检索精度。
模块提供 100% 的模块级 API 向后兼容。
"""

from typing import Any, Dict, List, Optional
from nexus_agent.lark_registry.registry import CLICommandRegistry

# -----------------------------------------------------------------------------
# GWS 专属注册表服务单例
# -----------------------------------------------------------------------------
# 实例化通用 CLI 注册表类，传入当前 gws_registry 包名以自动加载对应的 commands.json
_gws_registry_service = CLICommandRegistry(__package__)


# -----------------------------------------------------------------------------
# GWS 专属向后兼容模块级接口层（Wrapper）
# -----------------------------------------------------------------------------

def get_command_spec(command_id: str) -> Optional[Dict[str, Any]]:
    """
    【GWS 兼容接口 1】根据 command_id 精确查询单个 GWS 命令的参数规范和示例。
    保持与 tools.py 导入的原函数接口和行为一致。
    """
    return _gws_registry_service.get_command_spec(command_id)


def search_commands(
    query: str = "",
    service: str = "",
    intent: str = "",
    resource_parts: Optional[List[str]] = None,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """
    【GWS 兼容接口 2】多维度多层次模糊搜索与 NLP 分词检索。
    保持与 tools.py 导入的原函数接口和行为一致。
    由于复用了 CLICommandRegistry，GWS 现在天然共享最先进的拆词重合度加权检索算法。
    """
    return _gws_registry_service.search_commands(
        query=query,
        service=service,
        intent=intent,
        resource_parts=resource_parts,
        limit=limit,
    )


def find_command_for_args(args: List[str]) -> Optional[Dict[str, Any]]:
    """
    【GWS 兼容接口 3】逆向推导：根据当前物理执行的命令行参数，匹配其在注册表中最对齐的规范 spec。
    保持与 tools.py 导入的原函数接口和行为一致。
    """
    return _gws_registry_service.find_command_for_args(args)
