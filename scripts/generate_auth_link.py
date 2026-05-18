import json
import os
from urllib.parse import quote, urlparse, parse_qs, urlencode

def generate_lark_auth_url():
    # 1. 尝试加载 .env 文件获取配置
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # 2. 读取权限配置文件
    scopes_file = os.path.join(os.path.dirname(__file__), 'lark_scopes.json')
    if not os.path.exists(scopes_file):
        print(f"Error: {scopes_file} not found.")
        return

    with open(scopes_file, 'r') as f:
        try:
            scopes_data = json.load(f)
        except json.JSONDecodeError:
            print(f"Error: Failed to parse {scopes_file}. Please check the JSON format.")
            return

    # 合并 user 和 tenant 权限（OAuth2 授权通常需要 user 权限）
    user_scopes = scopes_data.get('scopes', {}).get('user', [])
    tenant_scopes = scopes_data.get('scopes', {}).get('tenant', [])
    all_scopes = sorted(list(set(user_scopes) | set(tenant_scopes)))

    if not all_scopes:
        print("Error: No scopes found in lark_scopes.json.")
        return

    # 3. 获取关键配置参数
    client_id = os.getenv("LARK_CLIENT_ID")
    redirect_uri = os.getenv("LARK_REDIRECT_URI")
    
    # 如果没有显式的 LARK_CLIENT_ID，尝试从现有的 LARK_AUTHORIZATION_URI 中解析
    existing_uri = os.getenv("LARK_AUTHORIZATION_URI")
    if not client_id and existing_uri:
        parsed_uri = urlparse(existing_uri)
        query_params = parse_qs(parsed_uri.query)
        client_id = query_params.get('client_id', [None])[0]
        if not redirect_uri:
            redirect_uri = query_params.get('redirect_uri', [None])[0]

    if not client_id:
        print("Error: LARK_CLIENT_ID not found in .env file.")
        print("Please add 'LARK_CLIENT_ID=your_cli_id' to your .env file.")
        return

    # 4. 构造新的 URL
    base_url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": " ".join(all_scopes) # 注意：urlencode 会处理空格转义
    }
    
    if redirect_uri:
        params["redirect_uri"] = redirect_uri

    # 使用 urlencode 构造查询字符串
    # 飞书要求 scope 里的空格转义为 %20，urlencode 默认也是这样处理的
    query_string = urlencode(params, quote_via=quote)
    final_url = f"{base_url}?{query_string}"

    print("-" * 50)
    print("成功生成飞书授权链接！")
    print("-" * 50)
    print(f"\n{final_url}\n")
    print("-" * 50)
    print("操作建议：")
    print("1. 确保已在飞书开放平台后台勾选了 lark_scopes.json 中的所有权限并发布版本。")
    print("2. 将上述链接更新到 .env 文件的 LARK_AUTHORIZATION_URI 变量中。")
    print("3. 点击该链接重新进行授权以获取包含新权限的 Token。")
    print("-" * 50)

if __name__ == "__main__":
    generate_lark_auth_url()
