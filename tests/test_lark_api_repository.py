import os
import sys
import logging

import requests
from dotenv import load_dotenv, find_dotenv

# 添加项目根目录到 path，以便导入 lark_agent
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lark_agent.infrastructure.lark_api_repository import (
    get_document_content,
    get_document_rich_text_by_block,
    get_document_as_pdf,
    get_document_as_docx,
)

print(f"Loaded .env from: {find_dotenv()}")
# 自动查找并加载（会向上搜索父目录）
load_dotenv(find_dotenv())

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 定义输出目录（当前目录下的 output 文件夹）
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_tenant_access_token():
    """从 .env 获取配置并请求飞书 Token"""
    app_id = os.environ.get("LARK_CLIENT_ID")
    app_secret = os.environ.get("LARK_CLIENT_SECRET")
    lark_domain = os.environ.get("LARK_DOMAIN", "https://open.feishu.cn")

    if not app_id or not app_secret:
        raise ValueError("请在 .env 中配置 LARK_CLIENT_ID 和 LARK_CLIENT_SECRET")

    url = f"{lark_domain}/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}

    logger.info(f"正在获取 Token (App ID: {app_id})...")
    response = requests.post(url, json=payload)
    response.raise_for_status()
    data = response.json()

    if data.get("code") == 0:
        return data.get("tenant_access_token")
    else:
        raise Exception(f"获取 Token 失败: {data.get('msg')}")


def test_get_plain_text(token, doc_token):
    logger.info(f"开始测试 get_document_content (纯文本), token: {doc_token}...")
    try:
        content = get_document_content(token, doc_token)
        logger.info(f"纯文本抓取成功！预览 (前200字): {content[:200]}...")

        # 保存到本地
        output_path = os.path.join(OUTPUT_DIR, "test_output_plain.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"纯文本内容已保存至: {output_path}")
    except requests.exceptions.HTTPError as e:
        logger.error(f"纯文本抓取失败 (HTTP Error): {e}")
        if e.response is not None:
            logger.error(f"响应详情: {e.response.text}")
    except Exception as e:
        logger.error(f"纯文本抓取失败: {e}")


def test_get_rich_text_by_block(token, doc_token):
    logger.info(
        f"开始测试 get_document_rich_text_by_block (图文), token: {doc_token}..."
    )
    try:
        result = get_document_rich_text_by_block(token, doc_token)
        logger.info("图文内容抓取成功！部分结果展示：")

        parts = result.get("parts", [])
        text_content = ""

        # 创建 media 目录保存图片
        media_dir = os.path.join(OUTPUT_DIR, "media")
        os.makedirs(media_dir, exist_ok=True)

        for p in parts:
            if p["type"] == "text":
                text_content += p["content"]
            elif p["type"] == "image":
                img_name = p.get("name", "unknown.jpg")
                img_data = p.get("data")
                if img_data:
                    img_path = os.path.join(media_dir, img_name)
                    with open(img_path, "wb") as f:
                        f.write(img_data)
                    logger.info(f"保存图片: {img_path}")

        # 保存文本内容
        output_path = os.path.join(OUTPUT_DIR, "test_output_rich.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text_content)

        logger.info(f"图文模式文本内容已保存至: {output_path}")
        logger.info(f"图片数量: {result.get('image_count')}")
    except Exception as e:
        logger.error(f"图文内容抓取失败: {e}")


def test_export_pdf(token, doc_token):
    logger.info(f"开始测试 get_document_as_pdf, token: {doc_token}...")
    try:
        pdf_bytes = get_document_as_pdf(token, doc_token)
        if pdf_bytes:
            logger.info(f"PDF 导出成功，大小: {len(pdf_bytes)} 字节")
            # 写出到本地进行人工检查
            output_path = os.path.join(OUTPUT_DIR, "test_output.pdf")
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)
            logger.info(f"PDF 已保存至 (绝对路径): {output_path}")
        else:
            logger.warning("PDF 导出返回为空 (可能触发了大小限制)")
    except Exception as e:
        logger.error(f"导出 PDF 失败: {e}")


def test_export_docx(token, doc_token):
    logger.info(f"开始测试 get_document_as_docx, token: {doc_token}...")
    try:
        docx_bytes = get_document_as_docx(token, doc_token)
        if docx_bytes:
            logger.info(f"Docx 导出成功，大小: {len(docx_bytes)} 字节")
            output_path = os.path.join(OUTPUT_DIR, "test_output.docx")
            with open(output_path, "wb") as f:
                f.write(docx_bytes)
            logger.info(f"Docx 已保存至 (绝对路径): {output_path}")
        else:
            logger.warning("Docx 导出返回为空 (可能触发了大小限制)")
    except Exception as e:
        logger.error(f"导出 Docx 失败: {e}")


if __name__ == "__main__":
    # 控制台手动获取token，有效期两小时
    # 地址：https://open.feishu.cn/document/server-docs/docs/drive-v1/file/batch_query?appId=cli_a9b3af98cdf9dcef
    token = ""  # Feishu App token, 类似 u-cQpSE73dJdfFu6faGHKdEM0k0XEkk1WpOoGyjA4w253q
    doc_token = ""  # Feishu 文档token, 类似 Fo7vdQkchoWbk6xGoCScpw1cn18
    if len(sys.argv) > 1:
        token = sys.argv[1]
    else:
        print("未输入 Feishu App Token")
        os._exit(1)
    if len(sys.argv) > 2:
        doc_token = sys.argv[2]
    else:
        print("未输入 Feishu 文档 Token")
        os._exit(1)

    try:
        # token = get_tenant_access_token()  # 权限不足
        # 1. 测试纯文字抓取
        test_get_plain_text(token, doc_token)

        # 2. 测试图文模式抓取 (Block API)
        test_get_rich_text_by_block(token, doc_token)

        # 3. 测试 PDF 导出
        test_export_pdf(token, doc_token)

        # 4. 测试 Word 导出
        test_export_docx(token, doc_token)

    except Exception as e:
        logger.error(f"测试初始化失败: {e}")
