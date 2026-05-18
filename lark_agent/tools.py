import logging
import re
import time


from google.adk.tools import ToolContext

from lark_agent.config import LARK_AUTH_ID, LARK_CLIENT_ID
from lark_agent.infrastructure import lark_api_repository
from lark_agent.infrastructure.cli_client import cli_client

logger = logging.getLogger(__name__)

STATUS_KEY = "status"
MESSAGE_KEY = "message"
DOCUMENTS_KEY = "documents"
DOCUMENTS_TOKEN = "documents_token"

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


def _normalize_markdown_for_new_doc(title: str, markdown: str) -> str:
    """
    Normalize generated markdown before create-doc.

    Feishu MCP create-doc already uses `title` as the document title, so we strip
    a duplicated leading H1 if it matches the title to avoid repeated headings.
    """
    if not markdown:
        return markdown

    normalized = markdown.strip()
    title_clean = (title or "").strip()
    if not title_clean:
        return normalized

    pattern = rf"^#\s+{re.escape(title_clean)}\s*(\r?\n)+"
    normalized = re.sub(pattern, "", normalized, count=1, flags=re.IGNORECASE)
    return normalized.strip()


def get_access_token(tool_context: ToolContext) -> str:
    """
    Get the access token from the tool context.
    """
    if isinstance(tool_context, str):
        return tool_context
    else:
        return tool_context.state.get(f"{LARK_AUTH_ID}")


def query_lark_documents(query: str, tool_context: ToolContext) -> dict:
    """
    Queries Lark documents for the authenticated user.

    It performs a real-time search against the Lark Cloud Documents API.
    Before searching, it verifies the user's authentication status.
    The search results are provided in two formats: one for display and one for further processing.

    Args:
        query: The user's search query (keyword).
        tool_context: The tool execution context for accessing session state and tokens.

    Returns:
        dict: A dictionary containing the search results if successful, or an error/auth_required status.
              - On success:
                {
                    'status': 'success',
                    'documents': ['<a href="...">Title</a>', ...], # For User Display
                    'documents_token': [{'title': '...', 'doc_token': '...'}, ...] # For Tool Use (e.g. fetching content)
                }
              - On auth required: {'status': 'auth_required', ...}
              - On error: {'status': 'error', 'message': ...}
    """
    try:
        access_token = get_access_token(tool_context)

        if not access_token:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Access token is missing. Please ensure OAuth is configured.",
            }

        documents = lark_api_repository.search_documents(access_token, query)

        # 生成带预览的文档列表（美观格式）
        # 格式：每个文档包含标题、链接和内容预览（如果有）
        display_docs = []
        for idx, doc in enumerate(documents):
            title = doc.get("title", "Untitled")
            url = doc.get("url", "")
            doc_token = doc.get("docs_token", "")
            doc_type = doc.get("docs_type", "").lower()
            summary_highlighted = doc.get("summary_highlighted", "")
            search_query = doc.get("search_query", query)  # 使用保存的搜索关键词

            # 预览内容生成策略：
            # 1. 优先使用搜索结果中的高亮摘要（summary_highlighted）
            #    优点：无需额外API调用，性能好
            #    缺点：可能内容较短，上下文不够丰富
            # 2. 如果没有高亮摘要，从文档内容中提取关键字附近的文本
            #    优点：上下文更丰富，可以提取更多内容
            #    缺点：需要额外的API调用（get_document_content），性能开销大
            preview_text = ""
            if summary_highlighted and summary_highlighted.strip():
                # 清理高亮标签，转换为 Markdown 加粗格式
                # 飞书API返回的格式：<h>关键字</h> -> Markdown格式：**关键字**
                preview_text = summary_highlighted.replace("<h>", "**").replace(
                    "</h>", "**"
                )
                # 限制长度，避免预览过长
                if len(preview_text) > 300:
                    preview_text = preview_text[:300] + "..."
            else:
                # 如果没有高亮摘要，从文档内容中提取关键字附近的文本
                # 性能优化：只为前5个文档获取预览，避免过多API调用
                # 只处理文档类型（doc/docx），其他类型（sheet、file等）不支持内容预览
                if idx < 5 and doc_token and doc_type in ["doc", "docx"]:
                    try:
                        preview_text = lark_api_repository.get_document_preview(
                            access_token,
                            doc_token,
                            doc_type,
                            search_query=search_query,  # 传入搜索关键词，用于提取关键字上下文
                            max_length=300,  # 预览长度限制
                        )
                    except Exception:
                        # 预览获取失败不影响主流程，静默处理
                        pass

            # 构建结构化的 Markdown 格式输出
            # 使用 Markdown 语法创建美观的文档卡片样式

            # 构建文档标题和链接
            if url:
                doc_display = f"### 📄 {idx + 1}. [{title}]({url})\n\n"
            else:
                doc_display = f"### 📄 {idx + 1}. {title}\n\n"

            # 如果有预览内容，添加到显示中
            if preview_text and preview_text.strip():
                # 清理预览文本
                preview_clean = preview_text.strip()
                # 限制预览行数（最多3行）
                preview_lines = preview_clean.split("\n")
                if len(preview_lines) > 3:
                    preview_clean = "\n".join(preview_lines[:3]) + "..."
                # 限制总长度
                if len(preview_clean) > 250:
                    preview_clean = preview_clean[:250] + "..."

                # 使用引用块来显示预览内容，增强视觉效果
                # 将预览文本按行分割，每行作为引用块的一部分
                preview_lines_formatted = preview_clean.split("\n")
                preview_markdown = "\n".join(
                    [
                        f"> {line}" if line.strip() else ">"
                        for line in preview_lines_formatted
                    ]
                )

                doc_display += f"**内容预览：**\n\n{preview_markdown}\n\n"

            # 如果有URL，添加链接提示
            if url:
                doc_display += f"🔗 [打开文档 →]({url})\n"

            # 添加分隔线（最后一个文档不加）
            if idx < len(documents) - 1:
                doc_display += "\n---\n\n"

            display_docs.append(doc_display)

        return {
            STATUS_KEY: STATUS_SUCCESS,
            DOCUMENTS_KEY: display_docs,
            DOCUMENTS_TOKEN: [
                {
                    "title": doc.get("title"),
                    "doc_token": doc.get("docs_token"),
                    "doc_type": doc.get("docs_type"),
                }
                for doc in documents
            ],
        }
    except Exception as e:
        logger.error(f"Error querying Lark documents: {str(e)}", exc_info=True)
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Failed to query Lark documents: {str(e)}",
        }


def get_lark_document_content(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Retrieves the content of a specific Lark document in Markdown format.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        doc_type: The document type (doc, docx, sheet, bitable). Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the document content if successful.
              - On success: {'status': 'success', 'content': '...markdown content...'}
              - On error: {'status': 'error', 'message': ...}
    """
    try:
        access_token = get_access_token(tool_context)

        if not access_token:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Access token is missing. Please ensure OAuth is configured.",
            }

        content = lark_api_repository.get_document_content(
            access_token, doc_token, doc_type
        )

        return {STATUS_KEY: STATUS_SUCCESS, "content": content}

    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to get document content: {error_detail}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"Failed to get document content for token {doc_token} (type: {doc_type}): {str(e)}",
            "debug_info": error_detail,
        }


def show_user_auth_info(tool_context: ToolContext) -> str:
    """
    Displays the current user authentication information using the standard ADK auth response format.

    Args:
        tool_context: The tool execution context.

    Returns:
        The authentication information.
    """
    token = ""
    try:
        token = get_access_token(tool_context)
        if not token:
            token = f"""tool_context.state[f"temp:{LARK_AUTH_ID}"] is None"""
    except Exception as e:
        token = (
            """tool_context.state[f"temp:{LARK_AUTH_ID}"] raised exception: """ + str(e)
        )

    tool_context.state["LARK_AUTH_INFO"] = token
    return str(tool_context.state.to_dict())


def get_lark_document_content_pdf(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Exports a Lark document directly to PDF format.
    Use this when you want the model to see the document exactly as it would appear when printed/viewed.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        doc_type: The source document type. Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the description and the PDF data in multimodal parts.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        # 1. 权限预检
        perm_result = lark_api_repository.check_document_permission(
            access_token, doc_token, doc_type
        )
        if not perm_result.get("has_export_permission"):
            error_msg = (
                perm_result.get("error")
                or "Export permission is explicitly denied for this document."
            )
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: f"⚠️ [PERMISSION DENIED] Cannot export PDF. {error_msg}",
                "debug_info": perm_result,
            }

        # 2. 导出为 PDF (现在返回原始 bytes)
        pdf_bytes = lark_api_repository.get_document_as_pdf(
            access_token, doc_token, doc_type
        )

        if not pdf_bytes:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Failed to export PDF content (or document is too large). Please try 'get_lark_document_content' for text-only access.",
            }

        # 将 PDF bytes 通过 __multimodal_parts__ 传递给模型视觉通道
        from .callbacks import MULTIMODAL_PARTS_KEY

        return {
            "status": "success",
            "text_content": "以下是该文档的 PDF 多模态视图。请直接阅读 PDF 内容回答用户问题。",
            "pdf_size_bytes": len(pdf_bytes),
            MULTIMODAL_PARTS_KEY: [
                {
                    "mime_type": "application/pdf",
                    "data": pdf_bytes,
                }
            ],
        }
    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to export PDF: {error_detail}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"⚠️ [TECHNICAL ERROR] Failed to export PDF for token {doc_token} (type: {doc_type}). Reason: {str(e)}",
            "debug_info": error_detail,
        }


def get_lark_document_content_docx(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Exports a Lark document directly to Word (docx) format.
    Use this when you want the model to analyze the document in its native Word structure.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        doc_type: The source document type. Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the status and the Word data in multimodal parts.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        # 1. 权限预检
        perm_result = lark_api_repository.check_document_permission(
            access_token, doc_token, doc_type
        )
        if not perm_result.get("has_export_permission"):
            error_msg = (
                perm_result.get("error")
                or "Export permission is explicitly denied for this document."
            )
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: f"⚠️ [PERMISSION DENIED] Cannot export Word. {error_msg}",
                "debug_info": perm_result,
            }

        # 2. 导出为 Word
        docx_bytes = lark_api_repository.get_document_as_docx(
            access_token, doc_token, doc_type
        )

        if not docx_bytes:
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: "Failed to export Word content (or document is too large).",
            }

        # 将 Word bytes 通过 __multimodal_parts__ 传递给模型
        from .callbacks import MULTIMODAL_PARTS_KEY

        return {
            "status": "success",
            "text_content": "以下是该文档的 Word (docx) 多模态数据。请分析其内容回答用户问题。",
            "docx_size_bytes": len(docx_bytes),
            MULTIMODAL_PARTS_KEY: [
                {
                    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "data": docx_bytes,
                }
            ],
        }
    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to export Word: {error_detail}")
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"⚠️ [TECHNICAL ERROR] Failed to export Word for token {doc_token} (type: {doc_type}). Reason: {str(e)}",
            "debug_info": error_detail,
        }


def get_lark_document_rich_content(
    doc_token: str, tool_context: ToolContext, doc_type: str = "docx"
) -> dict:
    """
    Exports a Lark document and extracts its rich content, including text and images.
    Use this when you need to analyze the document's structure or inspect embedded images.

    Args:
        doc_token: The unique identifier of the document.
        tool_context: The tool execution context.
        doc_type: The source document type (e.g., 'docx', 'doc', 'sheet'). Defaults to 'docx'.

    Returns:
        dict: A dictionary containing the document's text and image parts.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        # 1. 权限预检
        perm_result = lark_api_repository.check_document_permission(
            access_token, doc_token, doc_type
        )
        if not perm_result.get("has_export_permission"):
            error_msg = (
                perm_result.get("error")
                or "Export permission is explicitly denied for this document."
            )
            return {
                STATUS_KEY: STATUS_ERROR,
                MESSAGE_KEY: f"⚠️ [PERMISSION DENIED] Cannot get rich content. {error_msg}",
                "debug_info": perm_result,
            }

        # 2. 获取富文本内容
        result = lark_api_repository.get_document_rich_text_by_block(
            access_token, doc_token, doc_type
        )

        repo_parts = result.get("parts", [])
        text_segments = []  # 文本内容
        multimodal_parts = []  # 多媒体数据（图片/PDF），将通过 FunctionResponse.parts 传递

        for item in repo_parts:
            # 处理结构化数据 (来自 Repository 的 dict)
            if isinstance(item, dict):
                p_type = item.get("type")

                if p_type == "text":
                    text_segments.append(item.get("content", ""))

                elif p_type == "image":
                    img_name = item.get("name", "unknown")
                    img_data = item.get(
                        "data"
                    )  # raw bytes (来自 _process_image_to_bytes)
                    mime = item.get("mime_type", "image/jpeg")

                    if img_data:
                        # 将图片数据收集到 multimodal_parts（通过 __multimodal_parts__ 传递）
                        multimodal_parts.append(
                            {
                                "mime_type": mime,
                                "data": img_data,
                            }
                        )
                        # 在文本中插入图片占位符，帮助模型关联上下文
                        text_segments.append(
                            f"\n[📷 图片 {img_name} - 见下方视觉输入 #{len(multimodal_parts)}]\n"
                        )
                    else:
                        text_segments.append(f"\n[图片数据缺失: {img_name}]\n")

            # 处理纯文本 (兼容旧格式)
            elif isinstance(item, str):
                text_segments.append(item)

        # 如果处理的图片数量少于总数，添加提示
        if result.get("processed_image_count", 0) < result.get("image_count", 0):
            text_segments.append(
                f"\n\n> *注：文档包含更多图片（共 {result.get('image_count')} 张），"
                f"已自动展示前 {result.get('processed_image_count')} 张。*"
            )

        # 构造返回值：
        # - text_content: 合并后的文本，放入 FunctionResponse.response dict
        # - __multimodal_parts__: 图片数据，被 patch 后的 ADK 提取
        #   到 FunctionResponse.parts 中，让模型真正"看到"
        from .callbacks import MULTIMODAL_PARTS_KEY

        response = {
            "status": "success",
            "text_content": "\n".join(text_segments),
            "image_count": len(multimodal_parts),
            "total_image_count": result.get("image_count", 0),
        }

        if multimodal_parts:
            response[MULTIMODAL_PARTS_KEY] = multimodal_parts
            logger.info(
                f"[get_lark_document_rich_content] 返回 {len(multimodal_parts)} 张图片 "
                f"(通过 FunctionResponse.parts 视觉通道)"
            )

        return response
    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Failed to get rich content: {error_detail}")
        # 将具体错误信息合并到 message 中，确保用户在界面能感知到具体原因（如权限不足、Token错误等）
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: f"⚠️ [TECHNICAL ERROR] Failed to get rich content for token {doc_token} (type: {doc_type}). Reason: {str(e)}",
            "debug_info": error_detail,
        }


def create_lark_document(
    title: str, markdown_content: str, tool_context: ToolContext, folder_token: str = ""
) -> dict:
    """
    Creates a new Lark document with the specified title and Markdown content.

    Args:
        title: The title of the new document.
        markdown_content: The content of the document in Lark-flavored Markdown.
        tool_context: The tool execution context.
        folder_token: Optional. The token of the parent folder where the document should be created.

    Returns:
        dict: A dictionary containing the status and document info (doc_token, url).
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        args = ["--title", title, "--markdown", markdown_content]
        if folder_token:
            args += ["--folder-token", folder_token]

        result = cli_client.run_command("docs", "+create", args, access_token, LARK_CLIENT_ID)
        return result
    except Exception as e:
        logger.error(f"Failed to create Lark document: {str(e)}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def update_lark_document(
    doc_token: str, markdown_content: str, tool_context: ToolContext, mode: str = "append"
) -> dict:
    """
    Updates an existing Lark document.

    Args:
        doc_token: The unique identifier or URL of the document.
        markdown_content: The new content or content to append in Lark-flavored Markdown.
        tool_context: The tool execution context.
        mode: The update mode. Options: 'append' (default), 'overwrite', 'replace_range', 'replace_all', 'insert_before', 'insert_after', 'delete_range'.

    Returns:
        dict: A dictionary containing the update status.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        args = ["--doc", doc_token, "--markdown", markdown_content, "--mode", mode]
        result = cli_client.run_command("docs", "+update", args, access_token, LARK_CLIENT_ID)
        return result
    except Exception as e:
        logger.error(f"Failed to update Lark document: {str(e)}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def delete_lark_document(doc_token: str, tool_context: ToolContext) -> dict:
    """
    Deletes a Lark document or file.

    Args:
        doc_token: The unique identifier or URL of the document/file.
        tool_context: The tool execution context.

    Returns:
        dict: A dictionary containing the deletion status.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Authentication required."}

        args = ["--doc", doc_token]
        result = cli_client.run_command("drive", "+delete", args, access_token, LARK_CLIENT_ID)
        return result
    except Exception as e:
        logger.error(f"Failed to delete Lark document: {str(e)}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def feishu_mcp_create_doc(
    title: str = "",
    markdown: str = "",
    tool_context: ToolContext = None,
    folder_token: str = "",
    wiki_node: str = "",
    wiki_space: str = "",
    task_id: str = "",
) -> dict:
    """
    Creates a new Lark Cloud Document using the official MCP logic.

    Args:
        title: Document title. Required when task_id is not provided.
        markdown: Content in Lark-flavored Markdown. Required when task_id is not provided.
        tool_context: Tool execution context.
        folder_token: Optional folder token.
        wiki_node: Optional wiki node token or URL.
        wiki_space: Optional wiki space ID, supports "my_library".
        task_id: Optional async task ID for polling a previous create-doc request.

    Returns:
        dict: Structured result with status and doc fields.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Auth required."}

        args = {}
        if task_id:
            args["task_id"] = task_id
        else:
            if not title.strip():
                return {
                    STATUS_KEY: STATUS_ERROR,
                    MESSAGE_KEY: "title is required when task_id is not provided.",
                }
            if not markdown.strip():
                return {
                    STATUS_KEY: STATUS_ERROR,
                    MESSAGE_KEY: "markdown is required when task_id is not provided.",
                }
            args["title"] = title
            args["markdown"] = _normalize_markdown_for_new_doc(title, markdown)

        if folder_token:
            args["folder_token"] = folder_token
        if wiki_node:
            args["wiki_node"] = wiki_node
        if wiki_space:
            args["wiki_space"] = wiki_space

        result = lark_api_repository.call_feishu_mcp_tool(access_token, "create-doc", args)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        response = {STATUS_KEY: STATUS_SUCCESS, "raw_result": result}
        for key in ("doc_id", "doc_url", "message", "task_id", "log_id", "warnings"):
            if key in data:
                response[key] = data[key]
        if "message" not in response:
            response["message"] = "Document create request completed."
        return response
    except Exception as e:
        logger.error(f"MCP Create Doc Failed: {e}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def save_ai_output_to_feishu_doc(
    title: str,
    markdown_content: str,
    tool_context: ToolContext,
    folder_token: str = "",
    wiki_node: str = "",
    wiki_space: str = "",
) -> dict:
    """
    Save generated AI output into a new Feishu cloud document.

    This is a business-level wrapper around `feishu_mcp_create_doc` intended for
    agent use when the user asks to write the answer into Feishu.
    """
    if not title.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "title is required."}
    if not markdown_content.strip():
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: "markdown_content is required.",
        }

    result = feishu_mcp_create_doc(
        title=title,
        markdown=markdown_content,
        tool_context=tool_context,
        folder_token=folder_token,
        wiki_node=wiki_node,
        wiki_space=wiki_space,
    )
    if result.get(STATUS_KEY) != STATUS_SUCCESS:
        return result

    message = result.get("message", "Document created.")
    doc_url = result.get("doc_url", "")
    doc_id = result.get("doc_id", "")
    normalized = {
        STATUS_KEY: STATUS_SUCCESS,
        "title": title,
        "doc_id": doc_id,
        "doc_url": doc_url,
        "message": message,
    }
    if doc_url:
        normalized["summary"] = f"Created document '{title}' at {doc_url}"
    elif doc_id:
        normalized["summary"] = f"Created document '{title}' (doc_id: {doc_id})"
    else:
        normalized["summary"] = f"Created document '{title}'"
    return normalized


def save_ai_output_to_existing_feishu_doc(
    doc_id: str,
    markdown_content: str,
    tool_context: ToolContext,
    mode: str = "append",
    selection_with_ellipsis: str = "",
    selection_by_title: str = "",
    new_title: str = "",
) -> dict:
    """
    Save generated AI output into an existing Feishu cloud document.

    This is a business-level wrapper around `feishu_mcp_update_doc`.
    """
    if not doc_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "doc_id is required."}

    requires_markdown = mode != "delete_range"
    if requires_markdown and not markdown_content.strip():
        return {
            STATUS_KEY: STATUS_ERROR,
            MESSAGE_KEY: "markdown_content is required for the selected mode.",
        }

    result = feishu_mcp_update_doc(
        doc_id=doc_id,
        markdown=markdown_content,
        tool_context=tool_context,
        mode=mode,
        selection_with_ellipsis=selection_with_ellipsis,
        selection_by_title=selection_by_title,
        new_title=new_title,
    )
    if result.get(STATUS_KEY) != STATUS_SUCCESS:
        return result

    normalized = {
        STATUS_KEY: STATUS_SUCCESS,
        "doc_id": result.get("doc_id", doc_id),
        "mode": result.get("mode", mode),
        "message": result.get("message", "Document update request completed."),
    }
    for key in ("task_id", "warnings", "log_id", "replace_count", "success"):
        if key in result:
            normalized[key] = result[key]

    normalized["summary"] = (
        f"Updated document '{normalized['doc_id']}' with mode '{normalized['mode']}'"
    )
    return normalized


def wait_for_feishu_doc_create_task(
    task_id: str,
    tool_context: ToolContext,
    max_polls: int = 10,
    poll_interval_seconds: float = 1.0,
) -> dict:
    """
    Poll a previously submitted create-doc async task until it completes or the
    polling window is exhausted.
    """
    if not task_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "task_id is required."}
    if max_polls < 1:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "max_polls must be >= 1."}

    last_result = None
    for attempt in range(1, max_polls + 1):
        result = feishu_mcp_create_doc(
            task_id=task_id,
            tool_context=tool_context,
        )
        last_result = result
        if result.get(STATUS_KEY) != STATUS_SUCCESS:
            return result
        if not result.get("task_id"):
            result["completed"] = True
            result["poll_attempts"] = attempt
            result["summary"] = (
                f"Create task '{task_id}' completed after {attempt} poll(s)."
            )
            return result
        if attempt < max_polls:
            time.sleep(poll_interval_seconds)

    return {
        STATUS_KEY: STATUS_SUCCESS,
        "completed": False,
        "task_id": task_id,
        "poll_attempts": max_polls,
        "message": last_result.get("message", "Create task is still running.")
        if isinstance(last_result, dict)
        else "Create task is still running.",
        "summary": f"Create task '{task_id}' is still running after {max_polls} poll(s).",
        "raw_result": last_result,
    }


def wait_for_feishu_doc_update_task(
    task_id: str,
    tool_context: ToolContext,
    max_polls: int = 10,
    poll_interval_seconds: float = 1.0,
) -> dict:
    """
    Poll a previously submitted update-doc async task until it completes or the
    polling window is exhausted.
    """
    if not task_id.strip():
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "task_id is required."}
    if max_polls < 1:
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "max_polls must be >= 1."}

    last_result = None
    for attempt in range(1, max_polls + 1):
        result = feishu_mcp_update_doc(
            task_id=task_id,
            tool_context=tool_context,
        )
        last_result = result
        if result.get(STATUS_KEY) != STATUS_SUCCESS:
            return result
        if not result.get("task_id"):
            result["completed"] = True
            result["poll_attempts"] = attempt
            result["summary"] = (
                f"Update task '{task_id}' completed after {attempt} poll(s)."
            )
            return result
        if attempt < max_polls:
            time.sleep(poll_interval_seconds)

    return {
        STATUS_KEY: STATUS_SUCCESS,
        "completed": False,
        "task_id": task_id,
        "poll_attempts": max_polls,
        "message": last_result.get("message", "Update task is still running.")
        if isinstance(last_result, dict)
        else "Update task is still running.",
        "summary": f"Update task '{task_id}' is still running after {max_polls} poll(s).",
        "raw_result": last_result,
    }


def feishu_mcp_update_doc(
    doc_id: str = "",
    markdown: str = "",
    tool_context: ToolContext = None,
    mode: str = "append",
    selection_with_ellipsis: str = "",
    selection_by_title: str = "",
    new_title: str = "",
    task_id: str = "",
) -> dict:
    """
    Updates a Lark Cloud Document using the official MCP logic.

    Args:
        doc_id: Document ID or URL.
        markdown: Content to update.
        tool_context: Tool execution context.
        mode: Update mode (append, overwrite, replace_range, etc.). Default is 'append'.
        selection_with_ellipsis: Optional content-based locator.
        selection_by_title: Optional heading-based locator.
        new_title: Optional new document title.
        task_id: Optional async task ID for polling a previous update-doc request.

    Returns:
        dict: Success or error message.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Auth required."}

        args = {"mode": mode}
        if task_id:
            args["task_id"] = task_id
        else:
            if not doc_id.strip():
                return {
                    STATUS_KEY: STATUS_ERROR,
                    MESSAGE_KEY: "doc_id is required when task_id is not provided.",
                }
            args["doc_id"] = doc_id
            if markdown:
                args["markdown"] = markdown
            if selection_with_ellipsis:
                args["selection_with_ellipsis"] = selection_with_ellipsis
            if selection_by_title:
                args["selection_by_title"] = selection_by_title
            if new_title:
                args["new_title"] = new_title

        result = lark_api_repository.call_feishu_mcp_tool(access_token, "update-doc", args)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        response = {STATUS_KEY: STATUS_SUCCESS, "raw_result": result}
        for key in (
            "doc_id",
            "task_id",
            "message",
            "mode",
            "warnings",
            "log_id",
            "replace_count",
            "success",
        ):
            if key in data:
                response[key] = data[key]
        if "message" not in response:
            response["message"] = "Document update request completed."
        return response
    except Exception as e:
        logger.error(f"MCP Update Doc Failed: {e}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}


def feishu_mcp_fetch_doc(
    doc_id: str, tool_context: ToolContext, offset: int = 0, limit: int = 50000
) -> dict:
    """
    Fetches content of a Lark Cloud Document as Markdown via MCP.

    Args:
        doc_id: Document ID or URL.
        tool_context: Tool execution context.
        offset: Character offset for large docs.
        limit: Max characters to return.

    Returns:
        dict: Title and markdown content.
    """
    try:
        access_token = get_access_token(tool_context)
        if not access_token:
            return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: "Auth required."}

        args = {"doc_id": doc_id, "offset": offset, "limit": limit}
        result = lark_api_repository.call_feishu_mcp_tool(access_token, "fetch-doc", args)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        response = {STATUS_KEY: STATUS_SUCCESS, "raw_result": result}
        for key in ("doc_id", "title", "markdown", "content", "message", "has_more", "next_offset"):
            if key in data:
                response[key] = data[key]
        if "message" not in response and "markdown" in response:
            response["message"] = "Document fetched successfully."
        return response
    except Exception as e:
        logger.error(f"MCP Fetch Doc Failed: {e}")
        return {STATUS_KEY: STATUS_ERROR, MESSAGE_KEY: str(e)}
