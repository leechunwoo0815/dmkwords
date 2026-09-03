# scripts/export_openapi.py — OpenAPI 契约快照导出/校验（T27/S1）
"""用法：
  python scripts/export_openapi.py            # 导出快照 → docs/api/openapi.json
  python scripts/export_openapi.py --check    # 重新生成与快照 diff，不一致 exit 1 + 变更清单

效果（gate [8] 步骤调用 --check）：任何端点删改/字段类型变更/响应模型变化 →
gate 红 + 变更清单显形——破坏性变更必须"改代码 + 更新快照"两步走，
快照更新单独 commit（message 注明 contract-change: 前缀 + 变更端点列表）。
排序键稳定化（sort_keys）保证幂等输出。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SNAPSHOT = ROOT / "docs" / "api" / "openapi.json"


def export() -> dict:
    from backend.main import app

    return app.openapi()


def diff_summary(old: dict, new: dict) -> list[str]:
    """变更端点清单（paths 级 diff 概览 + schemas 汇总）。"""
    changes: list[str] = []
    old_paths = old.get("paths", {})
    new_paths = new.get("paths", {})
    for p in sorted(set(old_paths) | set(new_paths)):
        if p not in old_paths:
            changes.append(f"  + 端点新增 {p}")
        elif p not in new_paths:
            changes.append(f"  - 端点删除 {p}")
        else:
            for method in sorted(set(old_paths[p]) | set(new_paths[p])):
                if old_paths[p].get(method) != new_paths[p].get(method):
                    changes.append(f"  ~ 变更 {method.upper()} {p}")
    old_comp = old.get("components", {}).get("schemas", {})
    new_comp = new.get("components", {}).get("schemas", {})
    added = sorted(set(new_comp) - set(old_comp))
    removed = sorted(set(old_comp) - set(new_comp))
    changed = sorted(k for k in set(old_comp) & set(new_comp) if old_comp[k] != new_comp[k])
    if added:
        changes.append(f"  + schemas 新增 {len(added)}: {', '.join(added[:8])}")
    if removed:
        changes.append(f"  - schemas 删除 {len(removed)}: {', '.join(removed[:8])}")
    if changed:
        changes.append(f"  ~ schemas 变更 {len(changed)}: {', '.join(changed[:8])}")
    return changes


def main() -> int:
    current = export()
    if "--check" in sys.argv:
        if not SNAPSHOT.exists():
            print(f"✗ 契约快照缺失: {SNAPSHOT}（先跑 python scripts/export_openapi.py 导出首版）")
            return 1
        snapshot = json.loads(SNAPSHOT.read_text())
        if snapshot == current:
            print("[T27] 契约快照一致 ✓")
            return 0
        print("✗ OpenAPI 契约快照与代码不一致（破坏性变更必须两步显形：改代码 + 更新快照单独 commit）：")
        for line in diff_summary(snapshot, current):
            print(line)
        return 1
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    print(f"[T27] 契约快照已导出: {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
