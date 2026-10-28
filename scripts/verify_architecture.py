# scripts/verify_architecture.py — 架构关（宪法第三节/第四节机械执法）
"""每条架构规则对应一个检查；退出码非 0 = 架构违规。
骨架期检查项：四件套齐全 / Router 违规 / 单文件行数 / SQLite 禁令 / import 白名单。
域就绪后逐步收紧：事件发布订阅对账、函数内 import 计数阈值。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DOMAINS = [
    "identity",
    "catalog",
    "circulation",
    "billing",
    "reading",
    "growth",
    "activity",
    "admin",
    "reading_circle",
]
FOUR_PIECES = ["models.py", "schemas.py", "repository.py", "service.py", "router.py"]

ROUTER_VIOLATIONS = [
    (re.compile(r"db\.(query|add|commit|delete)\b"), "Router 层出现 ORM 操作"),
    # P3 门禁族（2026-10-22 第一百一十四批）：补 `execute/get/scalar/flush`——
    # 原先只拦 query/add/commit/delete，`db.execute(text(...))` / `db.get(Model, id)` /
    # `db.flush()` 三种写法**整体逃逸**（当前仓库恰好零命中，属"红色消失的通道"型缺口）。
    (
        re.compile(r"\bdb\.(execute|get|scalar|flush)\b"),
        "Router 层出现 ORM 操作（execute/get/scalar/flush）",
    ),
    (re.compile(r"\btry\s*:"), "Router 层出现 try/except"),
    (re.compile(r"raise\s+HTTPException"), "Router 层抛 HTTPException（应在 Service）"),
]

# A-1/T6：架构门禁扩 miniapp_router（原只扫 router.py，5 个 miniapp_router 执法真空）
ROUTER_PIECES = ("router.py", "miniapp_router.py")

MAX_FILE_LINES = 800


def check_four_pieces(errors: list[str]) -> None:
    for domain in DOMAINS:
        dpath = BACKEND / "domain" / domain
        if not dpath.is_dir():
            errors.append(f"域缺失: {domain}")
            continue
        for piece in FOUR_PIECES:
            if not (dpath / piece).is_file():
                errors.append(f"{domain}/{piece} 缺失（四件套不齐）")


def check_router_violations(errors: list[str]) -> None:
    for domain in DOMAINS:
        for piece in ROUTER_PIECES:
            router = BACKEND / "domain" / domain / piece
            if not router.is_file():
                continue
            text = router.read_text(encoding="utf-8")
            for pattern, msg in ROUTER_VIOLATIONS:
                if pattern.search(text):
                    errors.append(f"{domain}/{piece}: {msg}")


def check_lock_populate(errors: list[str]) -> None:
    """B-16/T2 防复发：所有 with_for_update 必须链 populate_existing（identity map 陷阱）。
    支持同行或下一行补链（reading 既有跨行先例）。

    P3 门禁族（2026-10-22 第一百一十四批）：扫描面从 `service.py` 扩到 **backend 全量 .py**——
    原先只有 `service.py` 受管，而实测有 12 处锁定读散落在 `order_guards.py` / `refund_online.py` /
    `deposit_refund_guards.py` / `reservation_ops.py` / `cancel_flow.py` / `repository.py`
    （当时恰好都合规，属"换个人写就漏"的盲区）。
    """
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        lines = py.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if ".with_for_update()" not in line:
                continue
            if ".populate_existing()" in line:
                continue
            # 允许中间夹注释/空行（P3 第一百一十四批：加了说明注释后"下一行"判定失效）
            follow = [
                ln for ln in lines[i + 1 : i + 4] if ln.strip() and not ln.strip().startswith("#")
            ]
            if follow and ".populate_existing()" in follow[0]:
                continue
            # 逃生口：**裸列锁**不加载实体 → identity map 陷阱不适用（`query(Child.id)` 这类）。
            # 判据取同一条语句里的 `.query(...)` 参数是否含点号（跨行向上找 ≤12 行）。
            head = "\n".join(lines[max(0, i - 12) : i + 1])
            m = list(re.finditer(r"\.query\(\s*([^)\n]+)\)", head))
            if m and "." in m[-1].group(1):
                continue
            rel = py.relative_to(ROOT)
            errors.append(f"{rel}:{i + 1}: with_for_update 未链 populate_existing（B-16）")


def check_domain_list(errors: list[str]) -> None:
    """域列表**不得硬编码漂移**（P3 门禁族，2026-10-22 第一百一十四批）。

    为什么：`DOMAINS` 是字面量列表，四件套 / Router 违规 / import 白名单三条规则都只
    `for domain in DOMAINS` —— 新增一个域目录但忘了登记，那个域就**完全不受架构关管辖**
    （静默豁免）。判据：磁盘 `backend/domain/*` 子目录集合与 `DOMAINS` **互为子集**。
    """
    domain_root = BACKEND / "domain"
    if not domain_root.is_dir():
        return
    actual = {p.name for p in domain_root.iterdir() if p.is_dir() and not p.name.startswith("__")}
    declared = set(DOMAINS)
    for extra in sorted(actual - declared):
        errors.append(
            f"backend/domain/{extra} 未登记进 DOMAINS —— 新域不受四件套/Router/import 规则管辖"
        )
    for missing in sorted(declared - actual):
        errors.append(f"DOMAINS 声明的域 {missing} 在磁盘上不存在（backend/domain/{missing}）")


def check_file_length(errors: list[str]) -> None:
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        lines = len(py.read_text(encoding="utf-8").splitlines())
        if lines > MAX_FILE_LINES:
            errors.append(f"{py.relative_to(ROOT)}: {lines} 行 > {MAX_FILE_LINES}（god file 禁止）")


def check_sqlite_ban(errors: list[str]) -> None:
    """SQLite 禁令（宪法：MySQL-only）。

    P3 门禁族（2026-10-22 第一百一十四批）：扫描面原先只有 `backend/**` + `scripts/**`，
    **`alembic/**`（迁移）与 `tests/**`（用例）是真空**——迁移里写 SQLite DSN、或用例偷偷
    用 `sqlite://` 起引擎都不判红。现在：
      · `alembic/**`：与 backend/scripts 同档**严格**（出现 `sqlite` 即判红）；
      · `tests/**`：**只判真实用法**（`sqlite://` DSN / `:memory:` / `import sqlite3`）——
        用例里有大量"叙述性 sqlite 字面量"（如门禁自证用例讲解本禁令），严格档会全量假红。
    """
    strict_roots = [BACKEND, ROOT / "scripts", ROOT / "alembic"]
    for root in strict_roots:
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts or py.name == "verify_architecture.py":
                continue  # 跳过自身（检查器含禁词字面量）
            if py.name == "import_ecdict.py":
                continue  # C5 一次性导入工具：ECDICT 源文件 data/ecdict.db 本就是 SQLite，运行时库仍 MySQL-only
            text = py.read_text(encoding="utf-8").lower()
            if "sqlite" in text:
                errors.append(f"{py.relative_to(ROOT)}: 检测到 sqlite（宪法禁令）")
    usage = re.compile(r"sqlite:///|:memory:|import\s+sqlite3|from\s+sqlite3", re.I)
    for py in (ROOT / "tests").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        if usage.search(text):
            errors.append(f"{py.relative_to(ROOT)}: 检测到 sqlite 真实用法（DSN/import；宪法禁令）")


def check_import_whitelist(errors: list[str]) -> None:
    """common 不依赖域；域不依赖 admin。"""
    for py in (BACKEND / "common").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        if re.search(r"from\s+backend\.domain", text):
            errors.append(f"{py.relative_to(ROOT)}: common 依赖了业务域")
    for domain in DOMAINS:
        if domain == "admin":
            continue
        for py in (BACKEND / "domain" / domain).rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            text = py.read_text(encoding="utf-8")
            if re.search(r"from\s+backend\.domain\.admin", text):
                errors.append(f"{py.relative_to(ROOT)}: 业务域反向依赖 admin")


def _fn_var_models(fn) -> dict[str, str]:
    """函数内 `变量名 → 模型名`（P3-1，2026-10-18 第七十六批）。

    覆盖本项目常见的三种绑定：`x = ...query(Model)...`、`for x in ...query(Model)`、
    `x = Model(...)`。解析不出来就不进表——调用方据此**跳过**而不是误报。
    """
    import ast

    out: dict[str, str] = {}

    def _query_model(node) -> str | None:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "query"
                and sub.args
                and isinstance(sub.args[0], ast.Name)
            ):
                return sub.args[0].id
        return None

    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                if isinstance(node.value, ast.Call):
                    if isinstance(node.value.func, ast.Name):
                        out.setdefault(target.id, node.value.func.id)  # x = Model(...)
                    model = _query_model(node.value)
                    if model:
                        out[target.id] = model
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.annotation, ast.Name):
                out.setdefault(node.target.id, node.annotation.id)  # x: Model = ...
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            model = _query_model(node.iter)
            if model:
                out[node.target.id] = model
    return out


def check_mutation_count_flush(root: Path | None = None) -> list[str]:
    """T32/教训 41 防呆（E-20260901-04 第三例证）：同函数内「ORM 状态字段赋值」
    与「COUNT 聚合查询」共存且中间无 flush() → autoflush=False 下 COUNT 读旧态
    （多算/少算 1）。两批两犯（T5/WM13 回写），提示词与教训库双失效，进脚本。

    **P3-1（2026-10-18 第七十六批）：只对"同一实体"告警。** 原实现不看模型是否同一张表，
    于是"改 A 表状态、对 B 表 COUNT"（跨实体，读旧态与本次赋值无关）被稳定误报——三处挂账
    会让告警失灵。现在从 AST 解析两边的模型名：**同名才告警**，解析不出来则跳过（宁缺勿滥）。
    自证样例见 `tests/unit/test_verify_architecture_lesson41.py`（同实体告警 / 跨实体零告警 /
    带 flush 零告警）。

    仍输出**告警清单不硬失败**：启发式解析不可能覆盖全部写法（如经函数参数传入的对象），
    硬失败会逼出绕过；清单内函数施工时必须逐个自查并在简报声明。
    """
    import ast

    alerts: list[str] = []
    status_attrs = {
        "status",
        "member_status",
        "refund_status",
        "order_status",
        "operation_locked",
        "available_amount",
    }
    base = root or ROOT
    for py in (base / "backend" / "domain").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            var_models = _fn_var_models(fn)
            # (lineno, kind, model) —— model 为 None 表示解析不出实体
            events: list[tuple[int, str, str | None]] = []
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Assign)
                    and node.targets
                    and isinstance(node.targets[0], ast.Attribute)
                    and node.targets[0].attr in status_attrs
                ):
                    recv = node.targets[0].value
                    model = var_models.get(recv.id) if isinstance(recv, ast.Name) else None
                    events.append((node.lineno, "mutation", model))
                elif isinstance(node, ast.Call):
                    src = ast.unparse(node)
                    if ".query(" in src and (".count()" in src or "func.count" in src):
                        events.append((node.lineno, "count", _fn_var_models_query(node)))
                    elif src.endswith(".flush()"):
                        events.append((node.lineno, "flush", None))
            events.sort(key=lambda e: e[0])
            mutated_model: str | None = None
            for _, kind, model in events:
                if kind == "mutation":
                    mutated_model = model
                elif kind == "flush":
                    mutated_model = None
                elif kind == "count" and mutated_model and model == mutated_model:
                    rel = py.relative_to(base)
                    alerts.append(
                        f"{rel}:{fn.name}——状态赋值后 COUNT 未 flush"
                        f"（教训 41 模式，同一实体 {mutated_model}，自查）"
                    )
                    break
    return alerts


def _fn_var_models_query(node) -> str | None:
    """从 `...query(Model)...count()` 调用里取模型名（取不到返回 None）。"""
    import ast

    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "query"
            and sub.args
            and isinstance(sub.args[0], ast.Name)
        ):
            return sub.args[0].id
    return None


def check_like_escape(errors: list[str]) -> None:
    """用户输入进 LIKE 必须转义（2026-09-16：``sql_utils.escape_like`` 写了却没人用）。

    不转义的后果：搜索框里输 `%` 会匹配全部、输 `_` 会被当单字符通配——不是 SQL 注入
    （参数仍绑定），但搜索失真、且能拿来探测数据。踩坑模式与 `question_validation`
    同类：**「设计了统一入口但调用点各写各的」**，故进架构关机械拦截。

    两种写法都要拦（首版只拦内联式，自证时被变量式绕过，已修正）：
      A 内联：`X.like(f"%{keyword}%")`            → 本行须有 `escape=` 且须有 `escape_like(`
      B 变量：`like = f"%{keyword}%"` + `X.like(like)` → 调用行须有 `escape=`，
                                                        且赋值行须有 `escape_like(`
    `escape=` 只声明"用反斜杠当转义符"，**前提是输入里的 % _ 已被 escape_like 转义**，
    故两个条件都要。sql_utils 自身跳过（它就是要讲这件事）。
    """
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in py.parts or py.name == "sql_utils.py":
            continue
        lines = py.read_text(encoding="utf-8").split("\n")
        # 扫描一：收集"由 f-string 拼出来的 LIKE 模式变量"，记录该赋值有没有过 escape_like
        # P3 门禁族（2026-10-22 第一百一十四批）：引号**两种都要认**——原正则只匹配双引号
        # `f"%`，于是 `like = f'%{kw}%'` + `X.like(like)` 可**整体逃逸**（实测全仓当前无此写法，
        # 属"换个人写就漏"）。
        pattern_vars: dict[str, tuple[int, bool]] = {}
        for ln, line in enumerate(lines, 1):
            m = re.match(r"\s*(\w+)\s*=\s*f[\"']%", line)
            if m:
                pattern_vars[m.group(1)] = (ln, "escape_like(" in line)
        # 扫描二：逐行检查 .like/.ilike 调用
        for ln, line in enumerate(lines, 1):
            if not re.search(r"\.i?like\(", line):
                continue
            inline = "%" in line.split(".like")[-1] or "%" in line
            uses_var = [
                v for v in pattern_vars if re.search(rf"\.i?like\(\s*{re.escape(v)}\b", line)
            ]
            if not inline and not uses_var:
                continue  # 既不是内联模式串、也没引用拼出来的变量（如领域方法 CircleService.like）
            if "escape=" not in line:
                errors.append(
                    f"{py.relative_to(ROOT)}:{ln}: LIKE 调用缺 escape=（应用 escape_like + escape=）"
                )
            if inline and "escape_like(" not in line:
                errors.append(f"{py.relative_to(ROOT)}:{ln}: 内联 LIKE 模式未经 escape_like 转义")
            for var, (assign_ln, escaped) in ((v, pattern_vars[v]) for v in uses_var):
                if not escaped:
                    errors.append(
                        f"{py.relative_to(ROOT)}:{assign_ln}: LIKE 模式变量 `{var}` 未经 "
                        f"escape_like 转义（在 {ln} 行被使用）"
                    )


#: 事件契约白名单：`event_type` → **不修的理由**（空理由不放行；条目过期会被判红）。
#: 依据：`CLAUDE.md` 第三节「跨域副作用走事件…**事件发布/订阅双向对账入门禁**」——
#: 第九轮 P1-8 实证这条承诺从未落地，而 P1-1（预约成功通知链断链）正是靠这个空档长期存活。
#: 2026-10-28 甲方裁定：N-8 四个死事件类（预约取书/预约取消/阅读完成/会话结束）已删除，
#: 白名单清零。此表只接受「确属设计选择且本轮不修」的条目，且必须写清理由。
EVENT_CONTRACT_WHITELIST: dict[str, str] = {}


def _scan_event_contract(backend: Path):
    """AST 全量扫事件契约三面：类定义（event_type）/ 发布点 / 订阅点。

    **订阅的三种写法都要解析**——第九轮专家复核 N-7 的假阳性教训：`growth_handlers.py:23`
    用的是 `event_bus.subscribe("reading.checkin", ...)`，只认 `X.event_type` 写法的扫描器
    看不见它，于是把「CheckInEvent 发布无订阅」写进了报告。对账器若犯同一个错，等于把
    假阳性固化成 gate 规则。

    解析不出的写法进 notes（**不判红**）：静态解析不可能覆盖全部写法，误报会逼出绕过。
    """
    import ast

    classes: dict[str, str | None] = {}  # 类名 → event_type（None = 未声明）
    sites: dict[str, str] = {}  # 类名 → "file:line"
    published: dict[str, str] = {}  # 类名 → "file:line"（事件类型在第二遍之后解析）
    subscribed: dict[str, str] = {}  # event_type → "file:line"
    notes: list[str] = []

    parsed = []
    for py in sorted(backend.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法错由 lint 管，这里不重复报
            continue
        rel = py.relative_to(ROOT) if ROOT in py.parents else py
        parsed.append((rel, tree))
        # 第一遍：类定义（发布点可能写在定义之前，必须先把类收齐）
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("Event" in ast.unparse(b) for b in node.bases):
                continue
            et: str | None = None
            for st in node.body:
                if (
                    isinstance(st, ast.AnnAssign)
                    and getattr(st.target, "id", None) == "event_type"
                    and isinstance(st.value, ast.Constant)
                    and isinstance(st.value.value, str)
                ):
                    et = st.value.value
            classes[node.name] = et
            sites[node.name] = f"{rel}:{node.lineno}"

    for rel, tree in parsed:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            attr = getattr(node.func, "attr", None)
            arg0 = node.args[0]
            if attr == "publish":
                name = None
                if isinstance(arg0, ast.Call):
                    name = getattr(arg0.func, "id", None) or getattr(arg0.func, "attr", None)
                elif isinstance(arg0, ast.Name):
                    name = arg0.id
                if name is None:
                    notes.append(f"{rel}:{node.lineno} publish 载荷无法静态解析")
                    continue
                published.setdefault(name, f"{rel}:{node.lineno}")
            elif attr == "subscribe":
                owner: str | None = None
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    subscribed.setdefault(arg0.value, f"{rel}:{node.lineno}")
                    continue
                if isinstance(arg0, ast.Attribute) and arg0.attr == "event_type":
                    owner = getattr(arg0.value, "id", None)
                elif isinstance(arg0, ast.Name):
                    owner = arg0.id
                if owner is None:
                    notes.append(f"{rel}:{node.lineno} subscribe 参数无法静态解析")
                    continue
                if owner not in classes:
                    notes.append(f"{rel}:{node.lineno} subscribe({owner}) 的类不在扫描面内")
                    continue
                et = classes[owner]
                if et is None:
                    notes.append(f"{rel}:{node.lineno} subscribe({owner}) 的类未声明 event_type")
                    continue
                subscribed.setdefault(et, f"{rel}:{node.lineno}")
    return classes, sites, published, subscribed, notes


def check_event_contract(errors: list[str]) -> str:
    """事件**发布 / 订阅 / 定义**三向对账（宪法第三节承诺；第九轮 P1-8）。

    判据（任一差集非空即红；例外只能进 `EVENT_CONTRACT_WHITELIST` 并写清理由）：
      · **发布无订阅**（断链）：P1-1 就是这个形状——订阅器与微信模板都在，发布方从不存在，
        接口照常 200、无审计、无死信，家长端永远收不到通知；
      · **订阅无发布**（死订阅）：订阅了一个全仓没有任何发布方的事件；
      · **定义即死类**：类存在但零发布零订阅（要么补接，要么删类）；
      · **订阅串不存在**：`subscribe("typo.here")` 这类拼错的事件类型；
      · **白名单过期**：条目已不再命中任何问题 → 必须删（防白名单长成第二个垃圾场）。
    """
    classes, sites, published, subscribed, notes = _scan_event_contract(BACKEND)
    declared = {et: cls for cls, et in classes.items() if et}

    pub_types: dict[str, str] = {}
    for name, site in sorted(published.items()):
        if name not in classes:
            errors.append(f"{site}: publish 的事件类 `{name}` 在本仓库内无定义（拼错？）")
            continue
        et = classes[name]
        if et is None:
            errors.append(f"{site}: 事件类 `{name}` 未声明 event_type（发布了也只会被静默丢弃）")
            continue
        pub_types.setdefault(et, f"{sites.get(name, '?')} → {site}")

    known = set(declared)
    for et, site in sorted(subscribed.items()):
        if et not in known:
            errors.append(f"{site}: 订阅了不存在的事件类型 `{et}`（拼错？全仓无此 event_type）")

    problems: set[str] = set()
    for et, chain in sorted(pub_types.items()):
        if et in subscribed:
            continue
        problems.add(et)
        if et not in EVENT_CONTRACT_WHITELIST:
            errors.append(
                f"事件契约断链：`{et}` 有发布方无订阅者（{chain}）——订阅方丢了，家长端永远收不到"
            )
    for et, site in sorted(subscribed.items()):
        if et in pub_types or et not in known:
            continue
        problems.add(et)
        if et not in EVENT_CONTRACT_WHITELIST:
            errors.append(f"事件契约死订阅：`{et}` 有人订阅但全仓无发布方（{site}）")
    for et, cls in sorted(declared.items()):
        if et in pub_types or et in subscribed:
            continue
        problems.add(et)
        if et not in EVENT_CONTRACT_WHITELIST:
            errors.append(
                f"事件契约死类：`{et}`（{cls} @ {sites.get(cls, '?')}）零发布零订阅"
                "——要么补接、要么删类"
            )
    for et, why in sorted(EVENT_CONTRACT_WHITELIST.items()):
        if et not in problems:
            errors.append(f"事件契约白名单条目 `{et}` 已不再命中任何问题（理由：{why}）——请删除")
    for note in notes:
        print(f"[事件契约·待自查] {note}")
    return (
        f"事件面 {len(declared)} 类 / 发布 {len(pub_types)} / 订阅 {len(subscribed)}"
        f" / 白名单 {len(EVENT_CONTRACT_WHITELIST)}"
    )


def _module_index(pkg_root: Path) -> dict[str, Path]:
    """`backend.a.b` → 磁盘文件（只收该包内的模块，`__init__` 归到包名）。"""
    idx: dict[str, Path] = {}
    for py in pkg_root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        parts = list(py.relative_to(pkg_root.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        idx[".".join(parts)] = py
    return idx


def _module_level_imports(path: Path) -> list[str]:
    """该文件的**模块级** import 目标（候选名，含 `from X import y` 里的 `X.y`）。

    只收模块级：函数/类体内的延迟导入**不算**——那是项目允许的形态（第九轮 N-4 已登记的
    circulation↔reading / activity↔identity 隐式环全在函数体内，导入期安全）。
    `if TYPE_CHECKING:` 也不算（运行时不执行）。
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out: list[str] = []

    def walk(nodes: list) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.If):
                if "TYPE_CHECKING" in ast.unparse(node.test):
                    continue
                walk(node.body)
                walk(node.orelse)
                continue
            if isinstance(node, ast.Try):
                walk(node.body)
                walk(node.orelse)
                walk(node.finalbody)
                continue
            if isinstance(node, ast.Import):
                out.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:  # 相对导入留空（解析易出错）
                    continue
                out.append(node.module)
                out.extend(f"{node.module}.{a.name}" for a in node.names)

    walk(tree.body)
    return out


def _resolve_module(candidate: str, index: dict[str, Path]) -> str | None:
    """候选名 → 索引里的模块全名：精确命中优先，否则退到最长的包前缀。"""
    parts = candidate.split(".")
    while parts:
        name = ".".join(parts)
        if name in index:
            return name
        parts.pop()
    return None


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """DFS 三色法找第一个环（返回环上模块名，首尾同点）。"""
    color: dict[str, int] = dict.fromkeys(graph, 0)
    stack: list[str] = []

    def dfs(node: str):
        color[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if color.get(nxt, 2) == 1:
                return stack[stack.index(nxt) :] + [nxt]
            if color.get(nxt, 2) == 0:
                got = dfs(nxt)
                if got:
                    return got
        stack.pop()
        color[node] = 2
        return None

    for node in sorted(graph):
        if color[node] == 0:
            got = dfs(node)
            if got:
                return got
    return None


def check_import_cycles(errors: list[str]) -> None:
    """**模块级导入环 = 0**（第九轮 P1-2 防复发）。

    为什么必须有：`wm10_service` ↔ `wm10_withdrawal_service` 曾互相顶层 import——实测
    `ImportError`，能跑只因 pytest 收集顺序恰好先导了其中一边。这类缺陷**在 CI 里可能永远不显形**
    （换个入口顺序就炸），只能靠机械对账拦。判据：只收模块级 import（延迟导入不算），任一环即红。
    """
    index = _module_index(BACKEND)
    graph: dict[str, set[str]] = {name: set() for name in index}
    for name, path in index.items():
        for candidate in _module_level_imports(path):
            target = _resolve_module(candidate, index)
            if target and target != name:
                graph[name].add(target)
    cycle = _find_cycle(graph)
    if not cycle:
        return
    chain = " → ".join(str(index[name].relative_to(ROOT)) for name in cycle)
    errors.append(f"模块级导入环（顶层循环导入，换导入顺序即 ImportError）：{chain}")


def _foreign_models(py: Path, dom: str) -> dict[str, str]:
    """本文件从**其他域**的 `models` 导入的 `{类名: 目标域}`。"""
    import ast

    out: dict[str, str] = {}
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module):
            continue
        parts = node.module.split(".")
        if len(parts) >= 4 and parts[:2] == ["backend", "domain"]:
            tgt = parts[2]
            if tgt != dom and parts[3] == "models":
                for alias in node.names:
                    out[alias.name] = tgt
    return out


#: 跨域写白名单：`(文件, 函数名, 目标域)` → 理由。**键不含行号**（行号会漂，语义不会）。
#: 目标是逐条销项成"调对方域公开方法"（见 `docs/整改任务书-第九轮-20261022.md` §三 B3）。
CROSS_DOMAIN_WRITE_WHITELIST: dict[tuple[str, str, str], str] = {
    (
        "backend/domain/circulation/service.py",
        "borrow",
        "catalog",
    ): "借出：副本 -> BORROWED（同事务行锁内）",
    (
        "backend/domain/circulation/service.py",
        "return_book",
        "catalog",
    ): "归还：副本 -> AVAILABLE/维护/遗失",
    (
        "backend/domain/reading/reservation_ops.py",
        "handle_copy_unavailable",
        "catalog",
    ): "副本失效漏斗：改锁他册/转异常",
    (
        "backend/domain/reading/reservation_service.py",
        "create",
        "catalog",
    ): "预约：副本 -> RESERVED",
    (
        "backend/domain/reading/reservation_service.py",
        "cancel",
        "catalog",
    ): "取消预约：副本 -> AVAILABLE",
    (
        "backend/domain/reading/reservation_service.py",
        "expire_due",
        "catalog",
    ): "超时释放：副本 -> AVAILABLE",
    (
        "backend/domain/reading/reservation_service.py",
        "checkout",
        "catalog",
    ): "核销转借阅：副本 -> BORROWED",
    (
        "backend/domain/identity/wm10_service.py",
        "_finalize_refund",
        "billing",
    ): "退款执行：押金 -> refunded + DepositLedger",
    (
        "backend/domain/identity/wm10_withdrawal_service.py",
        "ensure_deposit_refund_on_withdrawal",
        "billing",
    ): "退会联动押金：Deposit -> REFUNDING + 建押金退款单（R11-b 从 _complete_refund_withdrawal 抽出）",
    (
        "backend/domain/identity/wm10_withdrawal_service.py",
        "review",
        "billing",
    ): "退会复核：押金 -> REFUNDING",
    ("backend/domain/identity/transfer_service.py", "review", "billing"): "转让结算：押金退款链",
    (
        "backend/domain/identity/wm10_service.py",
        "_refund_activity_enrollment",
        "activity",
    ): "活动单退款：报名行状态",
    (
        "backend/domain/activity/cancel_flow.py",
        "cancel_activity",
        "identity",
    ): "活动取消：条件 UPDATE 作废未收款订单（2026-10-12 拍板 P4；待抽 identity 公开方法销项）",
}


def _scan_cross_domain_writes(backend: Path) -> set[tuple[str, str, str]]:
    """域 A 的函数**直接写域 B 的表** / **跨域调私有方法** → `{(文件, 函数, 目标域)}`。

    判据（AST，只认能静态判死的形态）：
      · 外部域模型对象的属性赋值（`dep.status = ...`，变量由 `query(Deposit)` 绑定）；
      · `db.add(外部域模型(...))`；
      · `query(外部域模型).update()/.delete()`；
      · 跨域调**私有**方法（`XxxService(self.db)._x(...)`，`XxxService` 定义在别的域）。
    解析不出的形态（跨层传参、动态取属性）**不判红**——误报会逼出绕过。
    """
    import ast

    service_domain: dict[str, str] = {}
    for dom in DOMAINS:
        for py in (backend / "domain" / dom).rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.endswith("Service"):
                    service_domain[node.name] = dom

    found: set[tuple[str, str, str]] = set()
    for dom in DOMAINS:
        for py in sorted((backend / "domain" / dom).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            foreign = _foreign_models(py, dom)
            if not foreign:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            rel = str(py.relative_to(ROOT)) if ROOT in py.parents else str(py)
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                bound: dict[str, str] = {}
                for node in ast.walk(fn):
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                        src = ast.unparse(node.value)
                        for cls, tgt in foreign.items():
                            if f"query({cls})" in src or f"get({cls}" in src:
                                if isinstance(node.targets[0], ast.Name):
                                    bound[node.targets[0].id] = tgt
                    elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                        src = ast.unparse(node.iter)
                        for cls, tgt in foreign.items():
                            if f"query({cls})" in src:
                                bound[node.target.id] = tgt
                for node in ast.walk(fn):
                    tgt: str | None = None
                    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Attribute):
                        recv = node.targets[0].value
                        if isinstance(recv, ast.Name) and recv.id in bound:
                            tgt = bound[recv.id]
                    elif isinstance(node, ast.Call):
                        src = ast.unparse(node)
                        func = ast.unparse(node.func)
                        if func.endswith((".add", ".delete")):
                            for cls, t in foreign.items():
                                if f"{cls}(" in src:
                                    tgt = t
                        elif "query(" in src and func.endswith((".update", ".delete")):
                            for cls, t in foreign.items():
                                if f"query({cls})" in src:
                                    tgt = t
                        elif isinstance(node.func, ast.Attribute) and node.func.attr.startswith(
                            "_"
                        ):
                            # 跨域调私有方法：`XxxService(self.db)._x(...)`
                            recv = node.func.value
                            name = recv.id if isinstance(recv, ast.Name) else ast.unparse(recv)
                            owner = name.split("(")[0].split(".")[-1].strip()
                            t = service_domain.get(owner)
                            if t and t != dom:
                                found.add((rel, fn.name, t))
                                continue
                    if tgt:
                        found.add((rel, fn.name, tgt))
    return found


def check_member_status_write_port(errors: list[str]) -> None:
    """会员状态**唯一写口**（第九轮 P2-6；第七十五批 P1-1 防复发）。

    判据：除写口本体（`identity/member_status.py`）与 `set_member_status`（`identity/models.py`）
    之外，`backend/**` 不得出现 `x.member_status = <值>` 直赋——直赋绕过"待评估时钟 + 押金连带
    动作"，而这两件连带动作**各丢过一次**（第七十五批 P1-1：押金复位只挂在观察期费路径）。
    第九轮实测 4 处直赋散在退会/转让链上（该轮已收拢，本条防回归）。
    """
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in py.parts or py.name == "member_status.py":
            continue
        if py.parent.name == "identity" and py.name == "models.py":
            continue
        for ln, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.member_status\s*=\s*[^=]", line):
                errors.append(
                    f"{py.relative_to(ROOT)}:{ln}: 直赋 member_status —— 请走 write_member_status（唯一写口）"
                )


def check_cross_domain_writes(errors: list[str]) -> None:
    """跨域直写必须**显式登记**（第九轮 P2-5/B3）。

    为什么要有：域边界靠"约定 + 对方域公开方法"维持，直写对方表是**静默**的——
    改签名/加不变量时没有任何机器提醒（第九轮实测 12 处，专家只抽了 4 处）。
    登记制把"边界侵蚀"变成"明示的例外"，新增直写当场判红；白名单条目过期也判红。
    """
    found = _scan_cross_domain_writes(BACKEND)
    for key in sorted(found - set(CROSS_DOMAIN_WRITE_WHITELIST)):
        rel, fn, tgt = key
        errors.append(
            f"{rel}:{fn}() 直写 `{tgt}` 域的表或调其私有方法（跨域边界）——"
            "改走对方域公开方法，或登记进 CROSS_DOMAIN_WRITE_WHITELIST 并写明理由"
        )
    for key in sorted(set(CROSS_DOMAIN_WRITE_WHITELIST) - found):
        rel, fn, tgt = key
        errors.append(
            f"跨域白名单条目 `{rel}:{fn}() -> {tgt}` 已不再命中任何直写"
            f"（理由：{CROSS_DOMAIN_WRITE_WHITELIST[key]}）——已销项请删除该条目"
        )


def self_test() -> int:
    """S1 注入自检：**每条规则各喂一个违例样本，必须全命中**。

    为什么必须有（第八轮审查 P1-10）：实测把 sqlite 禁令关掉后本检查器仍打印 `架构关 PASS`
    —— 架构关是宪法第三节的机械执法，它自己失效等于"执法者无人监督"。
    做法：把模块级扫描根（`BACKEND` / `ROOT` / `DOMAINS`）临时指向夹具树，逐条调规则函数。
    """
    import tempfile

    global BACKEND, ROOT, DOMAINS, EVENT_CONTRACT_WHITELIST, CROSS_DOMAIN_WRITE_WHITELIST
    saved = (BACKEND, ROOT, DOMAINS, EVENT_CONTRACT_WHITELIST, CROSS_DOMAIN_WRITE_WHITELIST)
    bad = 0
    try:
        with tempfile.TemporaryDirectory(prefix="arch-selftest-") as d:
            root = Path(d)
            backend = root / "backend"
            (backend / "domain/identity").mkdir(parents=True)
            (backend / "common").mkdir(parents=True)
            (backend / "domain/identity/models.py").write_text("", encoding="utf-8")
            (backend / "domain/identity/schemas.py").write_text("", encoding="utf-8")
            # repository.py 故意不建 → 四件套缺件样本
            # ① 四件套：故意缺 service.py；router.py 同时注入 ORM 调用 + 未链 populate_existing
            (backend / "domain/identity/router.py").write_text(
                "def x(db):\n    return db.query(1)\n", encoding="utf-8"
            )
            (backend / "domain/identity/miniapp_router.py").write_text("", encoding="utf-8")
            (backend / "domain/identity/service.py").write_text(
                "def f(db):\n    return db.query(X).with_for_update().first()\n", encoding="utf-8"
            )
            # ② sqlite 禁令
            (backend / "common/bad.py").write_text("import sqlite3\n", encoding="utf-8")
            # ③ import 白名单：common 依赖业务域
            (backend / "common/rogue.py").write_text(
                "from backend.domain.identity.models import Parent\n", encoding="utf-8"
            )
            # ④ LIKE 转义：内联模式未经 escape_like
            (backend / "common/like_bad.py").write_text('q = X.like(f"%{kw}%")\n', encoding="utf-8")
            # ⑤ 行数上限
            (backend / "common/huge.py").write_text(
                "x = 1\n" * (MAX_FILE_LINES + 5), encoding="utf-8"
            )
            # ⑥ 域列表漂移（P3 第一百一十四批）：磁盘多一个域目录
            (backend / "domain/rogue_domain").mkdir(parents=True)
            # ⑦ Router 的 db.execute 形态（新增正则）——注意必须写进 **ROUTER_PIECES 内的文件名**
            # （首版写成 router2.py，不在扫描面 → 自证当场判"未命中"，这就是自证的价值）
            (backend / "domain/identity/miniapp_router.py").write_text(
                "def y(db):\n    return db.execute(text('select 1'))\n", encoding="utf-8"
            )
            # ⑧ sqlite 扩面：alembic 严格档 + tests 真实用法档
            (root / "alembic/versions").mkdir(parents=True)
            (root / "alembic/versions/bad_mig.py").write_text(
                "SQLITE_URL = 'sqlite:///tmp.db'\n", encoding="utf-8"
            )
            (root / "tests").mkdir(parents=True)
            (root / "tests/test_bad_dsn.py").write_text(
                "engine = create_engine('sqlite:///:memory:')\n", encoding="utf-8"
            )
            (root / "tests/test_narrative.py").write_text(
                "# 本用例讲的是 sqlite 禁令的叙述性文字（不该判红）\nX = 1\n", encoding="utf-8"
            )
            # ⑨ LIKE 单引号形态（新增：原正则只认双引号）
            (backend / "common/like_single.py").write_text(
                "like = f'%{kw}%'\nq = X.like(like, escape='\\\\\\\\')\n", encoding="utf-8"
            )
            # ⑩ 事件契约三向对账（第九轮 P1-8）：发布无订阅 / 订阅无发布 / 死类 / 订阅串拼错 /
            #    白名单命中不判红 / **字符串字面量订阅必须被认出**（专家 N-7 假阳性的反向样本）
            (backend / "common/events_fixture.py").write_text(
                "class PubOnlyEvent(Event):\n"
                '    event_type: str = "fx.pub_only"\n'
                "\n\n"
                "class SubOnlyEvent(Event):\n"
                '    event_type: str = "fx.sub_only"\n'
                "\n\n"
                "class DeadEvent(Event):\n"
                '    event_type: str = "fx.dead"\n'
                "\n\n"
                "class WhitelistedDeadEvent(Event):\n"
                '    event_type: str = "fx.whitelisted"\n'
                "\n\n"
                "class LiteralOkEvent(Event):\n"
                '    event_type: str = "fx.literal_ok"\n'
                "\n\n"
                "def pub():\n"
                "    event_bus.publish(PubOnlyEvent())\n"
                "    event_bus.publish(LiteralOkEvent())\n"
                "\n\n"
                "def sub():\n"
                '    event_bus.subscribe("fx.sub_only", handler)\n'
                '    event_bus.subscribe("fx.literal_ok", handler)\n'
                '    event_bus.subscribe("fx.no_such_type", handler)\n',
                encoding="utf-8",
            )

            # ⑪ 模块级导入环（第九轮 P1-2 防复发）：互导对 + 延迟导入反向样本
            (backend / "cyc_a.py").write_text(
                "from backend.cyc_b import bee\n\n\ndef aye():\n    return 1\n", encoding="utf-8"
            )
            (backend / "cyc_b.py").write_text(
                "from backend.cyc_a import aye\n\n\ndef bee():\n    return 1\n", encoding="utf-8"
            )
            (backend / "lazy_c.py").write_text(
                "def f():\n    from backend.cyc_a import aye\n    return aye()\n", encoding="utf-8"
            )

            BACKEND, ROOT, DOMAINS = backend, root, ["identity"]
            EVENT_CONTRACT_WHITELIST = {"fx.whitelisted": "自证样本：白名单命中即不判红"}
            CROSS_DOMAIN_WRITE_WHITELIST = {}
            # ⑬ 会员状态直赋（第九轮 P2-6）：非写口模块里直赋 member_status → 必红
            (backend / "domain/identity/status_bad.py").write_text(
                "def bad(child):\n    child.member_status = 'withdrawn'\n",
                encoding="utf-8",
            )
            # ⑫ 跨域直写（第九轮 B3）：identity 直写 rogue_domain 的模型 → 必红；
            #     同文件的只读函数不得被判红（反向样本 ok_read）
            (backend / "domain/identity/cross_bad.py").write_text(
                "from backend.domain.rogue_domain.models import Widget\n"
                "\n\n"
                "def bad(db):\n"
                "    w = db.query(Widget).first()\n"
                "    w.name = 'x'\n"
                "\n\n"
                "def ok_read(db):\n"
                "    return db.query(Widget).count()\n",
                encoding="utf-8",
            )
            errs: list[str] = []
            check_domain_list(errs)
            check_four_pieces(errs)
            check_router_violations(errs)
            check_lock_populate(errs)
            check_file_length(errs)
            check_sqlite_ban(errs)
            check_import_whitelist(errs)
            check_like_escape(errs)
            check_import_cycles(errs)
            check_cross_domain_writes(errs)
            check_member_status_write_port(errs)
            check_event_contract(errs)
            joined = "\n".join(errs)
            needles = (
                ("四件套缺 repository.py", "identity/repository.py 缺失"),
                ("Router 出现 ORM", "Router 层出现 ORM 操作"),
                ("with_for_update 未链 populate_existing", "未链 populate_existing"),
                ("文件行数超限", "god file 禁止"),
                ("sqlite 禁令", "检测到 sqlite"),
                ("common 依赖业务域", "common 依赖了业务域"),
                ("LIKE 未经 escape_like", "未经 escape_like 转义"),
                ("域列表漂移（未登记的新域）", "未登记进 DOMAINS"),
                ("Router 的 db.execute 形态", "execute/get/scalar/flush"),
                ("alembic 里的 sqlite", "alembic/versions/bad_mig.py"),
                ("tests 里的 sqlite 真实用法", "sqlite 真实用法"),
                ("LIKE 单引号模式变量", "like_single.py"),
                ("事件契约断链（发布无订阅）", "fx.pub_only"),
                ("事件契约死订阅（订阅无发布）", "fx.sub_only"),
                ("事件契约死类", "fx.dead"),
                ("订阅了不存在的事件类型", "fx.no_such_type"),
                ("模块级导入环", "模块级导入环"),
                ("跨域直写未登记", "cross_bad.py"),
                ("会员状态直赋", "status_bad.py"),
            )
            for label, needle in needles:
                if needle not in joined:
                    print(f"✗ S1 [{label}] 未命中（期望输出含 {needle!r}）")
                    bad += 1
            # 反向样本：叙述性 sqlite 不得判红；字符串字面量订阅必须被认出（不得报"发布无订阅"）；
            # 白名单命中的死类不得判红（否则白名单形同虚设）；**延迟导入不得被当成环**
            for label, needle in (
                ("tests 里的叙述性 sqlite", "test_narrative.py"),
                ("字符串字面量订阅被误判为无订阅", "fx.literal_ok"),
                ("白名单命中的死类仍被判红", "fx.whitelisted"),
                ("函数内延迟导入被误判为导入环", "lazy_c.py"),
                ("跨域文件里的只读函数被误判为直写", "ok_read"),
            ):
                if needle in joined:
                    print(f"✗ S1 [{label}] 被误报")
                    bad += 1

            # 反向样本：把违例全修好 → 必须零违规（检查器过严会逼出无意义的绕过）
            (backend / "domain/identity/service.py").write_text(
                "def f(db):\n    return db.query(X).with_for_update().populate_existing().first()\n",
                encoding="utf-8",
            )
            (backend / "domain/identity/router.py").write_text(
                "def x():\n    return 1\n", encoding="utf-8"
            )
            (backend / "domain/identity/repository.py").write_text("", encoding="utf-8")
            (backend / "common/bad.py").write_text("import os\n", encoding="utf-8")
            (backend / "common/rogue.py").write_text("import os\n", encoding="utf-8")
            (backend / "common/like_bad.py").write_text(
                'q = X.like(f"%{escape_like(kw)}%", escape="\\\\")\n', encoding="utf-8"
            )
            (backend / "common/huge.py").write_text("x = 1\n", encoding="utf-8")
            # 新规则的合法样本：新域登记进 DOMAINS、alembic/tests 干净、LIKE 单引号但已转义
            DOMAINS = ["identity", "rogue_domain"]
            (backend / "domain/rogue_domain/repository.py").write_text("", encoding="utf-8")
            (backend / "domain/rogue_domain/models.py").write_text("", encoding="utf-8")
            (backend / "domain/rogue_domain/schemas.py").write_text("", encoding="utf-8")
            (backend / "domain/rogue_domain/service.py").write_text("", encoding="utf-8")
            (backend / "domain/rogue_domain/router.py").write_text("", encoding="utf-8")
            (backend / "domain/identity/miniapp_router.py").write_text(
                "def y():\n    return 1\n", encoding="utf-8"
            )
            (root / "alembic/versions/bad_mig.py").write_text(
                "URL = 'mysql+pymysql://x'\n", encoding="utf-8"
            )
            (root / "tests/test_bad_dsn.py").write_text("X = 1\n", encoding="utf-8")
            (backend / "common/like_single.py").write_text(
                "like = f'%{escape_like(kw)}%'\nq = X.like(like, escape='\\\\\\\\')\n",
                encoding="utf-8",
            )
            # 事件契约的合法样本：一 publish 一 subscribe（**字面量写法**），白名单清空
            EVENT_CONTRACT_WHITELIST = {}
            (backend / "common/events_fixture.py").write_text(
                "class LiteralOkEvent(Event):\n"
                '    event_type: str = "fx.literal_ok"\n'
                "\n\n"
                "def pub():\n"
                "    event_bus.publish(LiteralOkEvent())\n"
                "\n\n"
                "def sub():\n"
                '    event_bus.subscribe("fx.literal_ok", handler)\n',
                encoding="utf-8",
            )
            # 跨域写的合法样本：只读对方域 → 不判红
            (backend / "domain/identity/cross_bad.py").write_text(
                "from backend.domain.rogue_domain.models import Widget\n"
                "\n\n"
                "def ok_read(db):\n"
                "    return db.query(Widget).count()\n",
                encoding="utf-8",
            )
            CROSS_DOMAIN_WRITE_WHITELIST = {}
            (backend / "domain/identity/status_bad.py").write_text(
                "def ok(child):\n    return child.member_status\n", encoding="utf-8"
            )
            # 导入环的合法样本：把互导改成「一方延迟导入」→ 环消失
            (backend / "cyc_b.py").write_text(
                "def bee():\n    from backend.cyc_a import aye\n    return aye()\n",
                encoding="utf-8",
            )
            clean: list[str] = []
            check_domain_list(clean)
            check_four_pieces(clean)
            check_router_violations(clean)
            check_lock_populate(clean)
            check_file_length(clean)
            check_sqlite_ban(clean)
            check_import_whitelist(clean)
            check_like_escape(clean)
            check_import_cycles(clean)
            check_cross_domain_writes(clean)
            check_member_status_write_port(clean)
            check_event_contract(clean)
            if clean:
                print(f"✗ S1 合法样本被误报（检查器过严）: {clean}")
                bad += 1
            # 白名单过期自检：条目指向一个已不再是问题的 event_type → 必须判红（防白名单长成垃圾场）
            EVENT_CONTRACT_WHITELIST = {"fx.literal_ok": "自证样本：该条目已不再命中问题"}
            rot: list[str] = []
            check_event_contract(rot)
            if not any("白名单条目" in e for e in rot):
                print("✗ S1 事件契约白名单过期未判红（白名单不可腐烂）")
                bad += 1
            # 跨域白名单过期同样必须判红（指向一个已无直写的函数）
            CROSS_DOMAIN_WRITE_WHITELIST = {
                (
                    "backend/domain/identity/cross_bad.py",
                    "ok_read",
                    "rogue_domain",
                ): "自证样本：该条已无命中"
            }
            rot2: list[str] = []
            check_cross_domain_writes(rot2)
            if not any("已不再命中" in e for e in rot2):
                print("✗ S1 跨域白名单过期未判红")
                bad += 1
            CROSS_DOMAIN_WRITE_WHITELIST = {}
    finally:
        BACKEND, ROOT, DOMAINS, EVENT_CONTRACT_WHITELIST, CROSS_DOMAIN_WRITE_WHITELIST = saved
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print(f"✓ S1 注入自检通过（{len(needles)} 类违例全命中 + 合法样本与白名单零误报）")
    return 0


def main() -> int:
    errors: list[str] = []
    check_domain_list(errors)
    check_four_pieces(errors)
    check_router_violations(errors)
    check_lock_populate(errors)
    check_file_length(errors)
    check_sqlite_ban(errors)
    check_import_whitelist(errors)
    check_like_escape(errors)
    check_import_cycles(errors)
    check_cross_domain_writes(errors)
    check_member_status_write_port(errors)
    event_summary = check_event_contract(errors)

    if errors:
        print(f"架构关 FAIL（{len(errors)} 处违规）：")
        for e in errors:
            print(f"  ✗ {e}")
        return 1
    print(
        "架构关 PASS：域列表与磁盘一致 / 四件套齐全 / Router 零违规（含 miniapp_router）"
        " / 锁定读均链 populate_existing（backend 全量）/ 行数达标"
        " / 无 sqlite（backend+scripts+alembic 严格、tests 判真实用法）/ import 白名单通过 / LIKE 全转义"
        " / 模块级导入环 0（延迟导入不算）/ 跨域直写全量登记 / 会员状态零直赋"
        f" / 事件契约三向对账（{event_summary}）"
    )
    alerts = check_mutation_count_flush()
    if alerts:
        print(
            f"[T32 教训 41 防呆] 告警清单（不拦截，施工命中函数必须逐个自查+简报声明）{len(alerts)} 处："
        )
        for a in alerts:
            print(f"  ⚠ {a}")
    else:
        print("[T32 教训 41 防呆] 零命中 ✓")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
