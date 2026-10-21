#!/usr/bin/env python3
"""CI 检查脚本：测试卫生规则（扫描 features/** 与 tests/）。

规则 A｜假绿断言（**AST 判定**，2026-10-21 第八轮 P2-67 扩面）
  A1 常量断言：`assert True` / `assert False`——含**带消息**形式 `assert True, "x"`。
     behave 不支持 pytest.skip，前端交互步骤保留 assert True 但必须**同行注释**说明原因。
     为什么消息字符串不算"说明原因"：它在测试输出里长得像一条真实断言的标签，而注释不会
     （原实现见 `"` 或 `'` 就放行 → `assert True, "看起来在断言"` 一路绿灯）。
  A2 恒真比较：两侧同一表达式（`assert x == x`）或两侧都是字面量（`assert 1 == 1` /
     `assert "a" == "a"`）——断言与代码行为无关，永远绿（或永远红），是"看起来在断言"。
     只对**无副作用**的表达式判定（`assert f() == f()` 有副作用，不算恒真，放行）。
  两类都必须为 0；逃生口是同行注释（`assert True  # 前端交互步骤无服务端断言`）。

规则 B｜时间炸弹（2026-09-20 增补，源自当日真实事故；B2/B3 于 2026-10-21 P2-67 补面）
  测试 payload 里给 `start_at` / `end_at` / `enroll_deadline` / `publish_at` /
  `deadline` / `expire_at` 写**绝对时间**且落在"近期未来"（当年 ~ 2049）→ 到点即失效。
  实例：`test_p0_fix20_b` / `test_p0_fix21` 写死 `"2026-09-20T15:00:00"`（本意"明天"），
  2026-09-20 15:19 门禁实测 4 连红「开始时间必须在未来」——**测试自己过期了**。
  三种形态都要拦：
    B1 键名 + ISO 字符串字面量：`"start_at": "2026-09-20T15:00:00"`
    B2 **构造式**：`datetime(2026, 12, 1, 10, 0)` / `date(2026, 12, 1)`（原实现只认字符串，
       构造式写死近期未来年份一处都不拦——探针实测）
    B3 **`.feature` 文件**：场景表格里的 `| start_at | 2026-12-01T10:00:00 |`
       （原实现只 rglob `*.py`，.feature 整类不在扫描面）
  允许两种写法：
    ① 相对时间：`datetime.now() + timedelta(days=7)`（推荐）；
    ② 远期哨兵：年份 ≥ 2050（如 `2099-01-01T10:00:00`），永不失效；
    ③ 历史日期（年份 < 当年，如生日 `date(2020, 5, 1)`）不受影响——它不会"到点"。
  改本清单记得同步本 docstring（红线 31：检查器自述与实现必须一致）。
"""

import ast
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
#: B3：`.feature` 场景表格/步骤文本里的"键名 + 绝对日期"。
#: 分隔符只许**标点与空白**（`|`/`:`/`=`/引号/逗号/空格）——中文注释里
#: `# expire_at 口径（2026-10-13）` 这类不该被算成炸弹（当年号隔着汉字时不算相邻）。
FEATURE_BOMB_RE = re.compile(
    rf"""(?P<key>{_KEYS_ALT})[\s"'|:=,]{{0,6}}(?P<date>(\d{{4}})-\d{{2}}-\d{{2}})"""
)
#: 年份 ≥ 该值视为"远期哨兵"（永不到期），放行
FAR_FUTURE_YEAR = 2050
#: B2：写死近期未来年份的日期构造器（`datetime(2026, …)` / `date(2026, …)`）
DATE_CTOR_NAMES = ("datetime", "date")


def _is_pure(node: ast.AST) -> bool:
    """无副作用的表达式（纯取值）——只有两侧同形才算恒真比较。"""
    if isinstance(node, (ast.Constant, ast.Name, ast.Attribute, ast.Subscript)):
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_is_pure(e) for e in node.elts)
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, (ast.UAdd, ast.USub)) and _is_pure(node.operand)
    return False


def _assert_problem(node: ast.Assert, lines: list[str]) -> str | None:
    """规则 A 判定（**只在这一处**，自证与主流程共用）。"""
    test = node.test
    if isinstance(test, ast.Constant) and test.value in (True, False):
        line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
        after = line[line.find("assert") :] if "assert" in line else ""
        if "#" not in after:
            return f"常量断言 `assert {test.value}`（无同行注释说明原因）"
        return None
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        left, right = test.left, test.comparators[0]
        if not (_is_pure(left) and _is_pure(right)):
            return None  # 有副作用（如 `assert f() == f()`）→ 不是恒真
        if isinstance(left, ast.Constant) and isinstance(right, ast.Constant):
            return f"双常量比较（与代码行为无关）：{ast.unparse(test)}"
        if ast.dump(left) == ast.dump(right):
            return f"恒真比较（两侧同一表达式）：{ast.unparse(test)}"
    return None


def _near_future(d: datetime.date, now_date: datetime.date) -> bool:
    """是不是"会到点失效"的近期未来日期（今天及以后 ~ 2049）。

    判据为什么是"**不早于今天**"而不是"年份 = 当年"：写死一个**已经过去**的日期不会
    "到点失效"——纯函数输入永远成立、payload 校验当场就红（响亮失败，不是静默过期）。
    真正危险的是"今天跑还绿、明天跑就红"的那一类，即日期 ≥ 今天。年份只作上界（≥2050 是哨兵）。
    """
    return now_date <= d < datetime.date(FAR_FUTURE_YEAR, 1, 1)


def _ctor_date(node: ast.AST) -> datetime.date | None:
    """`datetime(2026, 12, 1, …)` / `date(2026, 12, 1)` → date（参数不全或非字面量则 None）。"""
    if not isinstance(node, ast.Call) or len(node.args) < 3:
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name not in DATE_CTOR_NAMES:
        return None
    parts = [a.value for a in node.args[:3] if isinstance(a, ast.Constant)]
    if len(parts) != 3 or not all(isinstance(x, int) for x in parts):
        return None
    try:
        return datetime.date(*parts)
    except ValueError:
        return None


def _keyed_bomb(key: str, value: ast.AST, now_date: datetime.date) -> str | None:
    """规则 B2：**payload 键位**上的日期构造式（`{"start_at": datetime(2026, 12, 1)}`）。

    为什么要限定在 TIME_BOMB_KEYS 的键位：写死日期本身不是问题——`bill_date=date(2026, 10, 1)`
    是**历史账单日**、`cron_due_today("0 8 * * *", datetime(2026, 9, 24, 9, 44))` 是**纯函数输入**，
    它们永不失效（探针实测：不限键位会对全仓 21 处这类合法写法误报）。
    会到点失效的只有"业务语义要求它必须在未来"的那几个键。
    """
    if key not in TIME_BOMB_KEYS:
        return None
    d = _ctor_date(value)
    if d is not None and _near_future(d, now_date):
        return (
            f"{key} 写死 {d.isoformat()}（构造式，今天及以后会到期）"
            f"——改相对时间或 ≥{FAR_FUTURE_YEAR} 的远期哨兵"
        )
    return None


def _bomb_from_string(key: str, date_text: str, now_date: datetime.date, where: str) -> str | None:
    try:
        d = datetime.date.fromisoformat(date_text[:10])
    except ValueError:
        return None
    if _near_future(d, now_date):
        return (
            f"{where}: {key} 写死 {date_text}…（今天及以后会到期）"
            f"——改相对时间或 ≥{FAR_FUTURE_YEAR} 的远期哨兵"
        )
    return None


def scan(root: pathlib.Path, now_date: datetime.date | None = None) -> tuple[list[str], list[str]]:
    """扫描 `root/{features/steps,tests}` 与 `root/features/**/*.feature`。

    返回 (假绿断言, 时间炸弹)。**参数化 root 是为了自证**（第八轮审查 P1-10）：
    原实现是模块级 for 循环直接扫仓库，没有任何办法给它喂样本 → 检测器改坏了也没人知道。
    """
    today = datetime.date.today() if now_date is None else now_date
    violations: list[str] = []
    bombs: list[str] = []
    for scan_dir in SCAN_DIRS:
        for py_file in (root / scan_dir).rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            lines = text.splitlines()
            # 规则 A（AST：常量断言 + 恒真比较）与规则 B2（键位上的日期构造式）
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assert):
                        problem = _assert_problem(node, lines)
                        if problem:
                            violations.append(f"{py_file}:{node.lineno}: {problem}")
                    elif isinstance(node, ast.Dict):
                        for k, v in zip(node.keys, node.values, strict=False):
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                bomb = _keyed_bomb(k.value, v, today)
                                if bomb:
                                    bombs.append(f"{py_file}:{node.lineno}: {bomb}")
                    elif isinstance(node, ast.keyword) and node.arg:
                        bomb = _keyed_bomb(node.arg, node.value, today)
                        if bomb:
                            bombs.append(f"{py_file}:{node.lineno}: {bomb}")
            # 规则 B1（键名 + ISO 字符串字面量）
            for i, line in enumerate(lines, 1):
                for m in TIME_BOMB_RE.finditer(line):
                    bomb = _bomb_from_string(
                        m.group("key"), m.group("date"), today, f"{py_file}:{i}"
                    )
                    if bomb:
                        bombs.append(bomb)
    # 规则 B3：.feature 文件（原实现整类漏检——只 rglob 了 *.py）
    features_dir = root / "features"
    if features_dir.is_dir():
        for feat in sorted(features_dir.rglob("*.feature")):
            for i, line in enumerate(feat.read_text(encoding="utf-8").splitlines(), 1):
                for m in FEATURE_BOMB_RE.finditer(line):
                    bomb = _bomb_from_string(m.group("key"), m.group("date"), today, f"{feat}:{i}")
                    if bomb:
                        bombs.append(bomb)
    return violations, bombs


def self_test() -> int:
    """S1 注入自检：每条规则的违例必须命中，合法写法不得误报。

    防的是什么（第八轮审查 P1-10）：检查器"看起来在跑"但检测逻辑被改坏时，
    门禁照样打印 OK —— 自证样本是唯一能发现这件事的机制。
    P2-67 补面后，自证覆盖 A1（裸 + 带消息）/ A2（同形 + 双常量）/ B1 / B2 / B3，
    并对"同行注释""相对时间""远期哨兵""历史日期""有副作用的比较"五种合法写法零误报。
    """
    bad = 0
    expect_violations = 7  # A1 裸 ×2 + A1 带消息 ×1 + A2 同形 ×1 + A2 双常量 ×2 + steps 样本 ×1
    expect_bombs = 3  # B1 字符串 ×1 + B2 构造式 ×1 + B3 .feature ×1
    with tempfile.TemporaryDirectory(prefix="fake-assert-selftest-") as d:
        root = pathlib.Path(d)
        (root / "tests").mkdir(parents=True)
        (root / "features/steps").mkdir(parents=True)
        (root / "tests/probe.py").write_text(
            # ---- 规则 A 违例 ----
            "assert True\n"  # A1：裸 assert True
            "assert False\n"  # A1：裸 assert False
            'assert True, "看起来在断言"\n'  # A1：带消息也拦（消息不是"说明原因"）
            "assert x == x\n"  # A2：两侧同一表达式
            "assert 1 == 1\n"  # A2：双常量
            'assert "a" == "a"\n'  # A2：双常量（字符串）
            # ---- 规则 A 合法 ----
            "assert x == 1, '业务说明'\n"  # 正常断言
            "assert True  # 前端交互步骤无服务端断言\n"  # 逃生口：同行注释
            "assert r.status_code == 404\n"  # 正常断言（非双常量）
            "assert f() == f()\n"  # 有副作用 → 不算恒真
            # ---- 规则 B 违例 ----
            '{"start_at": "2026-11-01T10:00:00"}\n'  # B1：键位 + 近期未来字符串
            '{"start_at": datetime(2026, 12, 1, 10, 0)}\n'  # B2：键位 + 构造式近期未来
            # ---- 规则 B 合法 ----
            '{"start_at": "2099-01-01T10:00:00"}\n'  # 远期哨兵
            '{"deadline": datetime.now() + timedelta(days=7)}\n'  # 相对时间
            "child.birthday = date(2020, 5, 1)\n"  # 历史日期（不会到点）
            "expires_at = datetime(2099, 1, 1)\n"  # 远期哨兵（构造式）
            '{"start_at": "2026-01-01T10:00:00"}\n'  # 已过去的日期（当场就红，不是静默过期）
            "bill_date=date(2026, 10, 1)\n"  # B2 非键位：历史账单日（探针实测 21 处同款）
            'cron_due_today("0 8 * * *", datetime(2026, 9, 24, 9, 44))\n'  # B2 非键位：纯函数输入
            'cron_due_today("0 8 * * *", now=datetime(2026, 12, 1, 9, 44))\n'  # B2 非炸弹键位：
            #   近未来日期但键名不是 payload 键（纯函数的 now 注入点）→ 不得误报
            # ---- .feature 注释里的假阳性防呆：键名与年份隔着汉字 → 不算相邻 ----
            "# expire_at 口径（2026-10-13）与代码一致\n",
            encoding="utf-8",
        )
        (root / "features/steps/s.py").write_text("assert True\n", encoding="utf-8")
        (root / "features/bomb.feature").write_text(
            "  场景: 时间炸弹\n"
            "    假如 活动开始时间 start_at 2026-12-01T10:00:00\n"
            "    # 口径订正（2026-10-13）：注释里的年份不算炸弹\n",
            encoding="utf-8",
        )
        violations, bombs = scan(root, now_date=datetime.date(2026, 10, 21))
        if len(violations) != expect_violations:
            print(f"✗ S1 假绿断言注入未全命中（期望 {expect_violations}，实得 {len(violations)}）")
            for v in violations:
                print(f"    {v}")
            bad += 1
        if len(bombs) != expect_bombs:
            print(f"✗ S1 时间炸弹注入未全命中（期望 {expect_bombs}，实得 {len(bombs)}）")
            for b in bombs:
                print(f"    {b}")
            bad += 1
        # 逐条形态断言：漏了哪一类必须指名道姓（只看总数会把"多报一处、少报一处"当通过）
        joined_v = " | ".join(violations)
        joined_b = " | ".join(bombs)
        for want in ("常量断言", "恒真比较", "双常量比较"):
            if want not in joined_v:
                print(f"✗ S1 规则 A 缺形态：{want}")
                bad += 1
        for want in ("start_at", "构造式", "bomb.feature"):
            if want not in joined_b:
                print(f"✗ S1 规则 B 缺形态：{want}")
                bad += 1
        # 合法写法零误报（逐条点名，防止"误报被总数掩盖"）
        for legit in (
            "f() == f()",
            "2099",
            "birthday",
            "2026-01-01",
            "bill_date",
            "cron_due_today",
        ):
            if legit in joined_v or legit in joined_b:
                print(f"✗ S1 合法写法被误报：{legit}")
                bad += 1
        # S2 空扫自检：扫描面塌缩（目录改名）时必须能看出来——样本在就该扫到
        if not violations or not bombs:
            print("✗ S2 扫描面异常（注入样本一个都没扫到）")
            bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print(
        f"✓ S1 注入自检通过（假绿断言 {expect_violations} 类全命中 / 时间炸弹 {expect_bombs} 类全命中"
        " / 合法写法零误报）"
    )
    return 0


def main() -> int:
    violations, bombs = scan(pathlib.Path.cwd())
    failed = False
    if violations:
        print(f"FOUND {len(violations)} 处假绿断言（常量断言 / 恒真比较）：")
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
    print(
        "OK: 假绿断言 0 处（常量/恒真比较）；时间炸弹 0 处"
        f"（.py 字符串 + datetime 构造 + .feature 表格；相对时间或远期哨兵 ≥{FAR_FUTURE_YEAR}）"
    )
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
