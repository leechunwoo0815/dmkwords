"""演示现场基线核验（`docs/19` 第 4 步 ①「数据回来了」的机械化）。

**为什么需要它**：门禁（pytest / behave）与 dev 后端**共库**，跑完门禁后库里会留下最后一批
场景数据。实测（2026-09-16）：`behave` 的 `features/wm3_member_edit.feature` 留下
`学籍家长B` / `学籍孩B` → 直接 COUNT 出**家长 4 / 孩子 12**，而干净基线是 **3 / 11**。
把脏读数当现场事实，就会得出"文档里的基线过期了"的错误结论（E-20260916-41 就是这么发生的）。

**本脚本做的事**（不是猜，是执行 `docs/19` 的规定动作）：

1. 清场：`dev.sh stop` → `pytest tests/unit/test_p0_t8_rate_limit.py`（该文件只做失败登录、
   不造业务数据，但 conftest 会在每个测试开始前 TRUNCATE 业务表 → 残留一并清掉）
2. `dev.sh restart`（MySQL → 迁移 → 双 seed → 后端 → 前端；**注释见 docs/19 §一 第 2 步**）
3. 计数四张核心业务表（`is_deleted=0`）并与基线比对

用法::

    python -m scripts.check_demo_baseline              # 清场 + restart + 计数 + 比对（标准动线）
    python -m scripts.check_demo_baseline --no-clean   # 只计数比对（调试用，读数可能是脏的）
    python -m scripts.check_demo_baseline --count-only # 只打印计数，不判 PASS/FAIL

退出码：0 = 与基线一致；1 = 不一致（按 `docs/19` 第 4 步"任何一项不过：回第 2 步"处理）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 干净基线（`docs/19` §一 第 4 步）：双 seed 定义态的实测值 = 书目 35 / 家长 3 / 孩子 11 / 活动 2。
# 口径变更（种子新增演示数据）时必须**重新实测并同步 docs/19**，别在别处手抄这套数字。
CLEAN_BASELINE = {
    "books": 35,
    "parents": 3,
    "children": 11,
    "activities": 2,
}

CLEANUP_TEST = "tests/unit/test_p0_t8_rate_limit.py"


def _run(cmd: list[str], check: bool = True) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT)
    if check and proc.returncode != 0:
        print(f"✗ 命令失败（退出码 {proc.returncode}）：{' '.join(cmd)}", file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.returncode


def count_rows() -> dict[str, int]:
    from backend.database import SessionLocal
    from backend.domain.activity.models import Activity
    from backend.domain.catalog.models import Book
    from backend.domain.identity.models import Child, Parent

    db = SessionLocal()
    try:
        return {
            "books": db.query(Book).filter(Book.is_deleted == 0).count(),
            "parents": db.query(Parent).filter(Parent.is_deleted == 0).count(),
            "children": db.query(Child).filter(Child.is_deleted == 0).count(),
            "activities": db.query(Activity).filter(Activity.is_deleted == 0).count(),
        }
    finally:
        db.close()


def clean_and_restore() -> None:
    _run(["bash", "scripts/dev.sh", "stop"])
    _run([".venv/bin/python", "-m", "pytest", CLEANUP_TEST, "-q"])
    _run(["bash", "scripts/dev.sh", "restart"])


def main() -> int:
    parser = argparse.ArgumentParser(description="演示现场基线核验（docs/19 第 4 步 ①）")
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="跳过清场（读数可能是门禁残留，仅调试用）",
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="只打印计数，不判 PASS/FAIL",
    )
    args = parser.parse_args()

    if not args.no_clean:
        print("=== 清场（docs/19 规定动作：pytest 清库 + restart 双 seed）===")
        clean_and_restore()
    else:
        print("=== 跳过清场（--no-clean）——读数可能是门禁残留 ===")

    actual = count_rows()
    print("\n=== 演示现场计数 ===")
    for key, expected in CLEAN_BASELINE.items():
        got = actual.get(key)
        flag = "" if got == expected else f"  ← 期望 {expected}"
        print(f"  {key:12s} {got}{flag}")

    if args.count_only:
        return 0

    drifted = {k: (actual.get(k), v) for k, v in CLEAN_BASELINE.items() if actual.get(k) != v}
    if not drifted:
        print("\n现场基线 PASS：书目 35 / 家长 3 / 孩子 11 / 活动 2（与 docs/19 一致）")
        return 0

    print("\n现场基线 FAIL：", file=sys.stderr)
    for key, (got, expected) in drifted.items():
        print(f"  {key}: 实测 {got} / 期望 {expected}", file=sys.stderr)
    if args.no_clean:
        print(
            "提示：本次用了 --no-clean，读数可能只是门禁残留（behave/pytest 各留一批）。"
            "\n      先去掉 --no-clean 按 docs/19 清场再判，再决定是不是真的丢了数据。",
            file=sys.stderr,
        )
    else:
        print(
            "已按 docs/19 清场仍不一致 → 数据真的少了，回 docs/19 §一 第 2 步重跑 restart；\n"
            "两轮不过上报，别改文档数字去迁就现场。",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
