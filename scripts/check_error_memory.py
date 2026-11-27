# scripts/check_error_memory.py — 错误记忆库的机械判据（B5-9，2026-11-27）
"""审查① G4：错误记忆库停在 10-31，`grep "2026-11"` = 0 命中——11 月的坑全部只落进 LEDGER/交接卡，
DoD 6「新坑写入错误记忆库」事实上从未执行。原因很实在：**库已 149 节 / 3800+ 行 / 345KB**，
"开工通读"在物理上等于"不读"。所以本检查器钉住三件事：

1. **滚动索引恒为最新 10 节**（`## 零、滚动最新 10 条`）：恰好 10 行、节号必须解析到真实小节、
   这 10 节的编号必须等于文件里**编号最高的 10 节**。写了新节却忘了滚动 → 判红
   （否则索引会慢慢退化成"最新 10 条里最新的那条是三个月前"）。
2. **索引行不许悬空/重复**：节号、日期、一句话教训三列都要有内容。
3. **"新坑入库"机械判据**：LEDGER 里带「教训 / 根因 / 新坑」的批次行，若批次号 ≥ `MEM_CUTOFF_BATCH`
   （本判据生效的批次），错误记忆库必须提到同一个「第 N 批」。
   历史欠账通过 `LEDGER_EXEMPT` 显式放行（**条目过期判红**，防白名单长成垃圾场——同
   `verify_architecture.CROSS_DOMAIN_WRITE_WHITELIST` 的纪律）。

自证（`--self-test`）：用夹具库/夹具台账注入三类违例，必须全部判红；合法夹具必须零误报。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEM_GLOB = "error_list/错误记忆库-全项目-*.md"
LEDGER = ROOT / "docs/LEDGER.md"

#: 本判据生效的批次（含）。更早的批次行属历史欠账，不入本判据的追溯范围。
MEM_CUTOFF_BATCH = 191

#: 历史欠账白名单：批次号 → 不补录的理由。**条目一旦不再命中问题即判红**（必须删）。
LEDGER_EXEMPT: dict[int, str] = {}

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100}

_HEADING_RE = re.compile(r"^## ([零一二三四五六七八九十百]+)、(.+)$")
_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*([零一二三四五六七八九十百]+)\s*\|\s*([0-9-]+)\s*\|\s*(.+?)\s*\|\s*$"
)
_BATCH_RE = re.compile(r"第([零一二三四五六七八九十百]+)批")
_ARABIC_BATCH_RE = re.compile(r"第\s*(\d+)\s*批")
_LESSON_RE = re.compile(r"教训|根因|新坑")
_ROLLING_TITLE = "## 零、"

ROLLING_SIZE = 10


def cn_to_int(text: str) -> int:
    """中文数字 → int（0..999）。拒绝空串与非数字字符。"""
    if not text:
        raise ValueError("空的中文数字")
    total = 0
    current = 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (current or 1) * _CN_UNITS[ch]
            current = 0
        else:
            raise ValueError(f"不认识的中文数字字符：{ch!r}（{text}）")
    return total + current


def int_to_cn(n: int) -> str:
    """int → 中文数字（夹具生成用；真实库的节号是中文数字）。"""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    h, rest = divmod(n, 100)
    head = digits[h] + "百"
    if rest == 0:
        return head
    if rest < 10:
        return head + "零" + digits[rest]
    if rest < 20:
        return head + "一十" + (digits[rest % 10] if rest % 10 else "")
    return head + digits[rest // 10] + "十" + (digits[rest % 10] if rest % 10 else "")


def latest_memory_file(root: Path = ROOT) -> Path:
    files = sorted(root.glob(MEM_GLOB))
    if not files:
        raise FileNotFoundError(f"找不到错误记忆库：{MEM_GLOB}")
    return files[-1]


def headings(text: str) -> dict[int, str]:
    """编号 → 标题（只认 `## <中文数字>、...`；`一·补` 这类非纯数字节名自然跳过）。"""
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if not m:
            continue
        try:
            n = cn_to_int(m.group(1))
        except ValueError:
            continue
        out[n] = m.group(2)
    return out


def rolling_rows(text: str) -> list[dict[str, str]] | None:
    """解析 §零 滚动索引表；没有这一节返回 None。"""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(_ROLLING_TITLE)), None)
    if start is None:
        return None
    rows: list[dict[str, str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        m = _ROW_RE.match(line)
        if m:
            rows.append(
                {"idx": m.group(1), "section": m.group(2), "date": m.group(3), "lesson": m.group(4)}
            )
    return rows


def check_memory(text: str) -> list[str]:
    """错误记忆库自身的判据（滚动索引三条）。"""
    errors: list[str] = []
    rows = rolling_rows(text)
    if rows is None:
        return ["缺少 `## 零、滚动最新 10 条` 索引节（开工只读这一节，B5-9）"]

    if len(rows) != ROLLING_SIZE:
        errors.append(
            f"滚动索引必须是 {ROLLING_SIZE} 行（实际 {len(rows)} 行）——多了/少了都会让索引失真"
        )

    secs = headings(text)
    rolled: list[int] = []
    for r in rows:
        try:
            n = cn_to_int(r["section"])
        except ValueError as exc:
            errors.append(f"滚动索引节号无法解析：{r['section']!r}（{exc}）")
            continue
        rolled.append(n)
        if n not in secs:
            errors.append(f"滚动索引指向不存在的小节：{r['section']}（悬空指针）")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"]):
            errors.append(f"滚动索引第 {n} 节日期格式应为 YYYY-MM-DD：{r['date']!r}")
        if len(r["lesson"]) < 6:
            errors.append(f"滚动索引第 {n} 节的一句话教训太短（<6 字）：{r['lesson']!r}")

    if len(set(rolled)) != len(rolled):
        errors.append(f"滚动索引有重复节号：{sorted(rolled)}")
    if len(rolled) == ROLLING_SIZE and not errors:
        newest = sorted(secs, reverse=True)[:ROLLING_SIZE]
        if sorted(rolled) != sorted(newest):
            errors.append(
                f"滚动索引不是最新 {ROLLING_SIZE} 节：索引={sorted(rolled)}，应为={sorted(newest)}"
                "（写了新节必须同时滚动索引）"
            )
        if rolled != sorted(rolled, reverse=True):
            errors.append(f"滚动索引必须新→旧排序（实际 {rolled}）")
    return errors


def batches_in_memory(mem_text: str) -> set[int]:
    """记忆库里出现过的批次号集合（**中文数字**，如「第一百九十一批」→ 191）。

    为什么必须转成 int 比：本判据首版直接 `f"第{191}批" in mem_text`——而正文里写的是
    「第一百九十一批」，**永远匹配不上**，于是门禁在真实库上判红（2026-11-27 实测）。
    文档用中文数字、代码用阿拉伯数字，这类"两种表示法"必须在校验边界上归一，
    否则判据看起来在跑，实际只会给假红/假绿。
    """
    out: set[int] = set()
    for raw in _BATCH_RE.findall(mem_text):
        try:
            out.add(cn_to_int(raw))
        except ValueError:
            continue
    # 正文也可能写阿拉伯数字（「第191批」）——两种表示法都要认，否则判据只认自己写的那一种
    out |= {int(n) for n in _ARABIC_BATCH_RE.findall(mem_text)}
    return out


def check_ledger(ledger_text: str, mem_text: str) -> list[str]:
    """LEDGER 的「教训/根因/新坑」批次行必须在本库有同批记录（含豁免期判红）。"""
    errors: list[str] = []
    hit_batches: set[int] = set()
    recorded = batches_in_memory(mem_text)
    for line in ledger_text.splitlines():
        if not line.startswith("|") or not _LESSON_RE.search(line):
            continue
        ordinals = []
        for raw in _BATCH_RE.findall(line):
            try:
                ordinals.append(cn_to_int(raw))
            except ValueError:
                continue
        ordinals = [n for n in ordinals if n >= MEM_CUTOFF_BATCH]
        if not ordinals:
            continue
        newest = max(ordinals)
        if newest in recorded or newest in LEDGER_EXEMPT:
            hit_batches.add(newest)
            continue
        errors.append(
            f"LEDGER 第 {newest} 批写有「教训/根因/新坑」，但错误记忆库没有该批记录"
            "（DoD 6：新坑入库；确属不补录的写进 LEDGER_EXEMPT 并写明理由）"
        )

    for batch, why in sorted(LEDGER_EXEMPT.items()):
        if batch not in hit_batches:
            errors.append(
                f"LEDGER_EXEMPT 条目「第 {batch} 批」已不再命中问题（理由：{why}）——请删除"
            )
    return errors


def run(mem_path: Path | None = None, ledger_path: Path | None = None) -> list[str]:
    mem = (mem_path or latest_memory_file()).read_text(encoding="utf-8")
    ledger = (ledger_path or LEDGER).read_text(encoding="utf-8")
    return check_memory(mem) + check_ledger(ledger, mem)


# ---------------------------------------------------------------- 自证


def _fixture_memory(n_sections: int = 12, rolling: int = ROLLING_SIZE) -> tuple[str, list[int]]:
    """造一个合法夹具库：n_sections 个编号小节 + 最新 rolling 节的索引。"""
    sections = list(range(101, 101 + n_sections))
    lines = [
        "# 夹具错误记忆库",
        "",
        _ROLLING_TITLE + "滚动最新 10 条",
        "",
        "| # | 节 | 日期 | 一句话教训 |",
        "|---|---|---|---|",
    ]
    for i, n in enumerate(sorted(sections, reverse=True)[:rolling], start=1):
        lines.append(f"| {i} | {int_to_cn(n)} | 2026-11-27 | 夹具教训第{i}条足够长 |")
    lines.append("")
    for n in sections:
        lines.append(f"## {int_to_cn(n)}、夹具小节 {n}")
        lines.append("内容")
    return "\n".join(lines) + "\n", sections


def self_check() -> int:
    ok = True
    mem, sections = _fixture_memory()
    ledger = "| 批次 | 说明 |\n|---|---|\n"

    # ① 合法夹具必须零误报
    errs = check_memory(mem) + check_ledger(ledger, mem)
    print(f"{'✓' if not errs else '✗'} 合法夹具零误报（{errs}）")
    ok &= not errs

    # ② 少一行滚动 → 必须红
    broken = mem.replace("| 10 | 一百零三 | 2026-11-27 | 夹具教训第10条足够长 |\n", "")
    errs = check_memory(broken)
    hit = any("必须是 10 行" in e for e in errs)
    print(f"{'✓' if hit else '✗'} 滚动索引少一行 → 判红（{errs[:1]}）")
    ok &= hit

    # ③ 索引落后（最新节没进索引）→ 必须红
    stale = mem + "\n## 一百二十、新写的小节\n内容\n"  # 追加新节但不滚动索引
    errs = check_memory(stale)
    hit = any("不是最新" in e for e in errs)
    print(f"{'✓' if hit else '✗'} 写了新节没滚动索引 → 判红（{errs[:1]}）")
    ok &= hit

    # ④ 悬空指针 → 必须红
    dangling = mem.replace("| 9 | 一百零四 |", "| 9 | 一百九十九 |")
    errs = check_memory(dangling)
    hit = any("不存在的小节" in e for e in errs)
    print(f"{'✓' if hit else '✗'} 索引指向不存在的小节 → 判红（{errs[:1]}）")
    ok &= hit

    # ⑤ LEDGER 新批写了教训但库里没有 → 必须红
    led = "| 第一百九十五批 | 某事 | DONE | **教训**：踩坑了 |\n"
    errs = check_ledger(led, mem)
    hit = any("第 195 批" in e for e in errs)
    print(f"{'✓' if hit else '✗'} LEDGER 新批教训未入库 → 判红（{errs[:1]}）")
    ok &= hit

    # ⑥ 同一条 LEDGER 行、库里已有该批 → 零误报
    mem_with = mem + "\n## 一百一十三、第195批的教训\n内容提到第195批\n"
    errs = check_ledger(led, mem_with)
    hit = not errs
    print(f"{'✓' if hit else '✗'} 库里已有该批 → 零误报（{errs}）")
    ok &= hit

    # ⑦ 记忆库用**中文数字**写批次（「第一百九十五批」）时也要认出来
    mem_cn = mem + "\n## 一百一十三、某批教训\n本轮第一百九十五批的教训\n"
    errs = check_ledger(led, mem_cn)
    hit = not errs
    print(f"{'✓' if hit else '✗'} 记忆库用中文数字写批次 → 认出（零误报，{errs}）")
    ok &= hit

    # ⑧ 豁免条目过期 → 必须红
    global LEDGER_EXEMPT
    saved = LEDGER_EXEMPT
    try:
        LEDGER_EXEMPT = {190: "夹具：本轮不追溯"}  # 190 < cutoff，永远不会命中
        errs = check_ledger(ledger, mem)
        hit = any("已不再命中" in e for e in errs)
    finally:
        LEDGER_EXEMPT = saved
    print(f"{'✓' if hit else '✗'} 豁免条目过期 → 判红（{errs[:1]}）")
    ok &= hit

    print()
    # 门禁约定（check_gate_selftest）：--self-test 的输出必须带 "S1"，否则视为"没自证"
    print("✓ S1 自证通过（8 类违例/正例全部命中，合法夹具零误报）" if ok else "✗ S1 自证失败")
    return 0 if ok else 1


def main() -> int:
    errors = run()
    if errors:
        print("✗ 错误记忆库判据失败：")
        for e in errors:
            print(f"  - {e}")
        return 1
    mem = latest_memory_file()
    text = mem.read_text(encoding="utf-8")
    print(
        f"✓ 错误记忆库判据通过：{mem.name}（{len(headings(text))} 节，"
        f"滚动索引 {ROLLING_SIZE} 条为最新；LEDGER 第 {MEM_CUTOFF_BATCH} 批起的教训均已入库）"
    )
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_check())
    sys.exit(main())
