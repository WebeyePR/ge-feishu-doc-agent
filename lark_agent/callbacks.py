"""
ADK 多模态扩展模块：Patch ADK 以支持工具返回图片/PDF 等多媒体。

背景：
  ADK 框架在构建 FunctionResponse 时只填充了 response 字段（JSON dict），
  没有填充 parts 字段。Gemini API 的 FunctionResponse.parts 才是存放
  多媒体数据（图片、PDF 等）的正确位置。如果只放在 response dict 的 inline_data 里，
  模型只会将其视为普通文本数据，而无法真正"看到"图片。

方案：
  Monkey-patch google.adk.flows.llm_flows.functions 模块中的
  __build_response_event 函数。当工具返回值的 dict 中包含特殊键
  '__multimodal_parts__' 时，将其提取为 FunctionResponsePart 列表，
  并注入到 FunctionResponse.parts 中，从而让模型获得真正的视觉输入。

用法：
  在 agent 模块初始化时调用 patch_adk_for_multimodal() 即可。
  工具端只需在返回 dict 中增加 '__multimodal_parts__' 键，值为 list[dict]，
  每个 dict 包含 {'mime_type': ..., 'data': base64_str_or_bytes}。
"""

import base64
import logging
from typing import Optional

from google.genai import types

logger = logging.getLogger(__name__)

# 工具返回值中用于标记多媒体数据的特殊键
MULTIMODAL_PARTS_KEY = "__multimodal_parts__"


def patch_adk_for_multimodal():
    """
    Monkey-patch ADK 的 __build_response_event 函数，
    使其支持在 FunctionResponse 中携带图片/PDF 等多媒体 parts。

    只需在应用启动时调用一次。重复调用是安全的（幂等）。
    """
    import google.adk.flows.llm_flows.functions as fn_module

    # 检查是否已经 patch 过
    if getattr(fn_module, "_multimodal_patched", False):
        logger.info("[multimodal_patch] 已经 patch 过，跳过")
        return

    # 获取原始函数引用
    original_build = getattr(fn_module, "__build_response_event", None)
    if original_build is None:
        logger.error(
            "[multimodal_patch] 无法找到 __build_response_event，跳过 patch。"
            "ADK 版本可能不兼容。"
        )
        return

    def patched_build_response_event(
        tool, function_result, tool_context, invocation_context
    ):
        """
        增强版 __build_response_event，支持从 function_result 中提取
        MULTIMODAL_PARTS_KEY 标记的多媒体数据，并注入 FunctionResponse.parts。

        当 function_result 中不含 MULTIMODAL_PARTS_KEY 时，行为与原始函数完全一致。
        """
        # 1. 提取多模态 parts（如果存在）
        multimodal_parts = None
        if (
            isinstance(function_result, dict)
            and MULTIMODAL_PARTS_KEY in function_result
        ):
            raw_parts = function_result.pop(MULTIMODAL_PARTS_KEY)
            multimodal_parts = _build_function_response_parts(raw_parts)
            logger.info(
                f"[multimodal_patch] 工具 '{tool.name}' 提取了 "
                f"{len(multimodal_parts) if multimodal_parts else 0} 个多媒体 parts"
            )

        # 2. 确保 function_result 是 dict（ADK spec 要求）
        if not isinstance(function_result, dict):
            function_result = {"result": function_result}

        # 3. 构造支持 parts 的 FunctionResponse
        function_response = types.FunctionResponse(
            name=tool.name,
            response=function_result,
            parts=multimodal_parts,  # ← 图片/PDF 通过这里传递给模型
        )
        function_response.id = tool_context.function_call_id

        part_with_fr = types.Part(function_response=function_response)

        from google.adk.events.event import Event

        content = types.Content(
            role="user",
            parts=[part_with_fr],
        )

        function_response_event = Event(
            invocation_id=invocation_context.invocation_id,
            author=invocation_context.agent.name,
            content=content,
            actions=tool_context.actions,
            branch=invocation_context.branch,
        )

        return function_response_event

    # 3. 执行 patch
    fn_module.__build_response_event = patched_build_response_event
    fn_module._multimodal_patched = True  # 幂等标记
    logger.info("[multimodal_patch] ADK __build_response_event 已成功 patch")


def _build_function_response_parts(
    raw_parts: list[dict],
) -> Optional[list[types.FunctionResponsePart]]:
    """
    将原始多媒体数据字典列表转换为 FunctionResponsePart 对象列表。

    输入格式:
        [
            {"mime_type": "image/jpeg", "data": "<base64_string>"},
            {"mime_type": "image/png",  "data": b"<raw_bytes>"},
        ]

    Args:
        raw_parts: 包含 mime_type 和 data 的字典列表。

    Returns:
        FunctionResponsePart 对象列表，如果为空则返回 None。
    """
    result = []
    for idx, part_data in enumerate(raw_parts):
        mime_type = part_data.get("mime_type", "image/jpeg")
        data = part_data.get("data")

        if data is None:
            logger.warning(f"[multimodal_patch] part #{idx}: 数据为空，跳过")
            continue

        try:
            # base64 字符串 → bytes
            if isinstance(data, str):
                image_bytes = base64.b64decode(data)
            elif isinstance(data, bytes):
                image_bytes = data
            else:
                logger.warning(
                    f"[multimodal_patch] part #{idx}: 不支持的数据类型 {type(data)}，跳过"
                )
                continue

            fr_part = types.FunctionResponsePart.from_bytes(
                data=image_bytes, mime_type=mime_type
            )
            result.append(fr_part)
            logger.debug(
                f"[multimodal_patch] part #{idx}: {mime_type}, "
                f"{len(image_bytes)} bytes → FunctionResponsePart"
            )
        except Exception as e:
            logger.warning(f"[multimodal_patch] part #{idx}: 构造失败: {e}")

    return result if result else None
