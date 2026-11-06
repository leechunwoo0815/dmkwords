#!/usr/bin/env python3
"""验证前端 api.js 的路径与后端 router 路径一致

解析逻辑：
1. 从 api.js 中提取所有 req.get/post/put/del 调用的路径
2. 从后端 router.py 中提取所有 @router.get/post/put/delete 装饰器路径
3. 比对差异（前端调用的路径必须在后端存在）
"""

import pathlib
import re
import sys


def parse_frontend_apis(api_js_path: str) -> dict[str, list[str]]:
    """返回 {method: [path, ...]}"""
    content = pathlib.Path(api_js_path).read_text(encoding="utf-8")
    apis: dict[str, list[str]] = {"get": [], "post": [], "put": [], "del": []}

    # 匹配 req.get(`/path/...`) 等
    pattern = r'req\.(get|post|put|del)\(\s*[`\'"](/[^`\'"]*?)[`\'"]'
    for match in re.finditer(pattern, content):
        method, path = match.groups()
        # 替换模板变量为 {param}
        path = re.sub(r"\$\{[^}]+\}", "{param}", path)
        # 去除查询字符串
        path = path.split("?")[0]
        if path.startswith("/"):
            apis[method].append(path.rstrip("/") or "/")

    return apis


def parse_backend_routes(backend_dir: str) -> dict[str, list[str]]:
    """返回 {method: [full_path, ...]}"""
    routes: dict[str, list[str]] = {"get": [], "post": [], "put": [], "del": []}
    method_map = {"get": "get", "post": "post", "put": "put", "delete": "del"}

    for router_file in pathlib.Path(backend_dir).rglob("*router.py"):
        content = router_file.read_text(encoding="utf-8")

        # 提取所有 router prefix（包括 fav_router 等别名）
        prefixes = re.findall(r'APIRouter\(prefix=["\'](.*?)["\']', content)

        for match in re.finditer(r'@router\.(get|post|put|delete)\(\s*["\'](.*?)["\']', content):
            method, path = match.groups()
            for prefix in prefixes:
                full_path = prefix + path
                full_path = re.sub(r"\{(\w+)\}", "{param}", full_path)
                method_key = method_map.get(method, method)
                if full_path not in routes[method_key]:
                    routes[method_key].append(full_path)

        # 也匹配 @fav_router 等别名
        for alias_match in re.finditer(
            r'@(\w+_router)\.(get|post|put|delete)\(\s*["\'](.*?)["\']', content
        ):
            _, method, path = alias_match.groups()
            for prefix in prefixes:
                full_path = prefix + path
                full_path = re.sub(r"\{(\w+)\}", "{param}", full_path)
                method_key = method_map.get(method, method)
                if full_path not in routes[method_key]:
                    routes[method_key].append(full_path)

    return routes


def main():
    frontend_apis = parse_frontend_apis("frontend/utils/api.js")
    backend_routes = parse_backend_routes("backend/domain")

    mismatches = []

    for method in ["get", "post", "put", "del"]:
        for path in frontend_apis[method]:
            matched = False
            for bpath in backend_routes.get(method, []):
                if path == bpath or path.rstrip("/") == bpath.rstrip("/"):
                    matched = True
                    break
                fe_pattern = re.sub(r"\{param\}", "[^/]+", re.escape(path))
                if re.fullmatch(fe_pattern, bpath):
                    matched = True
                    break
            if not matched:
                mismatches.append(f"Frontend {method.upper()} {path} has no matching backend route")

    if mismatches:
        print(f"FOUND {len(mismatches)} API contract mismatches:")
        for m in mismatches:
            print(f"  {m}")
        sys.exit(1)
    print("OK: API contract verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
