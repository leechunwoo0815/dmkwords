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

violations: list[str] = []
bombs: list[str] = []
now_year = datetime.date.today().year

for scan_dir in SCAN_DIRS:
    for py_file in pathlib.Path(scan_dir).rglob("*.py"):
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
                violations.append(f"{py_file}:{i}: {stripped.split('(')[0]} without explanation")
            # 规则 B：时间炸弹
            for m in TIME_BOMB_RE.finditer(line):
                year = int(m.group("date")[:4])
                if now_year <= year < FAR_FUTURE_YEAR:
                    bombs.append(
                        f"{py_file}:{i}: {m.group('key')} 写死 {m.group('date')}…"
                        f"（{year} 年会到期；改相对时间或 ≥{FAR_FUTURE_YEAR} 的远期哨兵）"
                    )

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
    sys.exit(1)
print("OK: 假绿断言 0 处；时间炸弹 0 处（相对时间或远期哨兵 ≥2050）")
