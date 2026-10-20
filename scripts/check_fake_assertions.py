#!/usr/bin/env python3
"""CI 检查脚本：测试卫生两条规则（扫描 features/steps/ 与 tests/）。

规则 A｜假绿断言：禁止无注释的 `assert True` / `assert False`
  behave 不支持 pytest.skip，前端交互步骤保留 assert True 但必须有注释说明原因。
  L2-028：覆盖范围扩展——features/steps/（BDD 步骤）与 tests/（pytest）均扫描；
  pytest 侧同样禁止裸 assert True/assert False（恒真/恒假无业务意义）。

规则 B｜时间炸弹（2026-09-20 增补，源自当日真实事故）：
  测试 payload 里给 `start_at` / `end_at` / `enroll_deadline` / `publish_at` /
  `deadline` / `expire_at` 写**绝对时间字面量**且落在"近期未来"（当年 ~ 2049）→ 到点即失效。
  实例：`test_p0_fix20_b` / `test_p0_fix21` 写死 `"2026-09-20T15:00:00"`（本意"明天"），
  2026-09-20 15:19 门禁实测 4 连红「开始时间必须在未来」——**测试自己过期了**。
  允许两种写法：
    ① 相对时间：`datetime.now() + timedelta(days=7)`（推荐）；
    ② 远期哨兵：年份 ≥ 2050（如 `2099-01-01T10:00:00`），永不失效。
  改本清单记得同步本 docstring（红线 31：检查器自述与实现必须一致）。
"""

import datetime
import pathlib
import re
import sys
import tempfile

SCAN_DIRS = ("features/steps", "tests")

#: 规则 B：键名 + 字符串字面量形式的时间（如 `"start_at": "2026-09-20T15:00:00"`）
TIME_BOMB_KEYS = (
    "start_at",
    "end_at",
    "enroll_deadline",
    "publish_at",
    "deadline",
    "expire_at",
)
_KEYS_ALT = "|".join(TIME_BOMB_KEYS)
TIME_BOMB_RE = re.compile(
    rf"""["'](?P<key>{_KEYS_ALT})["']\s*[:=]\s*["'](?P<date>(\d{{4}})-\d{{2}}-\d{{2}}T)"""
)
#: 年份 ≥ 该值视为"远期哨兵"（永不到期），放行
FAR_FUTURE_YEAR = 2050


def scan(root: pathlib.Path, now_year: int | None = None) -> tuple[list[str], list[str]]:
    """扫描 `root/{features/steps,tests}`，返回 (假绿断言, 时间炸弹)。

    **参数化 root 是为了自证**（第八轮审查 P1-10）：原实现是模块级 for 循环直接扫仓库，
    没有任何办法给它喂样本 → 检测器改坏了也没人知道。
    """
    year_now = datetime.date.today().year if now_year is None else now_year
    violations: list[str] = []
    bombs: list[str] = []
    for scan_dir in SCAN_DIRS:
        for py_file in (root / scan_dir).rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # 规则 A：assert True/False 必须有注释说明原因
                if stripped in ("assert True", "assert False"):
                    violations.append(f"{py_file}:{i}: bare {stripped} (no comment)")
                elif (
                    (stripped.startswith("assert True") or stripped.startswith("assert False"))
                    and "#" not in stripped
                    and '"' not in stripped
                    and "'" not in stripped
                ):
                    violations.append(
                        f"{py_file}:{i}: {stripped.split('(')[0]} without explanation"
                    )
                # 规则 B：时间炸弹
                for m in TIME_BOMB_RE.finditer(line):
                    year = int(m.group("date")[:4])
                    if year_now <= year < FAR_FUTURE_YEAR:
                        bombs.append(
                            f"{py_file}:{i}: {m.group('key')} 写死 {m.group('date')}…"
                            f"（{year} 年会到期；改相对时间或 ≥{FAR_FUTURE_YEAR} 的远期哨兵）"
                        )
    return violations, bombs


def self_test() -> int:
    """S1 注入自检：两类违例必须命中，两种合法写法不得误报。

    防的是什么（第八轮审查 P1-10）：检查器"看起来在跑"但检测逻辑被改坏时，
    门禁照样打印 OK —— 自证样本是唯一能发现这件事的机制。
    """
    bad = 0
    with tempfile.TemporaryDirectory(prefix="fake-assert-selftest-") as d:
        root = pathlib.Path(d)
        (root / "tests").mkdir(parents=True)
        (root / "features/steps").mkdir(parents=True)
        (root / "tests/probe.py").write_text(
            "assert True\n"  # 违例：裸 assert True
            "assert False\n"  # 违例：裸 assert False
            "assert x == 1, '业务说明'\n"  # 合法：正常断言
            "assert True  # 前端交互步骤无服务端断言\n"  # 合法：带注释
            '{"start_at": "2026-11-01T10:00:00"}\n'  # 违例：近期未来时间炸弹
            '{"start_at": "2099-01-01T10:00:00"}\n'  # 合法：远期哨兵
            '{"deadline": datetime.now() + timedelta(days=7)}\n',  # 合法：相对时间
            encoding="utf-8",
        )
        (root / "features/steps/s.py").write_text("assert True\n", encoding="utf-8")
        violations, bombs = scan(root, now_year=2026)
        if len(violations) != 3:
            print(f"✗ S1 假绿断言注入未全命中（期望 3，实得 {len(violations)}）: {violations}")
            bad += 1
        if len(bombs) != 1:
            print(f"✗ S1 时间炸弹注入未命中（期望 1，实得 {len(bombs)}）: {bombs}")
            bad += 1
        # S2 空扫自检：扫描面塌缩（目录改名）时必须能看出来——样本在就该扫到
        if not violations or not bombs:
            print("✗ S2 扫描面异常（注入样本一个都没扫到）")
            bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print("✓ S1 注入自检通过（裸 assert ×3 全命中 / 时间炸弹命中 / 合法写法零误报）")
    return 0


def main() -> int:
    violations, bombs = scan(pathlib.Path.cwd())
    failed = False
    if violations:
        print(f"FOUND {len(violations)} unexplained assert True/False:")
        for v in violations:
            print(f"  {v}")
        failed = True
    if bombs:
        print(f"FOUND {len(bombs)} 会过期的时间字面量（时间炸弹）：")
        for b in bombs:
            print(f"  {b}")
        failed = True
    if failed:
        return 1
    print("OK: 假绿断言 0 处；时间炸弹 0 处（相对时间或远期哨兵 ≥2050）")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
