import logging
import io
import zipfile
import xml.etree.ElementTree as ET
import time
import base64
from urllib3.util.retry import Retry


from requests import Session, HTTPError
from requests.adapters import HTTPAdapter
from PIL import Image

from lark_agent.config import LARK_DOMAIN

# NOTE: 不再直接依赖 vertexai.generative_models.Part
# 多媒体数据通过 callbacks.py 的 FunctionResponsePart 机制传递给模型

logger = logging.getLogger(__name__)


def _get_session():
    """
    创建一个带有重试机制的 requests Session。
    用于处理不稳定的网络连接，特别是 ConnectionResetError (Errno 54)。
    """
    session = Session()
    retry_strategy = Retry(
        total=5,  # 最多重试5次
        backoff_factor=1,  # 退避系数，重试间隔: 1s, 2s, 4s, 8s, 16s
        status_forcelist=[429, 500, 502, 503, 504],  # 触发重试的状态码
        allowed_methods=["HEAD", "GET", "POST", "OPTIONS"],  # 允许重试的 HTTP 方法
        raise_on_status=False,  # 不要立即抛出状态码异常，由代码处理
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# 全局共享的 Session 对象
_session = _get_session()


def _get_header(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def search_documents(access_token: str, query: str, count: int = 20) -> list:
    """
    Searches for documents using Lark Search API v2.

    功能说明：
    - 使用新的搜索API v2 (/open-apis/search/v2/doc_wiki/search)
    - 支持搜索所有文档类型：DOC, SHEET, BITABLE, MINDNOTE, FILE, WIKI, DOCX, CATALOG, SLIDES, SHORTCUT
    - 直接返回文档URL，无需额外API调用
    - 返回结果包含高亮摘要 (summary_highlighted)，用于显示关键字上下文

    技术细节：
    - API限制：page_size 最大为20
    - 返回的URL可以直接使用，无需调用 batch_get_document_urls
    - 搜索结果包含 title_highlighted 和 summary_highlighted，已包含关键字高亮标记 <h>...</h>

    Args:
        access_token: The user access token.
        query: The search keyword.
        count: The number of results to return (max 20).

    Returns:
        List of document dictionaries, each containing:
        - docs_token: Document token for content retrieval
        - docs_type: Document type (doc, docx, sheet, etc.)
        - title: Document title (with highlight tags removed)
        - url: Document URL (directly usable)
        - entity_type: Entity type (DOC or WIKI)
        - summary_highlighted: Highlighted summary with <h> tags around keywords
        - search_query: The original search query (for reference)
    """
    logger.info(f"--- search_documents ---: {query[:100]}")
    url = f"{LARK_DOMAIN}/open-apis/search/v2/doc_wiki/search"

    # 支持的所有文档类型
    all_doc_types = [
        "DOC",
        "SHEET",
        "BITABLE",
        "MINDNOTE",
        "FILE",
        "WIKI",
        "DOCX",
        "CATALOG",
        "SLIDES",
        "SHORTCUT",
    ]

    payload = {
        "query": query,
        "doc_filter": {
            "doc_types": all_doc_types,
            "only_title": False,
            "sort_type": "CREATE_TIME_ASC",
        },
        "wiki_filter": {
            "doc_types": all_doc_types,
            "only_title": False,
            "sort_type": "CREATE_TIME_ASC",
        },
        "page_size": min(count, 20),  # API限制最大20
    }

    response = _session.post(url, headers=_get_header(access_token), json=payload)
    response.raise_for_status()
    data = response.json()

    # 检查飞书 API 的错误码
    if data.get("code") != 0:
        error_msg = data.get("msg", "Unknown error")
        error_code = data.get("code", "Unknown")
        # 记录更详细的请求上下文
        logger.error(
            f"Lark API Search Error: {error_msg} (Code: {error_code}), Query: {query}"
        )
        raise Exception(f"Lark Search API Error [Code {error_code}]: {error_msg}")

    # 解析新接口返回的数据
    res_units = data.get("data", {}).get("res_units", [])
    documents = []

    for unit in res_units:
        meta = unit.get("result_meta", {})
        entity_type = unit.get("entity_type", "")

        # 提取文档信息（包括高亮摘要）
        # 注意：title_highlighted 和 summary_highlighted 包含 <h>...</h> 标签，需要转换为 Markdown 格式
        doc_info = {
            "docs_token": meta.get("token", ""),  # 用于后续获取文档内容
            "docs_type": meta.get("doc_types", "").lower(),  # DOC -> doc，用于类型判断
            "title": unit.get("title_highlighted", "")
            .replace("<h>", "")
            .replace("</h>", ""),  # 移除高亮标签，只保留纯文本
            "url": meta.get("url", ""),  # 文档URL，可直接使用
            "entity_type": entity_type,  # DOC 或 WIKI
            "summary_highlighted": unit.get(
                "summary_highlighted", ""
            ),  # 高亮摘要，包含 <h> 标签，需要在显示时转换为 **
            "search_query": query,  # 保存搜索关键词，用于后续从文档内容中提取上下文
        }
        documents.append(doc_info)

    return documents


def batch_get_document_urls(access_token: str, docs_list: list) -> dict:
    """
    [DEPRECATED] Batch queries document metadata to retrieve URLs.

    ⚠️ 此函数已弃用，不再被使用。

    原因：
    - 新的搜索API (search/v2/doc_wiki/search) 已经直接返回URL
    - 不再需要额外的批量查询API调用
    - 旧API需要额外的权限 (drive:drive, drive:drive.metadata:readonly)

    历史：
    - 之前用于在搜索后批量获取文档URL
    - 现在 search_documents() 已经直接返回URL，无需此步骤

    保留原因：
    - 暂时保留以防回滚需要
    - 计划在确认新API稳定后删除

    Args:
        access_token: The user access token.
        docs_list: List of document objects (containing docs_token and docs_type).

    Returns:
        A dictionary mapping doc_token to its URL.
    """
    url = f"{LARK_DOMAIN}/open-apis/drive/v1/metas/batch_query"

    if not docs_list:
        return {}

    # 构建请求文档列表，检查字段名
    request_docs = []
    for doc in docs_list:
        # 搜索 API 返回的字段可能是 "docs_token" 或 "token"
        doc_token = doc.get("docs_token") or doc.get("token")
        # 搜索 API 返回的字段可能是 "docs_type" 或 "type"
        doc_type = doc.get("docs_type") or doc.get("type") or "doc"

        # 类型映射：搜索 API 返回 "doc"，但批量查询 API 需要 "docx"
        type_mapping = {
            "doc": "docx",
            "sheet": "sheet",
            "slides": "slides",
            "bitable": "bitable",
            "file": "file",
        }
        doc_type = type_mapping.get(doc_type, doc_type)

        if doc_token:
            request_docs.append({"doc_token": doc_token, "doc_type": doc_type})

    if not request_docs:
        return {}

    payload = {"request_docs": request_docs, "with_url": True}

    try:
        response = _session.post(url, headers=_get_header(access_token), json=payload)
        response.raise_for_status()
        data = response.json()

        # 检查飞书 API 的错误码
        if data.get("code") != 0:
            error_msg = data.get("msg", "Unknown error")
            error_code = data.get("code", "Unknown")
            logger.error(
                f"Lark API Batch Query Error: {error_msg} (Code: {error_code})"
            )
            raise Exception(f"Lark Batch Query Error [Code {error_code}]: {error_msg}")

        url_map = {}
        metas = data.get("data", {}).get("metas", [])
        for meta in metas:
            doc_token = meta.get("doc_token")
            url_value = meta.get("url")
            if doc_token and url_value:
                url_map[doc_token] = url_value

        return url_map
    except HTTPError as e:
        logger.error(f"HTTP Error: {e}")
        logger.error(f"Response: {e.response.text if e.response else 'No response'}")
        raise


def get_document_content(
    access_token: str, doc_token: str, doc_type: str = "docx"
) -> str:
    """
    Retrieves the Markdown content of a specific Lark document.

    Args:
        access_token: The user access token.
        doc_token: The unique identifier of the document.
        doc_type: The document type (doc, docx, sheet, bitable). Defaults to 'docx'.

    Returns:
        The markdown content of the document as a string.

    Raises:
        Exception: If the API call fails or returns a non-zero error code.
    """
    url = f"{LARK_DOMAIN}/open-apis/docs/v1/content"
    params = {
        "doc_token": doc_token,
        "doc_type": doc_type,
        "content_type": "markdown",
    }

    response = _session.get(url, headers=_get_header(access_token), params=params)
    response.raise_for_status()
    data = response.json()

    if data.get("code") == 0:
        return data.get("data", {}).get("content", "")
    else:
        error_msg = data.get("msg", "Unknown error")
        error_code = data.get("code", "Unknown")
        logger.error(
            f"Lark API Content Error: {error_msg} (Code: {error_code}), Token: {doc_token}"
        )
        raise Exception(f"Lark Content API Error [Code {error_code}]: {error_msg}")


def get_document_preview(
    access_token: str,
    doc_token: str,
    doc_type: str,
    search_query: str = "",
    max_length: int = 300,
) -> str:
    """
    Gets a preview of document content around the search keyword.

    功能说明：
    - 优先提取关键字附近的上下文文本（如果提供了 search_query）
    - 如果没有关键字，返回文档的前几段内容
    - 只支持文档类型 (doc, docx)，其他类型返回空字符串

    实现逻辑：
    1. 如果提供了 search_query：
       - 调用 _extract_context_around_keyword() 提取关键字附近的文本
       - 关键字会被加粗显示 (**keyword**)
    2. 如果没有 search_query：
       - 返回文档的前3段内容（跳过标题）
       - 限制总长度为 max_length

    性能考虑：
    - 需要调用 get_document_content() API，会增加一次API请求
    - 建议只在必要时使用（如搜索结果没有 summary_highlighted 时）

    Args:
        access_token: The user access token.
        doc_token: The unique identifier of the document.
        doc_type: The document type (doc, docx, sheet, etc.).
        search_query: The search keyword to highlight (optional).
        max_length: Maximum length of preview in characters (default: 300).

    Returns:
        Preview text with highlighted keywords in Markdown format,
        or empty string if preview cannot be retrieved (e.g., unsupported doc_type).
    """
    try:
        # 只对文档类型获取预览
        if doc_type.lower() not in ["doc", "docx"]:
            return ""

        # 获取完整内容
        content = get_document_content(access_token, doc_token)

        if not content:
            return ""

        # 如果有搜索关键词，提取关键字附近的文本
        if search_query:
            preview = _extract_context_around_keyword(content, search_query, max_length)
            if preview:
                return preview

        # 否则返回前几段
        paragraphs = content.split("\n\n")
        preview_parts = []
        current_length = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 跳过标题（以 # 开头）
            if para.startswith("#"):
                continue

            # 如果加上这段会超过限制，就截取部分
            if current_length + len(para) > max_length:
                remaining = max_length - current_length
                if remaining > 50:  # 至少保留50个字符
                    preview_parts.append(para[:remaining] + "...")
                break

            preview_parts.append(para)
            current_length += len(para) + 2  # +2 for '\n\n'

            # 限制最多3段
            if len(preview_parts) >= 3:
                break

        preview = "\n\n".join(preview_parts)

        # 如果内容被截断，添加省略号
        if len(content) > current_length:
            preview += "..."

        return preview

    except Exception:
        # 预览获取失败，返回空字符串
        return ""


def _extract_context_around_keyword(
    content: str, keyword: str, max_length: int = 300
) -> str:
    """
    从文档内容中提取关键字附近的文本，并加粗关键字。

    功能说明：
    - 在文档内容中查找关键字第一次出现的位置
    - 提取关键字前后各 max_length/2 字符的上下文
    - 将关键字加粗显示（Markdown格式：**keyword**）
    - 如果不在文档开头/结尾，添加省略号

    实现细节：
    - 使用正则表达式进行不区分大小写的匹配
    - 如果关键字不存在，返回空字符串
    - 上下文范围：关键字前 max_length/2，关键字后 max_length/2

    使用场景：
    - 当搜索结果没有提供 summary_highlighted 时
    - 需要从文档内容中提取关键字上下文时

    Args:
        content: 文档的完整内容（Markdown格式）
        keyword: 搜索关键词
        max_length: 预览的最大长度（默认300字符）

    Returns:
        包含关键字及其上下文的文本，关键字已加粗（Markdown格式）
        如果关键字不存在，返回空字符串
    """
    import re

    # 转义特殊字符用于正则表达式
    keyword_escaped = re.escape(keyword)

    # 查找所有关键字出现的位置（不区分大小写）
    pattern = re.compile(keyword_escaped, re.IGNORECASE)
    matches = list(pattern.finditer(content))

    if not matches:
        return ""

    # 使用第一个匹配位置
    match = matches[0]
    start_pos = match.start()

    # 计算上下文范围（关键字前后各一半）
    context_before = max_length // 2
    context_after = max_length // 2

    # 提取上下文
    context_start = max(0, start_pos - context_before)
    context_end = min(len(content), start_pos + len(keyword) + context_after)

    # 提取文本
    context_text = content[context_start:context_end]

    # 在文本开头和结尾添加省略号（如果不在文档开头/结尾）
    if context_start > 0:
        context_text = "..." + context_text
    if context_end < len(content):
        context_text = context_text + "..."

    # 加粗所有关键字（不区分大小写）
    highlighted_text = pattern.sub(lambda m: f"**{m.group()}**", context_text)

    return highlighted_text


def check_document_permission(
    access_token: str, doc_token: str, doc_type: str = "docx"
) -> dict:
    """
    检查用户对文档的导出权限。

    功能说明：
    - 调用飞书 Drive V2 权限接口查询文档的公共设置。
    - 自动识别多种导出权限字段（export_entity, copy_print_export_entity, copy_entity）。
    - 判断当前文档是否允许被复制、打印或导出。

    实现逻辑：
    1. 访问 `/open-apis/drive/v2/permissions/{doc_token}/public` 接口。
    2. 解析 `permission_public` 对象中的权限实体字段。
    3. 如果字段明确为 "no_one_can_export" 或 "cannot_export"，则认定为无权限。
    4. 在 code 为 0 且无明确禁止标识时，默认授予权限。

    Args:
        access_token: 用户访问令牌
        doc_token: 文档标识符
        doc_type: 文档类型 (doc, docx, sheet, bitable)

    Returns:
        dict: 包含导出权限状态的字典。
        - has_export_permission: bool, 是否允许导出。
        - export_entity: str, 匹配到的权限实体值。
        - raw_data: dict, 原始权限数据 (用于调试)。
    """
    url = f"{LARK_DOMAIN}/open-apis/drive/v2/permissions/{doc_token}/public"
    params = {"type": doc_type}

    try:
        response = _session.get(url, headers=_get_header(access_token), params=params)
        if response.status_code != 200:
            logger.warning(
                f"Permission API returned non-200 status: {response.status_code}"
            )
        response.raise_for_status()
        data = response.json()

        if data.get("code") != 0:
            return {"has_export_permission": False, "error": data.get("msg")}

        public_data = data.get("data", {}).get("permission_public", {})
        # 飞书 API 可能返回 export_entity, copy_print_export_entity 或 copy_entity
        export_entity = (
            public_data.get("export_entity")
            or public_data.get("copy_print_export_entity")
            or public_data.get("copy_entity")
        )

        # 如果字段不存在，可能意味着默认允许或者权限模型不同。
        # 在权限 code 为 0 的情况下，如果没有明确的 "no_one_can_export" 或 "cannot_export"，
        # 且用户能调通接口，通常是有权限的。
        has_permission = True
        if export_entity in ["no_one_can_export", "cannot_export"]:
            has_permission = False

        return {
            "has_export_permission": has_permission,
            "export_entity": export_entity or "unknown",
            "raw_data": public_data,  # 方便调试
        }
    except Exception as e:
        logger.error(f"Error checking export permission: {str(e)}")
        return {"has_export_permission": False, "error": str(e)}


def _create_export_task(
    access_token: str, file_token: str, file_type: str, target_type: str = "docx"
) -> str:
    """
    创建云文档导出任务。

    功能说明：
    - 启动飞书云文档导出任务，返回任务票据 (ticket)。
    - 支持将各类云文档导出为指定格式的文件。

    Args:
        access_token: 用户访问令牌。
        file_token: 文档令牌。
        file_type: 原文档类型 (docx, sheet 等)。
        target_type: 导出目标格式 (docx, pdf, xlsx, csv)。

    Returns:
        str: 任务查询凭证 (ticket)。
    """
    url = f"{LARK_DOMAIN}/open-apis/drive/v1/export_tasks"
    payload = {"file_extension": target_type, "token": file_token, "type": file_type}

    response = _session.post(url, headers=_get_header(access_token), json=payload)
    if response.status_code != 200:
        logger.error(
            f"Lark Export Task Request Failed: {response.status_code}, Body: {response.text}"
        )
        raise Exception(
            f"Lark Export API Request Failed ({response.status_code}): {response.text}"
        )

    data = response.json()
    if data.get("code") != 0:
        logger.error(
            f"Lark Export Task Business Error: {data.get('msg')} (Code: {data.get('code')})"
        )
        raise Exception(
            f"Lark Export Business Error [Code {data.get('code')}]: {data.get('msg')}"
        )

    return data.get("data", {}).get("ticket", "")


def _get_export_task_status(access_token: str, ticket: str, file_token: str) -> dict:
    """
    查询导出任务的进度和状态。

    功能说明：
    - 根据创建任务时获得的票据，查询当前任务的执行状态。
    - 当任务完成 (job_status == 0) 时，提供结果文件的令牌。

    Args:
        access_token: 用户访问令牌。
        ticket: 任务查询凭证。
        file_token: 原文档令牌。

    Returns:
        dict: 导出任务结果对象，包含 job_status 和 file_token (成功时)。
    """
    url = f"{LARK_DOMAIN}/open-apis/drive/v1/export_tasks/{ticket}"
    params = {"token": file_token}

    try:
        response = _session.get(url, headers=_get_header(access_token), params=params)
        if response.status_code != 200:
            logger.error(
                f"Lark Export Status Request Failed: {response.status_code}, Body: {response.text}"
            )
            return {}
        data = response.json()
    except Exception as e:
        logger.error(f"Error getting export task status: {str(e)}")
        return {}

    if data.get("code") != 0:
        logger.error(
            f"Lark Export Status Business Error: {data.get('msg')} (Code: {data.get('code')})"
        )
        raise Exception(
            f"Lark Export Status Error [Code {data.get('code')}]: {data.get('msg')}"
        )

    return data.get("data", {})


def _download_document_by_export(access_token: str, file_token: str) -> bytes:
    """
    下载导出的二进制文件内容。

    功能说明：
    - 使用专门的导出下载接口获取最终生成的文档二进制数据。

    Args:
        access_token: 用户访问令牌。
        file_token: 导出的文件令牌 (从导出任务状态中获取)。

    Returns:
        bytes: 文件的二进制字节流。
    """
    # 根据用户测试反馈修正下载接口路径
    url = f"{LARK_DOMAIN}/open-apis/drive/v1/export_tasks/file/{file_token}/download"

    response = _session.get(
        url, headers={"Authorization": f"Bearer {access_token}"}, stream=True
    )
    response.raise_for_status()
    return response.content


def _export_document(
    access_token: str,
    doc_token: str,
    doc_type: str,
    target_type: str,
    max_size_bytes: int = None,
    timeout: int = 40,
) -> bytes:
    """
    云文档导出流水线基础函数。

    功能说明：
    - 编排“创建任务 -> 轮询状态 -> 下载结果”的完整异步导出流程。
    - 针对 ConnectionResetError 进行了会话级重试优化。
    - 支持导出过程中的文件大小校验。

    实现逻辑：
    1. 调用 _create_export_task 启动导出。
    2. 进入轮询循环，每 0.5 秒查询一次状态，最多重试 60 次 (约 30 秒)。
    3. 如果状态为 0 (成功)，提取 file_token 并校验数据大小。
    4. 调用 _download_document_by_export 下载二进制内容。

    Args:
        access_token: 用户访问令牌。
        doc_token: 文档标识符。
        doc_type: 原文档类型。
        target_type: 目标导出类型。
        max_size_bytes: 最大允许的文件大小 (bytes)。
        timeout: 超时时间（秒）。

    Returns:
        bytes: 导出的二进制数据内容。
    """
    # 1. 创建导出任务
    ticket = _create_export_task(access_token, doc_token, doc_type, target_type)

    # 2. 轮询任务状态
    result_file_token = ""
    for _ in range(int(timeout / 0.5)):
        time.sleep(0.5)
        status_data = _get_export_task_status(access_token, ticket, doc_token)
        if not status_data:
            logger.warning(f"_get_export_task_status failed: {ticket}, {doc_token}")
            continue

        result = status_data.get("result", {})
        job_status = result.get("job_status")

        if job_status == 0:  # 成功
            file_size = result.get("file_size", 0)
            if max_size_bytes and file_size > max_size_bytes:
                logger.warning(
                    f"Exported file for {doc_token} ({target_type}) is too large: {file_size} > {max_size_bytes}"
                )
                return None
            result_file_token = result.get("file_token", "")
            break
        elif job_status not in [1, 2]:  # 如果不是 1(初始化) 或 2(处理中)，则视为失败
            job_error_msg = result.get("job_error_msg", "Unknown error")
            logger.error(
                f"Lark Export Job Error: Status {job_status}, Msg: {job_error_msg}, Token: {doc_token}"
            )
            return None

    if not result_file_token:
        return None

    # 3. 下载导出的媒体文件
    return _download_document_by_export(access_token, result_file_token)


def _process_image_to_base64(
    image_bytes: bytes, max_size=(1536, 1536), quality=85
) -> str:
    """
    处理图片：调整大小、压缩并转换为 Base64 字符串（JPG 格式）。

    Args:
        image_bytes: 原始图片二进制数据。
        max_size: 最大宽高。
        quality: JPG 压缩质量 (1-100)。

    Returns:
        Processed image as a base64 string.
    """
    try:
        logger.info(f"--- [before] image_bytes len --- {len(image_bytes)}")
        img = Image.open(io.BytesIO(image_bytes))

        # 转换为 RGB (防止透明度问题及兼容 JPG)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # 调整大小 (保持比例)，1536px 是 Gemini 视觉识别的甜点位，既清晰又不会超大
        img.thumbnail(max_size, Image.Resampling.LANCZOS)

        # 保存到内存
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        processed_bytes = output.getvalue()

        data = base64.b64encode(processed_bytes).decode("utf-8")
        # if data.startswith("data:image"):
        #     data = data.split(",", 1)[-1]
        logger.info(f"--- [after] image len --- {len(data)}")
        return data
    except Exception as e:
        logger.warning(
            f"Image processing failed: {str(e)}. Falling back to original base64."
        )
        # 如果处理失败，尝试原样返回（简单 base64）
        return base64.b64encode(image_bytes).decode("utf-8")


def _process_image_to_bytes(
    image_bytes: bytes, max_size=(1536, 1536), quality=85
) -> bytes:
    """
    处理图片：调整大小、压缩并返回优化后的二进制数据（JPG 格式）。

    Args:
        image_bytes: 原始图片二进制数据。
        max_size: 最大宽高。
        quality: JPG 压缩质量 (1-100)。

    Returns:
        Processed image data as bytes.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # 转换为 RGB (防止透明度问题及兼容 JPG)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # 调整大小 (保持比例)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)

        # 保存到内存
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue()
    except Exception as e:
        logger.warning(
            f"Image processing failed: {str(e)}. Falling back to original bytes."
        )
        return image_bytes


def _download_media(access_token: str, media_token: str) -> bytes:
    """
    下载飞书云文档中的媒体文件（图片、附件等）。

    Args:
        access_token: 用户访问令牌。
        media_token: 媒体文件 Token。

    Returns:
        bytes: 媒体文件的二进制内容。
    """
    url = f"{LARK_DOMAIN}/open-apis/drive/v1/medias/{media_token}/download"

    response = _session.get(url, headers=_get_header(access_token), stream=True)
    if response.status_code != 200:
        logger.error(f"Download Media Error: {response.status_code} - {response.text}")
    response.raise_for_status()
    return response.content


def _download_media_by_batch(
    access_token: str, media_token: str | list[str]
) -> dict[str, bytes]:
    """
    下载飞书云文档中的媒体文件（图片等）。
    使用 batch_get_tmp_download_url 接口获取临时链接，再进行下载。
    返回 {media_token: content_bytes} 的字典。
    """
    tokens = [media_token] if isinstance(media_token, str) else media_token
    if not tokens:
        return {}

    # 1. 获取临时下载链接
    batch_url = f"{LARK_DOMAIN}/open-apis/drive/v1/medias/batch_get_tmp_download_url"
    headers = _get_header(access_token)
    params = {"file_tokens": tokens}

    batch_res = _session.get(batch_url, headers=headers, params=params)
    if batch_res.status_code != 200:
        logger.error(
            f"Batch Get Tmp Download URL Failed: {batch_res.status_code} - {batch_res.text}"
        )
        batch_res.raise_for_status()

    batch_data = batch_res.json()
    if batch_data.get("code") != 0:
        msg = batch_data.get("msg", "Unknown error")
        logger.error(f"Batch Get Tmp Download URL API Error: {msg}")
        raise Exception(f"Batch Get Tmp Download URL Error: {msg}")

    tmp_urls = batch_data.get("data", {}).get("tmp_download_urls", [])

    result = {}
    # TODO 优化为批量下载，使用多线程或异步
    for item in tmp_urls:
        token = item.get("file_token")
        download_url = item.get("tmp_download_url")
        if token and download_url:
            try:
                # 2. 从临时链接下载
                response = _session.get(download_url, stream=True)
                if response.status_code == 200:
                    result[token] = response.content
                else:
                    logger.error(
                        f"Download from Tmp URL Failed for {token}: {response.status_code}"
                    )
            except Exception as e:
                logger.error(f"Error downloading {token}: {e}")

    return result


def _parse_block_text(block: dict) -> str:
    """解析 Docx Block 中的文本内容，返回 Markdown 格式。"""
    content = ""
    # 不同类型的 Block，其文本内容存放位置不同
    # text, heading1-9, bullet, ordered, code, quote, todo 等都包含 text 字段
    text_data = None
    prefix = ""

    b_type = block.get("block_type")
    if b_type == 1:  # text
        text_data = block.get("text")
    elif 2 <= b_type <= 10:  # heading 1-9
        text_data = block.get(f"heading{b_type - 1}")
        prefix = "#" * (b_type - 1) + " "
    elif b_type == 12:  # bullet
        text_data = block.get("bullet")
        prefix = "- "
    elif b_type == 13:  # ordered
        text_data = block.get("ordered")
        prefix = "1. "  # 简化处理
    elif b_type == 14:  # code
        text_data = block.get("code")
        # 代码块特殊处理
        elements = text_data.get("elements", []) if text_data else []
        code_text = "".join(
            [e.get("text_run", {}).get("content", "") for e in elements]
        )
        return f"```\n{code_text}\n```\n"
    elif b_type == 15:  # quote
        text_data = block.get("quote")
        prefix = "> "
    elif b_type == 17:  # todo
        text_data = block.get("todo")
        prefix = "- [ ] "

    if text_data:
        elements = text_data.get("elements", [])
        for e in elements:
            if "text_run" in e:
                text_run = e["text_run"]
                text = text_run.get("content", "")
                # 处理样式 (简单加粗和链接)
                style = text_run.get("text_element_style", {})
                if style.get("bold"):
                    text = f"**{text}**"
                if style.get("link"):
                    link = style["link"].get("url", "")
                    text = f"[{text}]({link})"
                content += text
            elif "mention" in e:
                content += e["mention"].get("name", "")
            elif "equation" in e:
                content += f" ${e['equation'].get('content', '')}$ "

    return prefix + content + "\n" if content else ""


def get_document_rich_text_by_word(
    access_token: str, doc_token: str, doc_type: str = "docx", limit_media_num: int = 20
) -> dict:
    """
    导出文档为 docx 格式并解析为富文本（支持图文交织）。

    功能说明：
    - 导出云文档为 Word (.docx) 并在内存中动态解压解析。
    - 按照物理顺序提取文本和图片。
    - 返回包含文本段落和图片 Part 对象的混合列表。

    Args:
        access_token: 用户访问令牌。
        doc_token: 文档标识符。
        doc_type: 文档原类型。
        limit_media_num: 最大提取图片数量 (默认 20)。

    Returns:
        dict: 包含 'parts' (混合列表) 等。
    """
    content_bytes = _export_document(access_token, doc_token, doc_type, "docx")

    part_list = []
    images_metadata = []
    image_count = 0

    with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
        # 1. 解析关系文件映射 rId -> 路径
        rels = {}
        rels_path = "word/_rels/document.xml.rels"
        if rels_path in zf.namelist():
            rels_root = ET.fromstring(zf.read(rels_path))
            for rel in rels_root:
                rid = rel.get("Id")
                target = rel.get("Target")
                if rid and target:
                    # Target 可能是 media/image1.png，需要补全路径
                    if not target.startswith("word/"):
                        target = f"word/{target}"
                    rels[rid] = target

        # 2. 解析主文档 XML
        if "word/document.xml" in zf.namelist():
            xml_content = zf.read("word/document.xml")
            root = ET.fromstring(xml_content)

            # 定义命名空间
            ns = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
                "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
                "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
            }

            # 遍历所有段落
            for p in root.findall(".//w:p", ns):
                para_markdown = ""
                # 在段落中寻找文本或图片
                for elem in p.findall(".//*", ns):
                    # 提取文本
                    if elem.tag == f"{{{ns['w']}}}t" and elem.text:
                        para_markdown += elem.text

                    # 提取图片 (查找 blip 中的 rId)
                    elif elem.tag == f"{{{ns['a']}}}blip":
                        rid = elem.get(f"{{{ns['r']}}}embed")
                        if rid and rid in rels and image_count < limit_media_num:
                            img_path = rels[rid]
                            if img_path in zf.namelist():
                                img_data = zf.read(img_path)
                                # 处理图片：转换为优化后的 bytes，不转 Base64
                                img_bytes = _process_image_to_bytes(img_data)

                                img_name = img_path.split("/")[-1]
                                # 使用结构化字典返回，交给 Tool 层处理
                                part_list.append(
                                    {
                                        "type": "image",
                                        "name": img_name,
                                        "data": img_bytes,
                                        "mime_type": "image/jpeg",
                                    }
                                )

                                images_metadata.append({"name": img_name, "token": rid})
                                image_count += 1

                                # 图片已作为独立 Part 添加，无需拼接到 para_markdown
                                continue

                if para_markdown.strip():
                    part_list.append({"type": "text", "content": para_markdown})

        # 统计总图片数 (即使超过 limit_media_num 也统计总数)
        total_media = len([f for f in zf.namelist() if f.startswith("word/media/")])

    return {
        "parts": part_list,
        "image_count": total_media,
        "processed_image_count": image_count,
        "images": images_metadata,
    }


def get_document_rich_text_by_block(
    access_token: str, doc_token: str, doc_type: str = "docx", limit_media_num: int = 20
) -> dict:
    """
    通过 Block API 逐个读取文档块，获取文本和图片。
    文本内容会被解析为 Markdown 格式。
    这种方式只需要阅读权限，且比导出方式更稳定，不容易受导出限制。

    Args:
        access_token: 用户访问令牌。
        doc_token: 文档标识符。
        limit_media_num: 最大下载图片数量。

    Returns:
        dict: 包含 parts 列表，格式同 get_document_rich_text。
    """
    logger.info(f"--- get_document_rich_text_by_block ---: {doc_type}")
    try:
        url = f"{LARK_DOMAIN}/open-apis/docx/v1/documents/{doc_token}/blocks"
        headers = _get_header(access_token)

        part_list = []
        image_count = 0
        has_more = True
        page_token = ""

        all_blocks = []
        while has_more:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token

            response = _session.get(url, headers=headers, params=params)
            if response.status_code != 200:
                logger.error(f"Lark API Error Body: {response.text}")
            response.raise_for_status()
            data = response.json()

            if data.get("code") != 0:
                error_msg = data.get("msg", "Unknown error")
                logger.error(f"Failed to get blocks: {error_msg}")
                break

            block_data = data.get("data", {})
            items = block_data.get("items", [])
            all_blocks.extend(items)
            has_more = block_data.get("has_more", False)
            page_token = block_data.get("page_token", "")

        # 2. 收集图片 Token 并批量下载
        tokens_to_download = []
        for block in all_blocks:
            if block.get("block_type") == 27:  # image
                token = block.get("image", {}).get("token")
                if token and len(tokens_to_download) < limit_media_num:
                    tokens_to_download.append(token)

        downloaded_images = {}
        if tokens_to_download:
            logger.info(f"Batch downloading {len(tokens_to_download)} images...")
            downloaded_images = _download_media_by_batch(
                access_token, tokens_to_download
            )

        # 3. 解析为 Parts
        part_list = []
        image_count = 0
        total_image_count = 0
        images_metadata = []

        for block in all_blocks:
            b_type = block.get("block_type")

            # 处理文本相关的 Block
            if b_type in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 17]:
                text_content = _parse_block_text(block)
                if text_content:
                    if part_list and part_list[-1]["type"] == "text":
                        part_list[-1]["content"] += text_content
                    else:
                        part_list.append({"type": "text", "content": text_content})

            # 处理图片 Block
            elif b_type == 27:  # image
                total_image_count += 1
                token = block.get("image", {}).get("token")

                if token:
                    if token in downloaded_images:
                        img_data = downloaded_images[token]
                        img_bytes = _process_image_to_bytes(img_data)
                        # img_bytes = _process_image_to_base64(img_data)
                        img_name = f"image_{token[:8]}.jpg"

                        part_list.append(
                            {
                                "type": "image",
                                "name": img_name,
                                "data": img_bytes,
                                "mime_type": "image/jpeg",
                            }
                        )
                        images_metadata.append({"name": img_name, "token": token})
                        image_count += 1
                    else:
                        # 如果没有在下载列表中（超过 limit_media_num 或下载失败）
                        if image_count < limit_media_num:
                            part_list.append(
                                {
                                    "type": "text",
                                    "content": f"\n[图片下载失败: {token}]\n",
                                }
                            )
                        else:
                            part_list.append(
                                {
                                    "type": "text",
                                    "content": f"\n[图片页略过: {token}]\n",
                                }
                            )
        logger.info(f"=== get_document_rich_text_by_block ===: {total_image_count}")
        return {
            "parts": part_list,
            "image_count": total_image_count,
            "processed_image_count": image_count,
            "images": images_metadata,
        }

    except Exception as e:
        logger.error(f"Failed in get_document_rich_text_by_block: {e}")
        raise e


def get_document_as_pdf(
    access_token: str, doc_token: str, doc_type: str = "docx", limit_size: int = 15
) -> bytes:
    """
    将云文档直接导出为 PDF 二进制格式。

    功能说明：
    - 适用于需要保留完美视觉排版、表格布局和多页结构的分析场景。
    - 限制提取超过 15MB 的 PDF 文件。

    Args:
        access_token: 用户访问令牌。
        doc_token: 文档标识符。
        doc_type: 文档原类型。
        limit_size: PDF 文件大小限制（MB）。

    Returns:
        bytes: PDF 文件的二进制数据。
    """
    pdf_bytes = _export_document(access_token, doc_token, doc_type, "pdf")
    if not pdf_bytes:
        return None

    if len(pdf_bytes) > limit_size * 1024 * 1024:
        logger.warning(
            f"PDF file {doc_token} is too large: {len(pdf_bytes) / (1024 * 1024)}MB > {limit_size}MB"
        )
        # return None

    # 调用带大小校验的导出
    return pdf_bytes


def get_document_as_docx(
    access_token: str, doc_token: str, doc_type: str = "docx", limit_size: int = 10
) -> bytes:
    """
    将云文档导出为 Word (docx) 二进制格式。
    限制提取超过 10MB 的 Word 文件。

    Args:
        access_token: 用户访问令牌。
        doc_token: 文档标识符。
        doc_type: 文档原类型。
        limit_size: 文件大小限制（MB）。

    Returns:
        bytes: docx 文件的二进制数据。
    """
    docx_bytes = _export_document(access_token, doc_token, doc_type, "docx")
    if not docx_bytes:
        return None

    # 导出前预检大小
    if len(docx_bytes) > limit_size * 1024 * 1024:
        logger.warning(
            f"File {doc_token} is too large before export: {len(docx_bytes) / (1024 * 1024)}MB > {limit_size}MB"
        )

    return docx_bytes
