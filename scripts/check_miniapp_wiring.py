#!/usr/bin/env python
"""小程序接线检查（2026-10-09，A1-11）——两条"现有七查读不到"的类。

为什么新增（上线前审查 P0-2 / P2-14；错误记忆库 §一百零七 同族）：

  **W1 `bind*` handler 必须存在**：`refund-apply.wxml` 写 `bindtap="onSubmit"`，而
  `refund-apply.js` 里**从未定义** `onSubmit` → 家长选好订单、填完原因、点「提交退款申请」
  **毫无反应**（微信对缺失 handler 静默忽略），资金类入口形同虚设。现有
  `check_miniapp_bindings` 只查**数据面**（wxml 读的变量是否进过 data），不看 handler 是否存在。

  **W2 插值类名必须能解析到样式**：`class="cat-{{item.cat}}"` 这类写法里，前缀片段（`cat-`）
  必须在**同页作用域** wxss 里有对应类定义（如 `.cat-english`），否则状态配色**静默失效**；
  R12（悬空类名）/R16（两端对账）只看静态类名，未加引号的插值片段两处都测不到。

  **W3 reader 入口必须传 id（禁整对象进 URL）**（2026-10-20 第八轮 P1-6）：
  `/api/miniapp/borrows` 与 `/continue-listening` 下发的字段**没有 `audio_url`**
  （前者连 `id` 都没有），而 reader 靠 `audio_url` 决定是否拉详情补全音频。
  把整对象塞进 `?book=` 的入口一旦 title 非空，reader 就判不出"要补详情"→
  **续听整条链路哑火**（没声音、进度不上报）——书架与首页两个主入口都踩了。
  规则：`reader/reader?` 之后**不得出现 `book=` 整对象参数**（只许 `book_id=`）。

  **W4 请求失败不得伪装成空/0**（2026-10-20 第八轮 P2-45）：
  `.catch(() => [])` / `.catch(() => null)` 这类"失败即当成空数据"的写法，会让页面把
  **网络/服务故障**渲染成"还没有在借的书 / 暂无订单 / 连续 0 天 / 入会后可查看"——
  家长把故障理解成"孩子没数据"，客服被误导（违反"错误不许伪装成 0/空"明线）。
  规则：`pages/**` 与 `components/**` 里**不得**出现这两种吞错写法；失败必须进
  **失败态**（`error-view` + 重试）或至少留一个显式的降级提示。

扫描面与判据的**假阴性补面**（2026-10-21 第八轮 P2-70，逐条实测取证）：
  · **事件绑定的三种写法都要认**：`bindtap="x"`（旧式）/ `bind:tap="x"`（冒号式，
    现行 WXML 语法）/ `bind:onRetry="x"`（自定义组件事件，事件名**驼峰**）。
    原正则只认 `bind` + 全小写 + 双引号，于是仓库里 **17 处 `bind:` 冒号式**
    （`bind:onRetry` / `bind:action` / `bind:pay`）**一处都没进检查**——handler 拼错
    同样静默失效（P0-2 退款死按钮的同族形状）。单引号绑定同理漏检。
  · **`custom-tab-bar/` 必须在扫描面内**：自定义 tabBar 是独立目录（不在 `pages/`
    也不在 `components/`），原扫描面把它的 wxml 整份漏掉——底部导航 5 个 tab 的
    `bindtap="switchTab"` 与 `{{list}}/{{selected}}/{{circleDot}}` 全在盲区里。

判据与自证：
  · 四类违例都必须为 0（新规则不设基线）；
  · S1 注入自检：内置违例样本必须被检出（防检查器自身假绿）——含 P2-70 新增的
    冒号式/单引号样本，正例（旧式绑定 + handler 存在）不得误报；
  · S2 空扫描自检：扫到 0 个 wxml = FAIL（防"路径写错→全绿"的盲区形状）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINIAPP = ROOT / "miniapp"
SCAN_DIRS = (
    MINIAPP / "pages",
    MINIAPP / "components",
    MINIAPP / "custom-tab-bar",  # P2-70：自定义 tabBar 独立目录，原扫描面整份漏掉
)

#: 事件绑定三种写法：`bindtap` / `bind:tap` / `bind:onRetry`（自定义组件驼峰事件名），
#: 单双引号都认。`catch` 同款。P2-70 实测：原正则（`bind` + `[a-z]+` + 双引号）
#: 对仓库里 17 处 `bind:...` 全部漏检。
BIND_RE = re.compile(r"\b(?:bind|catch):?([A-Za-z][\w-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
CLASS_ATTR_RE = re.compile(r"class\s*=\s*\"([^\"]*)\"")
INTERP_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
CLASS_DEF_RE = re.compile(r"\.([A-Za-z][\w-]*)")
IMPORT_RE = re.compile(r"@import\s+\"([^\"]+)\";")
#: W3：reader 入口传整对象（`?book={...}`）——`/borrows` 等接口不下发 audio_url，必哑火
READER_OBJ_PARAM_RE = re.compile("reader/reader\\?[^\"'`\n]*\\bbook=")
#: W4：把"请求失败"吞成空数据（`[]` / `null`）——失败会被渲染成"没有数据/0"
#: 覆盖两种写法：`() => []` / `() => null` / `function () { return [] }`。
#: 注意**不要**在 `[]` 后面加 `\b`——`]` 是非单词字符，`\b` 会永远匹配不上（自检当场抓到）。
SILENT_EMPTY_CATCH_RE = re.compile(
    r"\.catch\(\s*(?:\(\s*\)|function\s*\(\s*\))\s*=>\s*(?:\{\s*return\s+)?(?:\[\s*\]|null)"
    r"|\.catch\(\s*function\s*\(\s*\)\s*\{\s*return\s+(?:\[\s*\]|null)\s*;?\s*\}\s*\)"
)


def js_defines(js_text: str, name: str) -> bool:
    """js 里是否存在该方法（三种写法：`name(` / `name: (` / `name: function (`，均可带 async）。"""
    patterns = (
        rf"^\s*(?:async\s+)?{re.escape(name)}\s*\(",
        rf"^\s*{re.escape(name)}\s*:\s*(?:async\s*)?(?:function\s*)?\(",
    )
    return any(re.search(p, js_text, re.M) for p in patterns)


def scope_classes(wxml: Path, app_wxss: Path) -> set[str]:
    """同页作用域的类名集合：同目录 wxss（含 @import 链）+ app.wxss。"""
    out: set[str] = set()
    seen: set[Path] = set()

    def absorb(path: Path) -> None:
        if path in seen or not path.is_file():
            return
        seen.add(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for rel in IMPORT_RE.findall(text):
            absorb((path.parent / rel).resolve())
        for m in CLASS_DEF_RE.finditer(text):
            out.add(m.group(1))

    absorb(wxml.with_suffix(".wxss"))
    absorb(app_wxss)
    return out


def static_values(expr: str) -> list[str]:
    """能静态求值的插值 → 具体类名后缀（只认字面量与 `X % N`，N ≤ 12；其余返回空）。"""
    e = (expr or "").strip()
    if e.isdigit():
        return [e]
    m = re.fullmatch(r"[\w\s.]+%\s*(\d+)", e)
    if m and int(m.group(1)) <= 12:
        return [str(i) for i in range(int(m.group(1)))]
    return []


def scan(wxml_files: list[Path], app_wxss: Path | None = None) -> list[str]:
    app_wxss = app_wxss or (MINIAPP / "app.wxss")
    problems: list[str] = []
    for wxml in wxml_files:
        text = wxml.read_text(encoding="utf-8", errors="ignore")
        try:
            rel = wxml.relative_to(ROOT)
        except ValueError:  # 自检样本在临时目录
            rel = wxml
        js_path = wxml.with_suffix(".js")
        js_text = js_path.read_text(encoding="utf-8", errors="ignore") if js_path.is_file() else ""
        # W1：bind*/catch* 指向的方法必须存在（插值形式无法静态判定，跳过）
        # 三组捕获：事件名 / 双引号值 / 单引号值（P2-70：冒号式与单引号原先整类漏检）
        for _event, dq, sq in BIND_RE.findall(text):
            name = (dq or sq).strip()
            if not name or "{{" in name:
                continue
            if not js_text:
                problems.append(f"W1 {rel}: 有 {name} 绑定但同名 js 不存在")
            elif not js_defines(js_text, name):
                problems.append(
                    f"W1 {rel}: bind/catch 指向的 {name}() 在 {js_path.name} 里没有定义"
                )
        # W2：class 属性里的插值片段必须能解析到样式
        #   ① 前缀级：`cat-{{x}}` 至少要存在一个 `.cat-*` 定义；
        #   ② 可静态求值：`banner-{{index % 3}}` → 逐值要求 `.banner-0/1/2` 存在
        #      （数据驱动的插值枚举不出来，那类由 R16b 的"JS 提供的类名必须存在"兜）。
        classes = scope_classes(wxml, app_wxss)
        for value in CLASS_ATTR_RE.findall(text):
            if "{{" not in value:
                continue
            parts = INTERP_RE.split(value)  # [字面量, 表达式, 字面量, 表达式, ..., 字面量]
            for i in range(0, len(parts) - 1, 2):
                frag = (parts[i] or "").strip().split(" ")[-1] if (parts[i] or "").strip() else ""
                if not re.fullmatch(r"[A-Za-z][\w-]*-", frag):
                    continue
                values = static_values(parts[i + 1])
                if values:
                    for suffix in values:
                        if f"{frag}{suffix}" not in classes:
                            problems.append(
                                f"W2 {rel}: 插值类名 `{frag}{suffix}` 在本页作用域 wxss 里没有"
                                f"定义（状态样式会静默失效）"
                            )
                elif not any(c.startswith(frag) for c in classes):
                    problems.append(
                        f"W2 {rel}: 插值类名前缀 `{frag}` 在本页作用域 wxss 里没有对应类定义"
                        f"（状态样式会静默失效）"
                    )
    return problems


def scan_reader_entry(js_files: list[Path]) -> list[str]:
    """W3：reader 入口不得把整对象塞进 URL（只许传 `book_id`）。

    为什么是"必哑火"而不是"风格问题"：reader 以 `audio_url` 为唯一判据决定是否拉详情补全，
    而借阅/继续听接口**不下发 audio_url**——整对象入口一旦带 title，补详情的分支就永不触发
    → 音频实例不初始化（没声音、进度不上报）。2026-10-20 第八轮 P1-6 实测两处主入口。
    """
    problems: list[str] = []
    for js in js_files:
        text = js.read_text(encoding="utf-8", errors="ignore")
        if READER_OBJ_PARAM_RE.search(text):
            try:
                rel = js.relative_to(ROOT)
            except ValueError:
                rel = js
            problems.append(
                f"W3 {rel}: reader 入口传了整对象（`?book=`）——借阅/继续听接口不下发 "
                f"audio_url，reader 判不出要补详情 ⇒ 续听哑火；改传 `book_id=`"
            )
    return problems


def scan_silent_empty_catch(js_files: list[Path]) -> list[str]:
    """W4：页面里不得把请求失败吞成空数组/ null（失败会被渲染成"没有数据"）。

    为什么是"必违例"而不是"风格问题"：这类写法让**故障**与**业务空**在屏幕上长得一样
    （书架"还没有在借的书"、订单"暂无订单"、打卡"连续 0 天"、首页"入会后可查看"）——
    家长据此以为孩子没数据，客服据此误判。失败要显式进失败态（`error-view` + 重试）。
    """
    problems: list[str] = []
    for js in js_files:
        text = js.read_text(encoding="utf-8", errors="ignore")
        for m in SILENT_EMPTY_CATCH_RE.finditer(text):
            if in_comment(text, m.start()):
                continue  # 注释里的"举例"（如本规则的说明）不算违例
            try:
                rel = js.relative_to(ROOT)
            except ValueError:
                rel = js
            line = text[: m.start()].count("\n") + 1
            problems.append(
                f"W4 {rel}:{line}: 把请求失败吞成空数据（`{m.group(0)}`）——"
                f'失败会被渲染成"没有数据/0"；请改走失败态（error-view + 重试）'
            )
            break  # 每个文件报一处即可（同类问题看第一处就够定位）
    return problems


def in_comment(text: str, pos: int) -> bool:
    """`pos` 是否落在注释里（行注释 `//` 或块注释 `/* */`）。

    为什么需要：本规则的说明与页面的"改动原因注释"里会出现 `.catch(() => [])` 这类**举例**——
    它们是文档而不是代码；检查器把注释当代码，会把"解释这件事"本身判成违例（自检当场抓到）。
    """
    line_start = text.rfind("\n", 0, pos) + 1
    if "//" in text[line_start:pos]:
        return True
    return text.rfind("/*", 0, pos) > text.rfind("*/", 0, pos)


def selftest() -> list[str]:
    """S1 注入自检：违例样本必须命中（每条规则各一 + P2-70 新增的三种绑定写法）。"""
    import tempfile

    fails: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "app.wxss").write_text(".only-app {}\n", encoding="utf-8")
        bad = base / "demo.wxml"
        bad.write_text(
            '<view bindtap="onMissing" class="cat-{{item.cat}}">x</view>\n', encoding="utf-8"
        )
        (base / "demo.wxss").write_text(".other {}\n", encoding="utf-8")
        (base / "demo.js").write_text("Page({\n  onLoad() {},\n})\n", encoding="utf-8")
        hits = scan([bad], app_wxss=base / "app.wxss")
        if not any(p.startswith("W1") for p in hits):
            fails.append("S1: W1 未命中注入样本")
        if not any(p.startswith("W2") for p in hits):
            fails.append("S1: W2 未命中注入样本")
        # P2-70 ①：冒号式（含自定义组件驼峰事件名）与单引号绑定必须同样命中——
        # 原正则对这两类整类漏检（仓库里 17 处 `bind:` 一处都没进检查）
        colon = base / "colon.wxml"
        colon.write_text(
            '<error-view bind:onRetry="onMissingColon" />\n'
            "<view bind:action='onMissingSingle'>x</view>\n"
            '<view catch:tap="onMissingCatch">y</view>\n',
            encoding="utf-8",
        )
        (base / "colon.js").write_text("Page({\n  onLoad() {},\n})\n", encoding="utf-8")
        colon_hits = [p for p in scan([colon], app_wxss=base / "app.wxss") if p.startswith("W1")]
        for want in ("onMissingColon", "onMissingSingle", "onMissingCatch"):
            if not any(want in p for p in colon_hits):
                fails.append(f"S1: P2-70 冒号式/单引号绑定 {want} 未命中（假阴性回归）")
        # 正例：三种写法的 handler 都存在 → 必须零命中
        colon_ok = base / "colon_ok.wxml"
        colon_ok.write_text(
            '<error-view bind:onRetry="onRetryLoad" />\n'
            "<view bind:action='goBooks'>x</view>\n"
            '<view catch:tap="onTap">y</view>\n',
            encoding="utf-8",
        )
        (base / "colon_ok.js").write_text(
            "Page({\n  onRetryLoad() {},\n  goBooks() {},\n  onTap() {},\n})\n", encoding="utf-8"
        )
        colon_good = scan([colon_ok], app_wxss=base / "app.wxss")
        if colon_good:
            fails.append(f"S1: P2-70 三种绑定写法正例被误报：{colon_good}")
        # 正例：handler 存在 + 前缀有定义 → 必须零命中
        good = base / "ok.wxml"
        good.write_text('<view bindtap="onTap" class="cat-{{c}}">x</view>\n', encoding="utf-8")
        (base / "ok.js").write_text("Page({\n  onTap() {},\n})\n", encoding="utf-8")
        (base / "ok.wxss").write_text(".cat-english {}\n", encoding="utf-8")
        good_hits = scan([good], app_wxss=base / "app.wxss")
        if good_hits:
            fails.append(f"S1: 正例被误报：{good_hits}")

        # W3：整对象进 URL 必须命中；只传 id 必须零命中
        bad_js = base / "bad_nav.js"
        bad_js.write_text(
            "wx.navigateTo({ url: `/pages/reading-pkg/reader/reader?book=${encodeURIComponent("
            "JSON.stringify(b))}&child_id=1` })\n",
            encoding="utf-8",
        )
        w3_hits = scan_reader_entry([bad_js])
        if not any(p.startswith("W3") for p in w3_hits):
            fails.append("S1: W3 未命中注入样本")
        good_js = base / "ok_nav.js"
        good_js.write_text(
            "wx.navigateTo({ url: `/pages/reading-pkg/reader/reader?book_id=${id}&child_id=1` })\n",
            encoding="utf-8",
        )
        w3_good = scan_reader_entry([good_js])
        if w3_good:
            fails.append(f"S1: W3 正例被误报：{w3_good}")

        # W4：吞成空数据必须命中；显式失败态（catch 里 setData loadError）必须零命中
        bad_catch = base / "bad_catch.js"
        bad_catch.write_text("api.list().catch(() => [])\n", encoding="utf-8")
        w4_hits = scan_silent_empty_catch([bad_catch])
        if not any(p.startswith("W4") for p in w4_hits):
            fails.append("S1: W4 未命中注入样本")
        good_catch = base / "ok_catch.js"
        good_catch.write_text(
            "api.list().then((r) => this.setData({ rows: r })).catch((e) => {\n"
            "  this.setData({ loadError: e.message })\n"
            "})\n",
            encoding="utf-8",
        )
        w4_good = scan_silent_empty_catch([good_catch])
        if w4_good:
            fails.append(f"S1: W4 正例被误报：{w4_good}")
    return fails


def main() -> int:
    wxml_files = sorted(p for d in SCAN_DIRS if d.is_dir() for p in d.rglob("*.wxml"))
    if len(wxml_files) < 10:  # S2：盲区形状（路径写错 / 扫描面塌缩）
        print(f"FAIL(S2): 只扫到 {len(wxml_files)} 个 wxml——扫描面异常，检查 SCAN_DIRS")
        return 1
    js_files = sorted(MINIAPP.rglob("*.js"))
    if len(js_files) < 10:  # S2 同款：W3 的扫描面也得有东西
        print(f"FAIL(S2): 只扫到 {len(js_files)} 个 js——扫描面异常")
        return 1
    self_fails = selftest()
    if self_fails:
        print("FAIL(S1): " + "；".join(self_fails))
        return 1
    problems = scan(wxml_files) + scan_reader_entry(js_files) + scan_silent_empty_catch(js_files)
    print(f"扫描面: {len(wxml_files)} 个 wxml（pages + components）+ {len(js_files)} 个 js")
    print(f"W1 bind*/catch* handler 缺失: {sum(1 for p in problems if p.startswith('W1'))}")
    print(f"W2 插值类名无样式定义: {sum(1 for p in problems if p.startswith('W2'))}")
    print(f"W3 reader 入口传整对象: {sum(1 for p in problems if p.startswith('W3'))}")
    print(f"W4 失败吞成空数据: {sum(1 for p in problems if p.startswith('W4'))}")
    if problems:
        print("\n违例明细：")
        for p in problems:
            print("  ✗", p)
        print("\nFAIL: 接线检查不通过（W1~W4 必须为 0）")
        return 1
    print(
        "PASS: 小程序接线四条规则全零（handler 存在 / 插值类名可解析 / reader 入口传 id / 失败不吞成空）✓"
    )
    return 0


if __name__ == "__main__":
    # `--self-test`：只跑 S1 注入自检（gate 会先跑它，再跑主流程——第八轮审查 P1-10）
    if "--self-test" in sys.argv:
        _fails = selftest()
        if _fails:
            print("FAIL(S1): " + "；".join(_fails))
            sys.exit(1)
        print("✓ S1 注入自检通过（W1~W4 违例命中 + 冒号式/单引号绑定命中 + 正例零误报）")
        sys.exit(0)
    sys.exit(main())
