# 第十轮整改 B2 批 · 微信开发者工具端上证据（S288 / S289）

> 采集：2026-10-25 11:00–11:30（本地）｜方式：`wechatide` CLI（skill 0.3.14）→ 模拟器 → 后端 `http://127.0.0.1:8002`（dev，健康）
> 上一轮为什么缺这条证据：CLI 的网络层走代理 `127.0.0.1:7890`，代理下线时报
> `tunneling socket could not be established … connect ECONNREFUSED 127.0.0.1:7890` → `APPID_ERROR`。
> 2026-10-25 甲方开启代理后通道恢复（`check_wechatide_status` → `ok:true`、`loginExpired:false`）。
> 复现入口：`wechatide -c zcode check_wechatide_status` → `simulator_open_page` / `simulator_screenshot` /
> `automation_page_action` / `automation_element_action` / `automation_evaluate`。

会话态注入（**不是** UI 登录，是直接写模拟器存储与 globalData，等价于"已登录且已选孩子"）：

```js
// 演示账号 13800008888（固定码 1234）在 shell 里取真 token，再注入模拟器
wx.setStorageSync('token', <token>); wx.setStorageSync('parent', <parent>);
wx.setStorageSync('children', <children>); wx.setStorageSync('currentChildId', 1);
getApp().globalData.token = <token>; getApp().globalData.currentChild = <children[0]>;
```

失败态注入：`getApp().globalData.baseURL = 'http://127.0.0.1:9'`（死端口）。
⚠️ 光改 `globalData` 会被"重新编译/重启"抹掉（`app.js` 每次启动按 `dev_base_url` 存储或默认值重算），
要跨重启生效必须同时 `wx.setStorageSync('dev_base_url', 'http://127.0.0.1:9')`；取证完记得
`wx.removeStorageSync('dev_base_url')`（本轮已清理）。

---

## 一、S288 · 六页失败态（健康态 / 故障态成对）

| 页 | 健康态 page data（实测） | 故障态 page data（实测） | 截图 |
|---|---|---|---|
| 消息中心 | `unread 14 / total 23` | `error=true`，列表空；界面「加载失败，点此重试」 | `S288-messages-{healthy,fail}.jpg` |
| 阅读护照 | `passport.words_total 109320 / books_total 5 / level A` | `loadError='名片加载失败'`、`noChild=false`、`passport=null` | `S288-profile-card-{healthy,fail}.jpg` |
| 等级与勋章 | `points` 10 条 | `loadError='成长数据加载失败'`、`points=[]` | `S288-achievement-{healthy,fail}.jpg` |
| 线上测验 | `status='available' / attemptsLeft 2 / 5 题` | `loadError='测验加载失败'`、`status=''`、`questions=[]` | `S288-quiz-{healthy,fail}.jpg` |
| 测验结果 | 服务端兜底路径出题面（`loadError=''`） | `loadError='成绩单加载失败'`、`result=null` | `S288-quiz-result-{healthy,fail}.jpg` |
| 晒成就 | `loadError=''`（正常出卡） | `loadError='成就卡加载失败'` | `S288-share-{healthy,fail}.jpg` |

六页故障态文案都带"**不是「没有…」，请重试**"口径 + 重试按钮 ⇒ B2 的整改目标
（"故障不能再被画成没有数据"）在端上成立。判据落点：`scripts/check_miniapp_wiring.py` 的 **W4**
（catch 必须落到可见失败态）+ `tests/unit/test_review10_b2_miniapp_failure_states_20261024.py`（node 桩）。

### 恢复腿

- `S288-profile-card-recovered.jpg`：baseURL 改回 `:8002` 后重新进入页面 → `loadError=''`，
  护照真实数据回来（109320 词 / 5 本 / 1133 分）。
- `S288-profile-card-fail-with-stale-card.jpg`：**观察项**——`loadError` 与上一次的 `passport`
  同时为真时，界面是"错误条 + 旧卡片"并存（模板里两者不是互斥分支）。本轮判定**不是 B2 缺陷**
  （失败没被画成"没有数据"，旧数据上方明确标了加载失败）；若甲方要求失败时连旧内容一起盖住，属另一条视觉口径。

### 「重试按钮」这条腿的诚实交代

- **自动化点不到组件内部元素**：`error-view` 组件里的 `button`（重试）在
  `automation_page_action --action querySelectorAll --selector button` 里恒为 **0 条**
  （同一工具在页面模板内的 `view` 上工作正常，见 S289 的点击实测），
  `automation_element_action --selector button --action offset` 返回的坐标也不可用（`top=1259`）。
  ⇒ 本轮**没有**采到"手点重试按钮"的截图证据，不冒充有。
- 替代证据两条：① 直接调用页面处理器 `--action callMethod --method onRetryLoad`
  （即按钮 `bind:onRetry` 绑定的**同一个函数**）→ `loadError` 由「名片加载失败」清空并重新取到数据；
  ② 全量静态核对：24 处 `<error-view … bind:onRetry=…>` 与组件 `triggerEvent('onRetry')`
  事件名两端对齐、处理方法全部存在（核对脚本见 §三）。
- 建议 **B4** 把这套"组件自定义事件名两端对齐"判据加进 `check_miniapp_wiring`（本轮不加，
  避免与 B4 的检查器批次重复动同一文件）。

## 二、S289 · 听书页「15s ↻」（book 31《Green Eggs and Ham》，演示音频 5.33s）

| 步骤 | `audio.currentTime` |
|---|---|
| 起点（暂停） | `0` |
| 点右侧「15s ↻」 | `5.328979166666667`（= `duration`）⇒ `min(duration, 0+15)` **夹到末尾** |
| 点左侧「↺ 15s」 | `0`（`max(0, 5.33-15)`） |

- 播放中复测：`ct=2.73` 时点快进 → 夹到末尾并触发 `ended`（结束后位置归零是既有行为）。
- **"+15" 的算术本身**在 5.33s 演示音频上不可分辨（任何 ≥5.33 的步进都落在末尾）⇒ 该部分由
  node 桩判据覆盖：`tests/unit/test_review10_b2_miniapp_failure_states_20261024.py`
  （桩 `duration=200` → 断言 `seek(45)`、末尾 `seek(100)`）。
- 截图 `S289-reader-skip-controls.jpg`（修复后：左右两个 15s 控件**同亮度**）。

## 三、本轮端上发现并修掉的两个真缺陷（都属"编译/视觉"族，不是"假空"族）

### D1 · `profile-card.wxss` 注释嵌套 → member-pkg **整分包 8 页白屏**（B2 自己引入）

- 形态：B2 给 `profile-card.wxss` 加说明注释时，正文里嵌了一句完整的"注释里再写注释"。
  WXSS 注释**不可嵌套** ⇒ 内层终结符让外层注释提前结束，残文（含反引号）被当成 CSS 规则。
- 现象：模拟器整页「编译 .wxss 文件错误，错误信息如上，可在控制台查看更详细信息」，
  member-pkg 的 8 个页面（护照/等级/打卡/排行榜/在读报告/报告/购买/会员码）**全部白屏**。
- 定位证据（IDE 日志）：`WeappLog/logs/2026-10-25-11-04-27-501.log`
  → `./pages/member-pkg/profile-card/profile-card.wxss(288:57): unexpected ... at pos 6503`；
  紧跟 `[devtools] webview on error Error: 编译 .wxss 文件错误…`。
- 为什么原判据看不见（**两条都判绿**）：R8 查属性名损坏、R9 查花括号配平与"注释未闭合"——
  这里注释"看起来闭合了"、残文里连花括号都没有（`.m { … }` 自己配平）。
- 修法：① 改写注释（不再出现注释开头/终结符）；② 新增判据 **R17 注释完整性**（注释体里再出现
  注释开头符 / 注释或字符串外出现反引号），带 S1 自证样本（坏样本两种形状各一条 + 好样本不误报）。
- 变异实测：把原注释形态塞回去 → `check_miniapp_style` **FAIL 2 项**
  （`注释体里又出现注释开头符`、`注释/字符串外出现反引号`）；还原 → **PASS**。

### D2 · 听书页右侧「15s ↻」被渲染成"禁用样"（灰 30%）

- 形态：控件上的标记类 `x-aa01b4` 来自 2026-09-13"内联样式机械迁移"（原 `style="opacity:0.3"`），
  当年它是死控件所以看着像禁用；B2 接活 `bindtap` 后**没人摘这条灰化规则** ⇒ 功能可用但看着不可点。
- 全仓排查：31 个机械迁移类里**仅此一处**带 `opacity`（其余是颜色/字号）。
- 修法：wxml 去掉标记类 + wxss 删掉该规则（并在原地留一行说明，避免下轮被"恢复"）。
- 证据：`S289-reader-skip-controls.jpg` 为**修复后**状态（左右两个 15s 控件同亮度）；
  修复前"右侧偏灰"是端上目视观察到的事实，本轮未单独留图（当轮截图已被修复后的同一路径覆盖，
  不补造）。缺陷本身的物证是删除的那条规则 + wxml 标记类，见本批 diff。
