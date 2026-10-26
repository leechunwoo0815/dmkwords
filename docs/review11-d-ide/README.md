# R11-d1 端上证据（微信开发者工具，2026-10-26）

- 工具状态：`wechatide -c zcode check_wechatide_status` → `ok:true` / `loginExpired:false`（用户 Bokoblin）。
- 工程：`/Users/litianyu/cc-projects/dmkwords/miniapp`（devtools 模拟器，后端 `127.0.0.1:8002`）。
- 修复代码：`M1` book-detail.js（unknown 禁止预约）、`M2` reader.js（心跳成功后推进起点）。

## M1 · `unknown` 状态禁止预约（S346）

- 开页：`simulator_open_page --page pages/reading-pkg/book-detail/book-detail --query "book_id=2&child_id=1"`
- 截图：`M1_book_detail_before.jpg`（详情页正常渲染，底部有「预约借书」）
- 探针（`automation_evaluate`）：`setData({borrowState:'unknown', reserving:false})` → 劫持 `wx.showToast` → `onReserve()`
- 实测结果：`toasts:["借阅状态未知，请先点重试加载"]`、`reserving:false`、`borrowState:"unknown"`
  ⇒ 点预约**没有发起请求**，只给了可见提示。

## M2 · 心跳失败不推进会话起点（S347）

- 开页：`simulator_open_page --page pages/reading-pkg/reader/reader --query "book_id=2&child_id=1"`
- 截图：`M2_reader_page.jpg`（听书页正常渲染，已覆盖 100%）
- 探针（`automation_evaluate`）：伪造 `_audio={currentTime:20}`、`_sessionStart=10`，把
  `getApp().globalData.baseURL` 指到不可达的 `http://127.0.0.1:9`，调用 `_reportNow()` 等其失败。
- 实测结果：`startBefore:10`、`startAfterFailedBeat:10` ⇒ 失败的心跳**没有**推进起点
  （修复前 await 前就推进 → 变成 20，这一段永久丢失）。
- 诚实边界：**成功腿**未在端上跑（需要真实播放 + 有效登录态与会员资格）；成功时推进起点由代码
  顺序保证，并由 `check_miniapp_wiring` + tsc 兜底。失败腿是本条缺陷的原始复现路径，已实测。

## R11-d2b 追加（2026-10-26）

- **M6 报告图**：开页 `pages/member-pkg/report/report?child_id=1`，截图 `M6_report_page.jpg`
  ——数据与报告图正常渲染（`imgError=false` 分支未误伤）；图片加载失败时走新增的
  `<error-view>` 分支（可重试），不再落入"生成中"。
- **M10 选项字母**：开页 `pages/reading-pkg/quiz/quiz?book_id=3&child_id=1`，探针实测
  `page.data.letters = ["A","B","C","D","E","F"]`（后端 options 允许 6 项；原先 wxml 硬编码
  A–D，第 5/6 项空白）。该书未解锁（`status:"locked"`），题面未展示，故以 data 探针为准。
- 其余 M3/M4/M5/M7/M8/M9/M11/M12 为代码路径修复，由 `node --check` + 小程序四检查器兜底；
  端上复跑步骤见 `docs/04` §七十三。
