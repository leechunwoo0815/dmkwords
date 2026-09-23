# backend/common/media_paths.py — uploads 路径形状口径（唯一来源）
"""哪些目录能清、哪些绝不能碰、回收站长什么样——**只涉及路径形状，不查 DB**。

拆到 common 的原因：判"这个路径能不能动"有两个调用方分属不同域——
`backend/domain/admin/media_service.py`（盘点/清理）与 `backend/domain/catalog/router.py`
（`/uploads/{path}` 不下发回收站内容）；而架构关禁止业务域反向依赖 admin。
于是路径口径下沉到 common，**引用集**（需要业务模型的那些）留在 admin 域。
三方共用同一份常量：新增目录类别时只改这里（红线 51：同一口径只能有一份来源）。
"""

from __future__ import annotations

#: 可再生目录（白名单）：内容可由程序重新生成，允许清理其中的孤儿。**白名单外一律不动**
REGENERABLE_PREFIXES = ("cover/", "book_audio/", "circle/", "reports/")

#: 保护名单：人工上传、**不可再生**（即使不在引用集里也绝不清理）
PROTECTED_PREFIXES = (
    "miniapp-audit",
    "posters/",
    "voucher/",
    "observation/",
    "voice/",
    "activity_detail/",
)

#: 证据目录按**后缀**保护（真实目录名如 `wm15-samples/`，前缀匹配对它们无效——fix44 R3 实修的原坑）
PROTECTED_SUFFIX_DIRS = ("-samples",)

#: 回收站目录名（uploads 根下）：不进盘点、不被 `/uploads/{path}` 下发、不在任何清理白名单里
TRASH_DIRNAME = ".trash"


def norm_rel(rel: str) -> str:
    """归一相对路径：去空白、统一分隔符、去前导 `/`（DB 里有写 `/uploads/x` 的历史形态）。"""
    return (rel or "").strip().replace("\\", "/").lstrip("/")


def is_trash_path(rel: str) -> bool:
    """是否落在回收站目录内（这类路径永远不允许再次"清理"，也不允许被下发）。"""
    return norm_rel(rel).split("/", 1)[0] == TRASH_DIRNAME


def is_protected(rel: str) -> bool:
    """保护目录判定（含后缀式证据目录）。"""
    rel = norm_rel(rel)
    if any(rel.startswith(p) for p in PROTECTED_PREFIXES):
        return True
    head = rel.split("/", 1)[0]
    return any(head.endswith(s) for s in PROTECTED_SUFFIX_DIRS)


def is_regenerable(rel: str) -> bool:
    """是否落在可再生白名单内（**唯一允许被清理的一类**）。"""
    return any(norm_rel(rel).startswith(p) for p in REGENERABLE_PREFIXES)
