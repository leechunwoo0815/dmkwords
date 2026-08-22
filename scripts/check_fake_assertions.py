#!/usr/bin/env python3
"""CI 检查脚本：禁止无注释的 assert True/False 假绿断言

behave 不支持 pytest.skip，前端交互步骤保留 assert True 但必须有注释说明原因。
L2-028：覆盖范围扩展——features/steps/（BDD 步骤）与 tests/（pytest）均扫描；
pytest 侧同样禁止裸 assert True/assert False（恒真/恒假无业务意义）。
"""

import pathlib
import sys

SCAN_DIRS = ("features/steps", "tests")
violations = []
for scan_dir in SCAN_DIRS:
    for py_file in pathlib.Path(scan_dir).rglob("*.py"):
        for i, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # assert True/False 必须有注释说明原因
            if stripped in ("assert True", "assert False"):
                violations.append(f"{py_file}:{i}: bare {stripped} (no comment)")
            elif (
                (stripped.startswith("assert True") or stripped.startswith("assert False"))
                and "#" not in stripped
                and '"' not in stripped
                and "'" not in stripped
            ):
                violations.append(f"{py_file}:{i}: {stripped.split('(')[0]} without explanation")

if violations:
    print(f"FOUND {len(violations)} unexplained assert True/False:")
    for v in violations:
        print(f"  {v}")
    sys.exit(1)
print("OK: all assert True/False have explanations")
