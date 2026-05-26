#!/usr/bin/env python3
"""
批量删除 Vertex AI Reasoning Engine 脚本
- 安全交互模式，每次删除前确认
- 支持前缀匹配过滤
- 分页显示，避免列出太多
"""

import os
import sys
import requests
from datetime import UTC, datetime


def parse_google_datetime(value):
    """Parse Google API timestamps as timezone-aware UTC datetimes."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def get_gcloud_token():
    """获取 gcloud access token"""
    try:
        import subprocess
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"❌ 获取 gcloud token 失败: {e}")
        print(f"   请先运行: gcloud auth login")
        sys.exit(1)


def list_reasoning_engines(session, project_id, location, token, prefix=None):
    """列出所有 Reasoning Engine，可按前缀过滤"""
    url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/reasoningEngines"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project_id,
    }

    response = session.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    engines = data.get("reasoningEngines", [])

    # 按前缀过滤
    if prefix:
        engines = [e for e in engines if e["name"].split("/")[-1].startswith(prefix)]

    # 按创建时间排序（新的在前）
    engines_sorted = sorted(
        engines,
        key=lambda x: parse_google_datetime(x["createTime"]),
        reverse=True,
    )
    return engines_sorted


def delete_reasoning_engine(session, project_id, location, re_name, token):
    """删除单个 Reasoning Engine"""
    url = f"https://{location}-aiplatform.googleapis.com/v1/{re_name}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project_id,
    }

    response = session.delete(url, headers=headers)
    return response.status_code in (200, 204, 404)


def check_engine_usage(engine):
    """检查 Reasoning Engine 是否可能正在使用中"""
    markers = []
    
    # 1. 检查是否有更新时间与创建时间不同（可能被使用过）
    create_time = parse_google_datetime(engine["createTime"])
    if "updateTime" in engine:
        update_time = parse_google_datetime(engine["updateTime"])
        if (update_time - create_time).total_seconds() > 300:  # 超过5分钟有更新
            markers.append("⚠️  有更新记录")
    
    # 2. 检查是否有自定义 displayName（可能被标记为重要）
    if "displayName" in engine and engine["displayName"]:
        markers.append(f"📛 显示名称: {engine['displayName']}")
    
    # 3. 检查是否是最近创建的（7天内）
    now = datetime.now(UTC)
    age_days = (now - create_time).days
    if age_days <= 7:
        markers.append("🆕 最近创建 (7天内)")
    
    return markers


def display_engine(engine, index):
    """显示单个 engine 信息"""
    short_name = engine["name"].split("/")[-1]
    create_time = engine["createTime"].replace("T", " ").replace("Z", "")[:19]
    
    markers = check_engine_usage(engine)
    
    print(f"\n  [{index}] {short_name}")
    print(f"      创建时间: {create_time}")
    if "displayName" in engine:
        print(f"      显示名称: {engine['displayName']}")
    if markers:
        print(f"      状态标记:")
        for marker in markers:
            print(f"        {marker}")


def create_session():
    """创建带有重试机制的 requests session"""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    
    session = requests.Session()
    # 禁用代理
    session.trust_env = False
    
    # 配置重试
    retry_strategy = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "DELETE"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def main():
    # 不使用代理 - Google Cloud API 不需要
    if "http_proxy" in os.environ:
        del os.environ["http_proxy"]
    if "https_proxy" in os.environ:
        del os.environ["https_proxy"]

    # 配置
    PROJECT_ID = "webeye-internal-test"
    LOCATION = "us-central1"
    PAGE_SIZE = 10

    print("=" * 70)
    print("🧹  安全批量清理 Vertex AI Reasoning Engine")
    print("=" * 70)

    # 1. 获取过滤前缀
    print("\n📝 请输入要删除的 Reasoning Engine 前缀（留空则显示所有）:")
    prefix = input("   前缀（例如: 7216）: ").strip()

    # 2. 创建 session 和获取 token
    session = create_session()
    token = get_gcloud_token()

    # 3. 获取并显示列表
    print(f"\n📋 正在获取 {LOCATION} 的 Reasoning Engine 列表...")
    engines = list_reasoning_engines(session, PROJECT_ID, LOCATION, token, prefix)

    if not engines:
        print("✅ 没有找到匹配的 Reasoning Engine")
        return

    print(f"\n📊 找到 {len(engines)} 个匹配的 Reasoning Engine\n")

    # 4. 分页处理
    deleted_count = 0
    skipped_count = 0
    i = 0

    while i < len(engines):
        # 显示当前页
        page_end = min(i + PAGE_SIZE, len(engines))
        print("-" * 70)
        print(f"\n📄 显示第 {i+1}-{page_end} 个（共 {len(engines)} 个）:\n")

        for j in range(i, page_end):
            display_engine(engines[j], j + 1)

        # 询问操作
        print("\n" + "-" * 70)
        print("\n🔧 操作选项:")
        print("   d - 删除当前页所有")
        print("   i - 逐个交互确认")
        print("   s - 跳过当前页")
        print("   q - 退出")
        choice = input("\n请选择操作 (d/i/s/q): ").strip().lower()

        if choice == "q":
            print("\n👋 退出")
            break
        elif choice == "s":
            skipped_count += (page_end - i)
            print(f"\n⏭️  跳过了 {page_end - i} 个")
            i = page_end
            continue
        elif choice == "d":
            confirm = input(f"\n⚠️  确定要删除当前页 {page_end - i} 个吗? (yes/no): ").strip().lower()
            if confirm != "yes":
                print("❌ 取消")
                continue
            print(f"\n🚀 开始删除...")
            for j in range(i, page_end):
                engine = engines[j]
                short_name = engine["name"].split("/")[-1]
                print(f"   删除: {short_name}...", end="")
                if delete_reasoning_engine(session, PROJECT_ID, LOCATION, engine["name"], token):
                    deleted_count += 1
                    print(" ✅")
                else:
                    print(" ❌")
            i = page_end
        elif choice == "i":
            for j in range(i, page_end):
                engine = engines[j]
                short_name = engine["name"].split("/")[-1]
                markers = check_engine_usage(engine)
                print(f"\n---")
                display_engine(engine, j + 1)
                
                prompt = f"\n   删除 {short_name}?"
                if markers:
                    prompt += " ⚠️  (有状态标记，请谨慎)"
                prompt += " (y=删除, s=跳过, q=退出): "
                action = input(prompt).strip().lower()
                if action == "q":
                    print("\n👋 退出")
                    i = len(engines)
                    break
                elif action == "s":
                    skipped_count += 1
                    print(f"   ⏭️  跳过")
                    continue
                elif action == "y":
                    print(f"   删除: {short_name}...", end="")
                    if delete_reasoning_engine(session, PROJECT_ID, LOCATION, engine["name"], token):
                        deleted_count += 1
                        print(" ✅")
                    else:
                        print(" ❌")
            i = page_end
        else:
            print("❌ 无效选项")

    # 总结
    print("\n" + "=" * 70)
    print("🎉  清理完成")
    print(f"   删除: {deleted_count} 个")
    print(f"   跳过: {skipped_count} 个")
    print(f"   剩余: {len(engines) - deleted_count} 个")
    print("=" * 70)


if __name__ == "__main__":
    main()
