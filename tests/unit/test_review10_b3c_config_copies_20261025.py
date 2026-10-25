# tests/unit/test_review10_b3c_config_copies_20261025.py — 第十轮整改 B3（余）：W2-D7 配置默认值副本守卫
"""`W2-D7`：同一个配置键的默认值在**四处各写一份**——种子（`backend/seeds/seed_configs.py`）、
后端兜底（`ConfigService.get_value(key, "兜底")`）、小程序 FALLBACK（`miniapp/utils/configs.js`）、
管理端 `cfgNum(configs, key, 兜底)`（`admin-web/src/hooks/useConfigs.ts`）。

现状是零漂移（专家第十轮扫描结论，本判据首跑复核为 **0 处不一致 / 0 个孤儿键**），
但**没有任何东西在维持它**：改种子忘改兜底，家长页在"配置接口挂掉"时会用旧数字渲染
（真金白银类文案尤其危险）。本判据把"四处一致"变成机器判据。

RED：把任一处数字改掉（例如 `miniapp/utils/configs.js` 的 `reservation_hours: 72` → `48`）即红。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _seed_defaults() -> dict[str, str]:
    """种子里的默认值：`("key", "value", "type", "group", "name", "desc")` 六元组。"""
    src = (ROOT / "backend/seeds/seed_configs.py").read_text(encoding="utf-8")
    return {m.group(1): m.group(2) for m in re.finditer(r'\(\s*"([a-z_]+)"\s*,\s*"([^"]*)"', src)}


def _miniapp_fallbacks() -> dict[str, str]:
    src = (ROOT / "miniapp/utils/configs.js").read_text(encoding="utf-8")
    block = re.search(r"const FALLBACK = \{(.*?)\n\}", src, re.S)
    assert block, "小程序 FALLBACK 块没找到（解析面坏了）"
    out = {}
    for k, v in re.findall(r"(\w+):\s*([^,\n]+)", block.group(1)):
        out[k] = v.strip().strip("'\"")
    return out


def _backend_fallbacks() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in (ROOT / "backend").rglob("*.py"):
        src = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'get_value\(\s*"([a-z_]+)"\s*,\s*"([^"]*)"', src):
            out.setdefault(m.group(1), set()).add(m.group(2))
        for m in re.finditer(r'get_value\(\s*"([a-z_]+)"\s*,\s*([0-9.]+)', src):
            out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def _admin_fallbacks() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in (ROOT / "admin-web/src").rglob("*.ts*"):
        src = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'cfgNum\(\s*configs\s*,\s*"([a-z_]+)"\s*,\s*([0-9.]+)', src):
            out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def _norm(value: str) -> str:
    """归一化：数字按数值比（`"5"` == `5` == `5.0`），其余小写、去引号。"""
    v = str(value).strip().strip("'\"")
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f}"
    except ValueError:
        return v.lower()


def test_scan_surface_is_not_empty():
    """S2 式空结果自检：解析面坏了必须**判红**，不能"零命中=通过"。"""
    seed = _seed_defaults()
    mini = _miniapp_fallbacks()
    backend = _backend_fallbacks()
    admin = _admin_fallbacks()
    assert len(seed) >= 50, f"种子配置解析面异常：只有 {len(seed)} 个键"
    assert len(mini) >= 8, f"小程序 FALLBACK 解析面异常：只有 {len(mini)} 个键"
    assert len(backend) >= 10, f"后端兜底解析面异常：只有 {len(backend)} 个键"
    assert len(admin) >= 5, f"管理端 cfgNum 解析面异常：只有 {len(admin)} 个键"


def test_no_orphan_fallback_keys():
    """兜底键必须都在种子里——否则那个键在库里**根本不存在**，兜底只是自我安慰。"""
    seed = set(_seed_defaults())
    orphans = {
        "miniapp": sorted(k for k in _miniapp_fallbacks() if k not in seed),
        "backend": sorted(k for k in _backend_fallbacks() if k not in seed),
        "admin": sorted(k for k in _admin_fallbacks() if k not in seed),
    }
    bad = {src: keys for src, keys in orphans.items() if keys}
    assert bad == {}, f"这些兜底键在种子里不存在：{bad}"


def test_all_copies_agree():
    """同一键在四处副本里的默认值必须一致（归一化后比较）。"""
    seed = _seed_defaults()
    mini = _miniapp_fallbacks()
    backend = _backend_fallbacks()
    admin = _admin_fallbacks()

    mismatches = []
    for key in sorted(set(seed) | set(mini) | set(backend) | set(admin)):
        copies: dict[str, str] = {}
        if key in seed:
            copies["seed"] = _norm(seed[key])
        if key in mini:
            copies["miniapp"] = _norm(mini[key])
        if key in backend:
            copies["backend"] = " / ".join(sorted(_norm(v) for v in backend[key]))
        if key in admin:
            copies["admin"] = " / ".join(sorted(_norm(v) for v in admin[key]))
        if len(set(copies.values())) > 1:
            mismatches.append(f"{key}: {copies}")
    assert mismatches == [], "配置默认值四处副本不一致：\n  " + "\n  ".join(mismatches)
