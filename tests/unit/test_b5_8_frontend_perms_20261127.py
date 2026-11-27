# tests/unit/test_b5_8_frontend_perms_20261127.py — B5-8 路由权限元数据的机械判据
"""B5-8：`admin-web` 的路由权限码收敛成一张元数据表（`src/permissions.ts` 的 ROUTE_META）。

为什么必须机器判：权限码有三处消费者（侧边栏菜单 `pages/Layout.tsx`、路由守卫 `App.tsx`、
页内按钮 `hasPermission(...)`），靠人眼对齐必然漂移——漂移的两个方向都是线上事故：
  - 路由门挂了**不存在的码** → 该页对所有角色永远"无访问权限"（进去也白进）；
  - 路由门挂了**没人校验的码** → 假门（以为拦住了，实际直输 URL 照样能操作）。

唯一权威来源是后端 `require_perm(...)` 声明（宪法 §五.2，对账器 `scripts/check_rbac_consistency.py`）。
本文件因此只做机械比对，**不判断"这个页面该归哪个码"**（那是评审/甲方的语义决策）：

  ① 元数据里每个码都在后端已声明集合内（STAFF ∪ SUPER_ADMIN_ONLY）——编新码即判红；
  ② 每个码确实被至少一个后端 `require_perm(...)` 引用（防"声明了但没人校验"的假门）；
  ③ 元数据与 `App.tsx` 的路由**双向覆盖**（有页面没元数据 / 元数据指向不存在的路由，都判红）；
  ④ 菜单（Layout.tsx `rawItems`）与路由元数据同码（同一页面不许两套口径）；
  ⑤ `App.tsx` 里不许再出现内联权限码字面量（防散落写法回归）；
  ⑥ 抽取器自检：注入一个假码必须能被判出来（抽不出来 = 判据永远绿）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:  # `python -m pytest` 与 `pytest` 两种跑法都能 import scripts/backend
    sys.path.insert(0, str(ROOT))

PERMISSIONS_TS = ROOT / "admin-web/src/permissions.ts"
APP_TSX = ROOT / "admin-web/src/App.tsx"
LAYOUT_TSX = ROOT / "admin-web/src/pages/Layout.tsx"

# `{ path: "/books", perm: "book.manage" }`（允许换行/多空格；perm 键形与对账脚本抽取口径一致）
_META_ROW = re.compile(r'\{\s*path:\s*"([^"]+)",\s*perm:\s*"([A-Za-z_][A-Za-z_.]*)"\s*\}')
_ROUTE_PATH = re.compile(r'<Route\s+path="([^"]+)"')
_KEY = re.compile(r'key:\s*"([^"]+)"')
_PERM = re.compile(r'perm:\s*"([A-Za-z_][A-Za-z_.]*)"')
_STRING_LITERAL = re.compile(r'"([A-Za-z_][A-Za-z_.]*)"')

# 不做路由级拦截的路径：登录页（未登录入口）与 404 兜底重定向
UNGATED_PATHS = frozenset({"/login", "*"})


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _route_meta() -> dict[str, str]:
    """从 permissions.ts 抽出 路由 → 权限码（顺序表；重复键直接判红）。"""
    rows = _META_ROW.findall(_read(PERMISSIONS_TS))
    assert rows, "permissions.ts 里抽不出任何 路由→权限码（抽取器/表结构被改坏 = 判据恒绿）"
    meta: dict[str, str] = {}
    dup: list[str] = []
    for path, code in rows:
        if path in meta:
            dup.append(path)
        meta[path] = code
    assert not dup, f"ROUTE_META 里同一路由登记了多次（后者会静默覆盖前者）：{sorted(set(dup))}"
    return meta


def _app_route_keys() -> set[str]:
    """App.tsx 里所有页面路由的绝对路径（index 记作 "/"；登录页与 404 兜底排除）。"""
    src = _read(APP_TSX)
    keys: set[str] = set()
    for raw in _ROUTE_PATH.findall(src):
        if raw in UNGATED_PATHS:
            continue
        # 父级 Layout 路由写 "/"，子路由写相对路径（"books" / "books/:id"）——统一成绝对路径
        keys.add("/" + raw.lstrip("/") if raw != "/" else "/")
    if "<Route index" in src:
        keys.add("/")
    assert keys, "App.tsx 里抽不出任何路由（抽取器被改坏 = 判据恒绿）"
    return keys


def _menu_perms() -> dict[str, str]:
    """Layout.tsx 侧边栏 `rawItems` 的 菜单 key → perm（菜单是既有的前端判据，必须与路由同码）。"""
    src = _read(LAYOUT_TSX)
    start = src.index("const rawItems = [")
    block = src[start : src.index("\n  ];", start)]
    keys = [(m.group(1), m.start()) for m in _KEY.finditer(block)]
    out: dict[str, str] = {}
    for i, (key, pos) in enumerate(keys):
        end = keys[i + 1][1] if i + 1 < len(keys) else len(block)
        perm = _PERM.search(block, pos, end)
        if perm is not None:
            out[key] = perm.group(1)
    assert len(out) >= 10, f"Layout.tsx 菜单 perm 抽取过少（{len(out)} 项），抽取口径可能已漂移"
    return out


def _declared_codes() -> set[str]:
    """后端权限目录（与 check_rbac_consistency.main 同一事实源）。"""
    from backend.domain.admin.service import (
        STAFF_PERMISSIONS,
        SUPER_ADMIN_ONLY_PERMISSIONS,
    )

    return set(STAFF_PERMISSIONS) | set(SUPER_ADMIN_ONLY_PERMISSIONS)


def _backend_referenced_codes() -> set[str]:
    """后端 `require_perm(...)` 实际引用的码（复用对账脚本的解析结果，不另写一套正则）。"""
    from scripts.check_rbac_consistency import backend_used

    return set(backend_used(ROOT))


def _undeclared(meta: dict[str, str], declared: set[str]) -> list[str]:
    """返回元数据里后端**未声明**的码（排序后便于断言输出）。"""
    return sorted({code for code in meta.values() if code not in declared})


def test_route_perm_codes_are_declared_by_backend():
    """① 元数据里的每个权限码都必须是后端已声明的码——不允许编新码（编了就是幽灵权限）。"""
    meta = _route_meta()
    declared = _declared_codes()
    assert declared, "后端权限目录抽成空集（对账将恒绿）"
    offenders = _undeclared(meta, declared)
    assert not offenders, (
        f"路由元数据引用了后端未声明的权限码 {offenders}——"
        f"已声明集合 {sorted(declared)}；请改用既有码，或在后端登记（登记属 backend 变更，不在本批范围）"
    )


def test_route_perm_codes_are_enforced_somewhere_in_backend():
    """② 每个码都被至少一个后端 `require_perm(...)` 引用——否则是假门（能进页面，操作全 403 或更糟）。"""
    meta = _route_meta()
    referenced = _backend_referenced_codes()
    unenforced = sorted({code for code in meta.values() if code not in referenced})
    assert not unenforced, (
        f"路由元数据用了后端从没挂过守卫的码 {unenforced}——"
        f"这类码只会造成「以为拦住了」的假门；后端已引用 {sorted(referenced)}"
    )


def test_route_metadata_and_app_routes_cover_each_other():
    """③ 双向覆盖：页面路由都有元数据（缺口可见）+ 元数据只指向真实路由（防止陈旧行）。"""
    meta = _route_meta()
    routes = _app_route_keys()
    missing = sorted(routes - set(meta))
    phantom = sorted(set(meta) - routes)
    assert not missing, (
        f"这些页面路由没有权限元数据 {missing}——"
        f"要么在 permissions.ts 登记码（须先确认后端声明），要么像登录页那样显式排除"
    )
    assert not phantom, (
        f"路由元数据里这些路径在 App.tsx 里不存在（陈旧行，会导致门挂错页）：{phantom}"
    )


def test_menu_and_route_use_the_same_code():
    """④ 菜单与路由同码：同一页面不许出现"菜单看不见但 URL 能进"或反之的两套口径。"""
    meta = _route_meta()
    menu = _menu_perms()
    missing = sorted(set(menu) - set(meta))
    assert not missing, f"菜单项的页面没有路由元数据（会让 URL 直输绕过菜单拦截）：{missing}"
    drift = {key: (menu[key], meta[key]) for key in sorted(menu) if menu[key] != meta[key]}
    assert not drift, f"菜单与路由权限码不一致（菜单码, 路由码）：{drift}"


def test_no_inline_permission_codes_left_in_app():
    """⑤ App.tsx 不再出现内联权限码字面量——码只能来自 permissions.ts（B5-8 的收敛目标）。"""
    literals = set(_STRING_LITERAL.findall(_read(APP_TSX)))
    inline = sorted(literals & _declared_codes())
    assert not inline, f"App.tsx 又出现了内联权限码 {inline}——请写进 permissions.ts 的 ROUTE_META"


def test_extractor_has_teeth():
    """⑥ 判据自证：注入一个假码必须被判红（抽取为空/正则失效时本文件其余断言会恒绿）。"""
    meta = _route_meta()
    declared = _declared_codes()
    assert _undeclared(meta, declared) == [], "真实元数据本身应无未声明码"
    assert _undeclared({"/x": "ghost.front.code"}, declared) == ["ghost.front.code"], (
        "注入的假码没被识别——判据失效（正则被改坏就会这样）"
    )
