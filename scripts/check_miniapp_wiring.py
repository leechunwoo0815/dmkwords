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
  · 六类违例都必须为 0（新规则不设基线）；
  · S1 注入自检：内置违例样本必须被检出（防检查器自身假绿）——含 P2-70 新增的
    冒号式/单引号样本，正例（旧式绑定 + handler 存在）不得误报；
  · S2 空扫描自检：扫到 0 个 wxml = FAIL（防"路径写错→全绿"的盲区形状）。

**W6（2026-10-24 第一百三十四批，IDE 实测抓到）**：调用 `utils/*` 的导出符号但本文件没有
`require` ⇒ 模块顶层 `ReferenceError`，**整个分包页面注册失败**（reading 分包 5 页全打不开）。
前七查（W1–W5 + bindings + style + media）一条都覆盖不到"用了但没导入"。
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

#: W4 **第二形态**（2026-10-22 第一百一十三批）：`try { … } catch (e) { this.setData({ rows: [] }) }`
#: ——同一条规则，但原先只认内联 `.catch(() => [])`，于是预约页/榜单页那种
#: `catch (e) { this.setData({ items: [] }) }` **一路绿灯**（第八轮 P3 实测：那两页确实把
#: 失败渲染成"还没有预约 / 整页白屏"）。判据：catch 块里出现 `setData({ <键>: [] })`，且
#: 同块内**没有任何**失败态或提示（loadError / noChild / error / toast / message）→ 违例。
#: **逃生口**：catch 块内写明「降级」二字的注释 = 有意的次级功能降级（如 circle.js 的点赞
#: 通知条失败不显示条、信息流照常）——与 `check_fake_assertions` 的"同行注释才是逃生口"同款。
CATCH_BLOCK_RE = re.compile(r"catch\s*\([^)]*\)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", re.S)
SILENT_EMPTY_IN_CATCH_RE = re.compile(r"setData\(\s*\{[^}]*:\s*\[\s*\]")
FAILURE_MARK_RE = re.compile(r"loadError|noChild|error|toast|message|showToast|降级")

#: W4 **第三形态**（2026-10-24 第一百三十五批，IDE 实测抓到）：**资金/记录类页面的加载函数**里，
#: catch 必须落到**可见失败态**（`loadError|listError|condError|noChild|…Error:`），
#: "什么都不做（只靠 request.js 的 toast）"或"只把 loading 置 false"都算违例。
#:
#: 为什么只扫这 6 页：前两形态只认"吞成空数据"，而押金页的真实形态是
#: `catch (e) { /* 已 toast */ }`——toast 几秒后消失，页面显示「暂无流水记录」，
#: 与"确实没有流水"长得一模一样（退款申请页同款：显示「没有可退款的订单」）。
#: 全仓铺开不行的原因：`index`/`circle`/`book-detail` 等处大量静默 catch 是**有意的次级
#: 功能降级**（轮播、点赞通知条、预检放行），一刀切会制造几十处噪音改动。
#: 判据只覆盖"失败=空会直接误导家长的钱/记录"的页面；**新增加这类页面时把路径加进来**。
MONEY_RECORD_PAGES = {
    "miniapp/pages/order-pkg/deposit/deposit.js",
    "miniapp/pages/order-pkg/order-history/order-history.js",
    "miniapp/pages/order-pkg/refund-apply/refund-apply.js",
    "miniapp/pages/order-pkg/reservation/reservation.js",
    "miniapp/pages/order-pkg/withdrawal/withdrawal.js",
    "miniapp/pages/order-pkg/benefit-transfer/benefit-transfer.js",
}
#: 加载路径的函数名（onShow 会同步调 load()；onLoad 直接拉数的页面也在内）
LOAD_FN_RE = re.compile(
    r"^\s*(?:async\s+)?(load\w*|onShow|onLoad|onRetry\w*)\s*\([^)]*\)\s*\{", re.M
)
#: 可见失败态标记（**不含 toast**——瞬时提示不算"页面当前状态"）。
#: 两种形态都认：`setData({ loadError: … })`（页面级 Banner）与 `settlementError = true`
#: （行级标记，由 wxml 逐行渲染「明细取不到」——withdrawal 页的实际做法）。
ERROR_STATE_MARK_RE = re.compile(
    r"loadError|listError|condError|listFailed|noChild|\w*[Ee]rror\s*[:=]"
)

#: W5 **远程图必须带加载失败兜底**（2026-10-22 第一百一十六批；第八轮 P3 实测）：
#: 全仓 135 个 `<image>` 里 `binderror` 出现 **0 次**。封面/活动封面/帖子配图/二维码这些
#: `src` 来自后端（URL 由 `utils/media.fullUrl` 拼 baseURL + token），一旦文件被运维删了、
#: token 过期、图片接口 5xx，`<image>` 会停在**空白/裂图**上——家长看到的是"这本书没有
#: 封面""孩子头像没了"，而不是"加载失败"。
#:
#: 判据：`src` 插值表达式里出现**远程资源字段名** ⇒ 该 `<image>` 必须带 `binderror`
#: （处理器清空该字段 → 翻到同页既有的 `wx:else` 占位分支，实现见 `miniapp/utils/img.js`）。
#: 只认下面这组字段名：本地包内资产（`/icons/ui/*.png`、`item.iconUrl` 之类）不在内——
#: 它们在包里不会 404，要求一遍只会制造 100+ 处噪音改动。
#:
#: **已知盲区（如实登记）**：字段改了名（如 `item.cover`）就测不到——这是**命名型**规则，
#: 不是数据流分析；新增远程图字段时把名字加进 `REMOTE_IMG_FIELDS` 并跑 `--self-test`。
REMOTE_IMG_FIELDS = (
    "cover_url",
    "image_url",
    "imageUrls",
    "thumbUrl",
    "imageUrl",
    "qrImage",
    "bcImage",
)
IMAGE_TAG_RE = re.compile(r"<image\b[^>]*?>", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

#: W6 **共享 utils 的导出符号必须先 require 才能用**（2026-10-24 第一百三十四批；
#: 微信开发者工具实测抓到，前七查全部为绿）：
#:
#: `pages/reading-pkg/reader/reader.js` 在 `data` 里调用 `getConfigs()`，却从未
#: `require('../../../utils/configs')`（2026-10-21 E5 批次改动漏了 import）。
#: 结果是 **整个 reading 分包**（reader / book-detail / quiz / quiz-result / vocabulary）
#: 在模块加载阶段抛 `ReferenceError: getConfigs is not defined` → 分包 bundle 注册失败，
#: 五个页面**全部打不开**（IDE console 只有 "Page ... has not been registered yet"）。
#: 静态检查器当时一条都没报：W1 看 wxml 的 handler、W2 看类名、W3 看 reader 入参、
#: W4 看吞错、W5 看图片兜底——**没有人看"用了但没导入"**。
#:
#: 判据：从 `miniapp/utils/*.js` 的 `module.exports = { … }` 提取导出名（只取
#: 长度 ≥ 5 且非全大写的标识符，避免 `get`/`post`/常量这类通用名误报）；页面/组件
#: 文件里出现 `名字(` 调用、跨文件未 require、本文件也没有本地定义 ⇒ 违例。
#: 已知盲区（如实登记）：拼接调用 `obj[name](…)`、解构后的别名、只当值用不当函数调用的
#: 符号都不在判据内——本规则只覆盖"整包挂掉"这一类最常见形状。
UTIL_DIR = MINIAPP / "utils"
EXPORT_BLOCK_RE = re.compile(r"module\.exports\s*=\s*\{(.*?)\}", re.S)
EXPORT_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
REQUIRE_PATH_RE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
#: `module.exports = { … }` 里会出现 `get(path) { return request(…) }` 这类**函数体**，
#: 逐词扫描会把 `return`/`function` 当成导出名（主流程首跑就报了 `return()` 假阳性）——
#: 关键字与内建一律剔除，只留真正的导出标识符。
JS_KEYWORDS = {
    "async",
    "await",
    "break",
    "case",
    "catch",
    "const",
    "continue",
    "default",
    "delete",
    "else",
    "exports",
    "false",
    "finally",
    "for",
    "function",
    "if",
    "in",
    "instanceof",
    "let",
    "module",
    "new",
    "null",
    "of",
    "return",
    "switch",
    "this",
    "throw",
    "true",
    "try",
    "typeof",
    "undefined",
    "var",
    "void",
    "while",
}


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


def scan_remote_img_fallback(wxml_files: list[Path]) -> list[str]:
    """W5：`src` 来自后端的 `<image>` 必须带 `binderror`（见 `REMOTE_IMG_FIELDS` 的说明）。

    先剥 `<!-- -->` 注释：注释里出现的 `<image>`（这些页面的注释常写"展示用 `<image>`"）
    不是真实标签，把它当标签就会报出幽灵违例（本规则首版就踩过）。
    """
    problems: list[str] = []
    for wxml in wxml_files:
        raw = wxml.read_text(encoding="utf-8", errors="ignore")
        text = HTML_COMMENT_RE.sub(lambda m: " " * len(m.group(0)), raw)
        try:
            rel = wxml.relative_to(ROOT)
        except ValueError:  # 自检样本在临时目录
            rel = wxml
        for m in IMAGE_TAG_RE.finditer(text):
            tag = m.group(0)
            src = re.search(r'src="([^"]*)"', tag)
            if not src or "{{" not in src.group(1):
                continue
            if not any(fld in src.group(1) for fld in REMOTE_IMG_FIELDS):
                continue
            if "binderror" in tag:
                continue
            line = text[: m.start()].count("\n") + 1
            problems.append(
                f'W5 {rel}:{line}: 远程图 `<image src="{src.group(1)}">` 没有 binderror 兜底——'
                f"文件缺失/token 过期/接口 5xx 时会停在空白或裂图；"
                f'补 `data-img-path="<要清空的 data 路径>" binderror="onImgError"`'
                f"（实现见 miniapp/utils/img.js）"
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

        # W4 第二形态：`try/catch` 里把失败吞成空数组（内联形态之外的那一半）
        for m in CATCH_BLOCK_RE.finditer(text):
            body = m.group(1)
            if not SILENT_EMPTY_IN_CATCH_RE.search(body):
                continue
            if FAILURE_MARK_RE.search(body):
                continue  # 同块内有失败态/提示/「降级」说明 → 不是静默吞错
            if in_comment(text, m.start()):
                continue
            try:
                rel = js.relative_to(ROOT)
            except ValueError:
                rel = js
            line = text[: m.start()].count("\n") + 1
            problems.append(
                f"W4 {rel}:{line}: catch 里把请求失败吞成空数组（`setData({{ …: [] }})`）——"
                f'失败会被渲染成"没有数据"；请改走失败态（error-view + 重试）；'
                f"确属次级功能的降级请在注释里写明「降级」"
            )
            break
    return problems


def load_function_bodies(text: str):
    """产出加载路径的 `(函数名, 函数体, 起始行号)`（函数名见 `LOAD_FN_RE`）。

    函数体用**花括号配平**取（跳过引号内的括号，避免把字符串里的 `{` 算进去）——
    正则版 `[^{}]*` 一遇到嵌套对象就断，取不到整段 catch。
    """
    for m in LOAD_FN_RE.finditer(text):
        start = m.end() - 1  # 指向函数体的 `{`
        depth = 0
        i = start
        quote = ""
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
            elif ch in "'\"`":
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield m.group(1), text[start + 1 : i], text[:start].count("\n") + 1
                    break
            i += 1


def scan_load_catch_state(js_files: list[Path], pages: set[str] | None = None) -> list[str]:
    """W4c：资金/记录类页面的**加载函数**里，catch 必须落到可见失败态（见 MONEY_RECORD_PAGES）。

    只扫加载路径：`onCancel` / `onSubmit` 这类**动作**的 catch 弹 toast 是合理 UX，
    不在此判据内（把它们一起判红会逼着给所有动作加常驻横幅）。
    """
    pages = MONEY_RECORD_PAGES if pages is None else pages
    problems: list[str] = []
    for js in js_files:
        try:
            rel = js.relative_to(ROOT).as_posix()
        except ValueError:  # 自检样本在临时目录
            rel = js.as_posix()
        if rel not in pages:
            continue
        text = js.read_text(encoding="utf-8", errors="ignore")
        for fn_name, body, fn_line in load_function_bodies(text):
            for m in CATCH_BLOCK_RE.finditer(body):
                block = m.group(1)
                if ERROR_STATE_MARK_RE.search(block) or "降级" in block:
                    continue
                line = fn_line + body[: m.start()].count("\n")
                problems.append(
                    f"W4 {rel}:{line}: 加载函数 `{fn_name}()` 的 catch 没落到可见失败态——"
                    f"失败会被渲染成「没有数据」（toast 一过就没人知道出过错）；"
                    f"补 `loadError` + error-view + 重试；确属次级功能降级请写明「降级」"
                )
    return problems


def util_exports(util_dir: Path | None = None) -> dict[str, list[Path]]:
    """`{导出名: [所属 utils 文件, …]}`——只收长度 ≥ 5 且非全大写的标识符。"""
    util_dir = util_dir or UTIL_DIR
    out: dict[str, list[Path]] = {}
    if not util_dir.is_dir():
        return out
    for path in sorted(util_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in exports_from_source(text):
            out.setdefault(name, []).append(path.resolve())
    return out


def strip_js_comments(text: str) -> str:
    """去掉 `//` 行注释与 `/* */` 块注释（字符串内的 `//` 不动）。

    为什么要剥：`utils/api.js` 的导出块里夹着大段中文说明，里面有 `openid`/`need_bind`
    这类词——不剥注释会把**注释里的词**当成导出名（W6 首版就是这么报出 6 个假导出的）。
    """
    out: list[str] = []
    i = 0
    quote = ""
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = len(text) if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def brace_body(clean: str, open_index: int) -> str:
    """取 `open_index` 处 `{` 起、配平到对应 `}` 的**对象体**。

    参数必须是**已 `strip_js_comments` 的文本**：两边索引必须同一坐标系——首版在函数内
    现剥一份、却用原文的索引去切，结果切到错位置（导出名解析直接归零，自检当场抓到）。
    """
    depth = 0
    i = open_index
    while i < len(clean):
        ch = clean[i]
        if ch in "'\"`":
            quote = ch
            i += 1
            while i < len(clean):
                if clean[i] == "\\":
                    i += 2
                    continue
                if clean[i] == quote:
                    break
                i += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return clean[open_index + 1 : i]
        i += 1
    return clean[open_index + 1 :]


def object_top_level_keys(body: str) -> list[str]:
    """对象体里**顶层**的键名（跳过嵌套对象/数组/函数体里的名字）。

    为什么必须按顶层切：非贪心正则遇到第一个 `}` 就停 —— `utils/api.js` 的导出块
    13866 字符只能取到 257 字符（77 个导出只收录 6 个，**判据塌缩**，2026-10-24 W8 包实测）。
    """
    keys: list[str] = []
    depth = 0
    quote = ""
    seg: list[str] = []
    segs: list[str] = []

    def flush(buf: list[str]) -> None:
        m = re.match(
            r"\s*(?:async\s+)?(?:function\s+)?([A-Za-z_$][\w$]*)\s*(?::|\(|,|$)", "".join(buf)
        )
        if m:
            keys.append(m.group(1))

    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            seg.append(ch)
            if ch == "\\":
                if i + 1 < len(body):
                    seg.append(body[i + 1])
                    i += 2
                    continue
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            seg.append(ch)
        elif ch in "{[(":
            depth += 1
            seg.append(ch)
        elif ch in "}])":
            depth -= 1
            seg.append(ch)
        elif ch == "," and depth == 0:
            segs.append("".join(seg))
            seg = []
        else:
            seg.append(ch)
        i += 1
    segs.append("".join(seg))
    for s in segs:
        flush([s])
    return keys


def exports_from_source(text: str) -> list[str]:
    """从源码里取 `module.exports = { … }` 的**顶层导出名**（长度 ≥5、非全大写、非关键字）。"""
    names: list[str] = []
    clean = strip_js_comments(text)
    for m in re.finditer(r"module\.exports\s*=\s*\{", clean):
        body = brace_body(clean, m.end() - 1)
        for name in object_top_level_keys(body):
            if len(name) < 5 or name.isupper() or name in JS_KEYWORDS:
                continue
            names.append(name)
    return sorted(set(names))


def js_defines_name(text: str, name: str) -> bool:
    """本文件里是否自己定义了这个名字（函数声明 / 变量 / 对象方法 / 对象键）。"""
    patterns = (
        rf"function\s+{re.escape(name)}\b",
        rf"\b(?:const|let|var)\s+{re.escape(name)}\b",
        rf"^\s*{re.escape(name)}\s*[:=]",
        rf"^\s*(?:async\s+)?{re.escape(name)}\s*\([^)]*\)\s*\{{",
    )
    return any(re.search(p, text, re.M) for p in patterns)


def scan_missing_util_require(
    js_files: list[Path], exports: dict[str, list[Path]] | None = None
) -> list[str]:
    """W6：调用了 `utils/*` 的导出符号，却没有 require 它所属的模块（见上方说明）。

    运行时后果不是"少个功能"，而是 `ReferenceError` 打在**模块顶层**——同一分包的所有
    页面一起注册失败。所以这里按"必违例"处理，不设基线。
    """
    exports = util_exports() if exports is None else exports
    problems: list[str] = []
    util_root = UTIL_DIR.resolve()
    for js in js_files:
        if js.resolve().parent == util_root:
            continue  # utils 内部互相 require 不在判据内（它们本来就在同一目录）
        text = js.read_text(encoding="utf-8", errors="ignore")
        required: set[Path] = set()
        for raw in REQUIRE_PATH_RE.findall(text):
            base = (js.parent / raw).resolve()
            required.add(base.with_suffix("") if base.suffix else base)
        for name, owners in exports.items():
            m = re.search(rf"(?<![\w.]){re.escape(name)}\s*\(", text)
            if not m or in_comment(text, m.start()):
                continue
            if js_defines_name(text, name):
                continue
            if any(owner.with_suffix("") in required for owner in owners):
                continue
            try:
                rel = js.relative_to(ROOT)
            except ValueError:
                rel = js
            line = text[: m.start()].count("\n") + 1
            mods = "、".join(sorted({f"utils/{o.stem}" for o in owners}))
            problems.append(
                f"W6 {rel}:{line}: 调用了 `{name}()` 但本文件没有 require（对应 {mods}）——"
                f"运行时会抛 ReferenceError，且**同分包页面全部注册失败**"
            )
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

        # W4 第二形态（try/catch）：注入违例必须命中；带「降级」注释的次级功能必须零命中
        bad_try = base / "bad_try_catch.js"
        bad_try.write_text(
            "async load() {\n"
            "  try {\n"
            "    const r = await api.list()\n"
            "    this.setData({ items: r })\n"
            "  } catch (e) {\n"
            "    this.setData({ items: [] })\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        if not any(p.startswith("W4") for p in scan_silent_empty_catch([bad_try])):
            fails.append("S1: W4 第二形态（try/catch 吞成空数组）未命中注入样本")
        ok_try = base / "ok_try_catch.js"
        ok_try.write_text(
            "async load() {\n"
            "  try {\n"
            "    const r = await api.list()\n"
            "    this.setData({ items: r })\n"
            "  } catch (e) {\n"
            "    this.setData({ items: [], loadError: e.message })\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        if scan_silent_empty_catch([ok_try]):
            fails.append(
                f"S1: W4 第二形态正例（带 loadError）被误报：{scan_silent_empty_catch([ok_try])}"
            )
        degrade_try = base / "degrade_try_catch.js"
        degrade_try.write_text(
            "async load() {\n"
            "  try {\n"
            "    const r = await api.list()\n"
            "    this.setData({ items: r })\n"
            "  } catch (e) {\n"
            "    // 通知条失败不影响信息流（降级：不显示条）\n"
            "    this.setData({ items: [] })\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        if scan_silent_empty_catch([degrade_try]):
            fails.append(
                f"S1: W4 第二形态「降级」逃生口被误报：{scan_silent_empty_catch([degrade_try])}"
            )

        # W5：远程图缺兜底必须命中；带 binderror / 本地资产 / 注释里的举例都必须零命中
        bad_img = base / "bad_img.wxml"
        bad_img.write_text(
            '<image src="{{item.cover_url}}" mode="aspectFill" />\n'
            '<image src="{{rep.imageUrls[0]}}" mode="aspectFill" />\n'
            '<image src="{{sharedPost.image_url}}" mode="widthFix" />\n',
            encoding="utf-8",
        )
        w5_hits = scan_remote_img_fallback([bad_img])
        for want in ("cover_url", "imageUrls", "image_url"):
            if not any(want in p for p in w5_hits):
                fails.append(f"S1: W5 未命中注入样本（{want}）")
        good_img = base / "ok_img.wxml"
        good_img.write_text(
            '<image src="{{item.cover_url}}" data-img-path="books[{{index}}].cover_url" binderror="onImgError" />\n'
            '<image src="/icons/ui/book.png" mode="aspectFit" />\n'
            '<image src="{{item.iconUrl}}" mode="aspectFit" />\n'
            '<!-- 说明：展示用 <image src="{{x.cover_url}}"> 是历史写法 -->\n',
            encoding="utf-8",
        )
        w5_good = scan_remote_img_fallback([good_img])
        if w5_good:
            fails.append(f"S1: W5 正例被误报（本地资产/注释/已带兜底）：{w5_good}")

        # W6：调 utils 导出但没 require 必须命中；require 过 / 本文件自己定义 必须零命中
        fake_utils = base / "utils"
        fake_utils.mkdir()
        (fake_utils / "configs.js").write_text(
            "module.exports = { loadConfigs, getConfigs }\n", encoding="utf-8"
        )
        fake_exports = util_exports(fake_utils)
        bad_require = base / "bad_require.js"
        bad_require.write_text(
            "Page({\n  data: { cfg: getConfigs() },\n  onLoad() { loadConfigs() },\n})\n",
            encoding="utf-8",
        )
        w6_hits = scan_missing_util_require([bad_require], fake_exports)
        for want in ("getConfigs", "loadConfigs"):
            if not any(want in p for p in w6_hits):
                fails.append(f"S1: W6 未命中注入样本（{want}）")
        ok_require = base / "ok_require.js"
        ok_require.write_text(
            "const { loadConfigs, getConfigs } = require('./utils/configs')\n"
            "Page({\n  data: { cfg: getConfigs() },\n  onLoad() { loadConfigs() },\n})\n",
            encoding="utf-8",
        )
        if scan_missing_util_require([ok_require], fake_exports):
            fails.append(
                f"S1: W6 正例（已 require）被误报：{scan_missing_util_require([ok_require], fake_exports)}"
            )
        local_def = base / "local_def.js"
        local_def.write_text(
            "function getConfigs() { return {} }\nPage({\n  data: { cfg: getConfigs() },\n})\n",
            encoding="utf-8",
        )
        if scan_missing_util_require([local_def], fake_exports):
            fails.append("S1: W6 正例（本文件自己定义）被误报")

        # W4 第三形态：资金/记录页的**加载函数** catch 必须落到可见失败态
        money_bad = base / "money_bad.js"
        money_bad.write_text(
            "Page({\n  async load() {\n    try {\n      this.setData({ rows: await api.list() })\n"
            "    } catch (e) { /* toast 已弹 */ }\n  },\n})\n",
            encoding="utf-8",
        )
        money_good = base / "money_good.js"
        money_good.write_text(
            "Page({\n  async load() {\n    try {\n      this.setData({ rows: await api.list() })\n"
            "    } catch (e) { this.setData({ loadError: e.message, rows: [] }) }\n  },\n})\n",
            encoding="utf-8",
        )
        money_degrade = base / "money_degrade.js"
        money_degrade.write_text(
            "Page({\n  async load() {\n    try {\n      this.setData({ rows: await api.list() })\n"
            "    } catch (e) {\n      // 降级：次级卡片失败不影响主列表\n      this.setData({ rows: [] })\n    }\n  },\n})\n",
            encoding="utf-8",
        )
        money_action = base / "money_action.js"
        money_action.write_text(
            "Page({\n  async onCancel() {\n    try {\n      await api.cancel(1)\n"
            "    } catch (e) { /* toast 已弹（动作类，不需要常驻横幅） */ }\n  },\n})\n",
            encoding="utf-8",
        )
        money_pages = {money_bad.as_posix(), money_good.as_posix()}
        money_pages |= {money_degrade.as_posix(), money_action.as_posix()}
        if not any(p.startswith("W4") for p in scan_load_catch_state([money_bad], money_pages)):
            fails.append("S1: W4 第三形态未命中注入样本（加载函数里 catch 什么都不做）")
        for sample, why in (
            (money_good, "带 loadError"),
            (money_degrade, "写明「降级」"),
            (money_action, "动作类 catch"),
        ):
            hits = scan_load_catch_state([sample], money_pages)
            if hits:
                fails.append(f"S1: W4 第三形态正例（{why}）被误报：{hits}")

        # W6 解析面：「嵌套对象之后的导出」必须收录。首版非贪心正则取到第一个 `}` 就停，
        # `utils/api.js` 的 78 个导出只收录 6 个（判据塌缩，2026-10-24 W8 包实测）。
        nested_utils = base / "nested_utils"
        nested_utils.mkdir(exist_ok=True)
        (nested_utils / "extra.js").write_text(
            "module.exports = {\n"
            "  // 说明文字里的词 commentOnlyWord 不该被当成导出\n"
            "  inner() { return { a: 1 } },\n"
            "  deepKey: { nested: { x: 1 } },\n"
            "  targetCallName() { return 1 },\n"
            "}\n",
            encoding="utf-8",
        )
        nested_exports = util_exports(nested_utils)
        if "targetCallName" not in nested_exports:
            fails.append("S1: W6 嵌套导出解析塌缩（`targetCallName` 未收录）")
        if "commentOnlyWord" in nested_exports:
            fails.append("S1: W6 把注释里的词当成了导出名")
        nested_page = base / "nested_page.js"
        nested_page.write_text("Page({\n  onLoad() { targetCallName() },\n})\n", encoding="utf-8")
        if not scan_missing_util_require([nested_page], nested_exports):
            fails.append("S1: W6 嵌套导出样本未命中（旧解析器在这里是假绿）")
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
    problems = (
        scan(wxml_files)
        + scan_reader_entry(js_files)
        + scan_silent_empty_catch(js_files)
        + scan_remote_img_fallback(wxml_files)
        + scan_missing_util_require(js_files)
        + scan_load_catch_state(js_files)
    )
    print(f"扫描面: {len(wxml_files)} 个 wxml（pages + components）+ {len(js_files)} 个 js")
    print(f"W1 bind*/catch* handler 缺失: {sum(1 for p in problems if p.startswith('W1'))}")
    print(f"W2 插值类名无样式定义: {sum(1 for p in problems if p.startswith('W2'))}")
    print(f"W3 reader 入口传整对象: {sum(1 for p in problems if p.startswith('W3'))}")
    print(f"W4 失败吞成空数据/加载函数无失败态: {sum(1 for p in problems if p.startswith('W4'))}")
    print(f"W5 远程图缺 binderror 兜底: {sum(1 for p in problems if p.startswith('W5'))}")
    print(f"W6 调 utils 导出但未 require: {sum(1 for p in problems if p.startswith('W6'))}")
    if problems:
        print("\n违例明细：")
        for p in problems:
            print("  ✗", p)
        print("\nFAIL: 接线检查不通过（W1~W6 必须为 0）")
        return 1
    print(
        "PASS: 小程序接线六条规则全零（handler 存在 / 插值类名可解析 / reader 入口传 id / "
        "失败不吞成空（含资金页加载失败态）/ 远程图带兜底 / utils 符号先 require）✓"
    )
    return 0


if __name__ == "__main__":
    # `--self-test`：只跑 S1 注入自检（gate 会先跑它，再跑主流程——第八轮审查 P1-10）
    if "--self-test" in sys.argv:
        _fails = selftest()
        if _fails:
            print("FAIL(S1): " + "；".join(_fails))
            sys.exit(1)
        print(
            "✓ S1 注入自检通过（W1~W6 违例命中 + 冒号式/单引号绑定命中 + "
            "本地资产/注释/已带兜底/已 require 正例零误报）"
        )
        sys.exit(0)
    sys.exit(main())
