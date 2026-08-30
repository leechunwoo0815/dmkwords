# DmkWords 小程序 v5 改造报告（2026-08-30 下午批次）

> 执行：GLM（接手上一任美化模型的烂尾）。基准：`d97b0f5`（tag acceptance-baseline-wm3-wm11）+
> 上一任 46+2 项未提交改动（部分保留、伪需求推翻）。
> 截图证据：`docs/miniapp-redesign-20260830/screenshots/`（每页改造前 baseline-* / 改造后 v5-*）。

---

## 一、上一任遗留的三座大山，全部搬掉

| 遗留问题 | 根因 | 本轮处理 |
|---|---|---|
| 模拟器连不上后端（P0，换了 3 个 IP 都不通） | IDE 微信登录态过期 + automator 未就绪，**不是网络问题**（后端 127.0.0.1:8002 一直活着） | 扫码重登后 `wx.request /health` 直通 200；wx.request/登录/封面全部验证 |
| 封面图 401 | `<image>` 无法带 Authorization 头 | 上一任的 query-token 改动方向正确，本轮 curl 直验 `covers/{id}?token=` 返回 `200 image/jpeg` ✓ 保留 |
| "书架=本地缓存"伪需求 | v4 把书架定义为 `wx.storage` 本地数据（上限 30 本） | **推翻**：书架回归后端真实数据（在借/收藏/预约三个 tab），本地 `shelf.js` 依赖从详情页/书架页移除（文件暂留，收尾时删） |

## 二、本轮完成的改造（每页截图自查）

### 数据底座（先让页面有真数据可看）
- `scripts/seed_demo_library.py`（新）：30 本经典童书 + Pillow 生成绘本风封面（12 配色几何贴纸风）
  + 每本 5 道测验题 + 音频轮换补齐；幂等可重跑。现库：**35 本上架书全带封面和音频、175 道题**。
- `scripts/seed_wm11_demo.py` 扩容：演示孩成长数据（打卡 3 天/词数 550/积分 10/续听进度 40%）
  + 收藏 2 本 + 预约 1 条（锁 reserved 副本，72h 任务可自动释放）；演示书缺题自动补；**建书改复活语义**（软删行挡 ISBN 唯一索引，C50 同族）。
- 清理：12 本 "hello word" 垃圾测试书 + 上一任造的重复 HP（78000 词版）软删。

### 页面改造
| 页面 | 状态 | 关键变化 |
|---|---|---|
| **首页** | ✅ 重做 | 死推荐卡清掉 → 任务台：问候卡（打卡 pill）+ 提醒条（未读/在借/预约，有才显示）+ **续听卡**（真实进度条+到期日）+ 今日数据（词数/积分/打卡）+ **真封面横滑推荐** + 2×4 宫格；底部安全区避让 |
| **图书馆** | ✅ 重做 | **筛选体系落地**：年级（6 档）/主题（8 类）/AR 区间/排序（5 种）/🎧音频开关 + 已选条件 chips 可单删+清空 + 计数 + 真分页（onReachBottom）。后端 `list_books` 同步扩参（CAST DECIMAL 处理字符串 AR 列） |
| **书架** | ✅ 重做 | 三 tab 真实资产：在借中（到期倒计时/逾期红标/续听）、收藏（想读）、预约中（状态中文/取消预约带确认）；每 tab 配引导空态 |
| **详情页** | ✅ 重做 | 去掉"加入书架"伪按钮（借书是馆员操作）；**真实借阅状态卡**（在借中/已预约/可预约三态）+ 简介 + 年级/主题 chips + 时长人性化（X 分 X 秒）+ 按钮状态化（开始听/再听一遍/测验未解锁） |
| **听书页** | ✅ 验证+保留 | **查词闭环打通**（WM11 遗留正式关闭）：ham → 音标 hæm + 中英释义完整显示（340 万词库）；播放器/倍速/进度沿用上一任结构 |
| **消息中心** | ✅ 重做 | 卡片化列表：分类彩色小标签（修掉"资金押金到账"粘连）+ 未读橙点+粗边框 + 友好时间（今天/昨天/MM-DD）+ 未读操作条 |
| **测验页** | ✅ 补态 | 补 `passed` 态（之前掉进 else 显示 1/0 空白）+ locked 态"去听书"引导按钮 + 空题防御 |
| **tabBar** | ✅ 重绘 | Pillow 生成 8 张绘本风图标（房子/书/书架/笑脸 × 普通/选中），替换默认方块图 |

### 后端配套（小程序体验硬依赖，随批交付）
- `reading/miniapp_router.list_books`：grade/topic/ar_min/ar_max/has_audio/sort 三路筛选 + 五种排序
  （curl 实测：AR0-2→5 本、has_audio→35 本、words_asc 升序 ✓）
- `reading/service.ReservationService.list_mine`：补 cover_url/has_audio（预约 tab 封面）

## 二B、验收日功能修复三连（2026-08-30 晚，用户实测反馈）

| # | 用户反馈 | 根因 | 修复 | 复验 |
|---|---|---|---|---|
| 1 | 打卡日历 30 号数字看不见；"连续打卡 3"的 3 和背景同色 | `.cal-day.today .cal-day-text`（橙字）源码顺序在 `.checked`（白字）之后，同优先级后者覆盖；streak 数字是行内橙字 | today+checked 组合态强制白字+描边；streak 数字白色加粗 40rpx | 截图 v5-checkin-fixed.jpg：30 号白字清晰、streak 3 白色粗体 |
| 2 | 音频只有 4-5 秒却显示 1 分多，**永远无法读完** | `_mp3_duration` 只解析 MPEG1（帧头 fffb）；lame 低采样率输出是 **MPEG2 Layer III（fff3）**→ 解析返回 0 → 兜底 60/90s → 完播判定 coverage/total 不可达 | 解析器补 MPEG2/2.5 分支（低速率表/576 samples/采样率表）；重算 30 本书时长入库；seed 进度改按真实时长动态算 | 6 文件解析 4-6s（真实）；MPEG1 回归通过；books 全表时长正确 |
| 3 | 测验 5 题答完点提交**没反应** | `.confirm-overlay` 默认 `display:none`，只有 `.show` 类才显示；而 wxml 用 `wx:if` 条件渲染——节点出现时永远没有 `.show` 类，**确认弹层根本不可见** | 去掉 display:none（显隐交给 wx:if） | 弹层居中可见（v5-quiz-confirm.jpg）→ 确认提交 → 结果页"测验通过 100 分"（v5-quiz-result.jpg） |

**排查中的重要发现（非 bug）**：库里有**两个家长**——用户自注册的 13800000914（id=1，"九九孩"）和
演示家长 13800008888（id=2，"演示孩"）。所有演示数据（打卡/词数/进度）挂在 child_id=2；
下午自动化测试 URL 里硬编码 child_id=1 是**测试脚本自己的错**（页面无 bug）。后续自动化一律
从登录态取 currentChild.id，禁止硬编码。

**完播链路验证边界（2026-08-30 深夜已推翻，onEnded 模拟器端到端通过）**：
原结论"模拟器 InnerAudioContext 播放不推进时间、无法端到端"不成立——差异在触发路径：
用 UI 点击依赖 onCanplay 起播（部分基础库不触发）；改 automation_evaluate 直接调
页面 onTogglePlay 后模拟器正常推进时间。实测（book 2，5s 音频，半听 2/5 续播）：
播放至 onEnded → 查库 `reading_progress` coverage_seconds=5、total_seconds=5、
**finished=1**、intervals=[0,5]、last_position=5 ✓（截图 v6-onended-before/after.jpg）。
onEnded 修复（duration 兜底上报）验证通过。
**遗留观察（E-20260830-11，已修复 2026-08-30 晚）**：播完 UI 仍显示"已覆盖 60%"而非 100%
——根因是竞态：onEnded 里 loadProgress 与最终上报并行，GET 先于 POST 落库拿到旧值。
已修：`reportProgress(...).then(() => loadProgress())` 上报落库后再刷新。实测 UI 显示
"已覆盖 100% + 已读完"、滑块到头（v6-onended-ui100.jpg）。真机最终确认仍按 §遗留清单保留。

## 三、本轮新踩的坑（防复发）

1. **WXSS 中文类名会卡死页面导航**：`.cat-资金` 导致 navigateTo 消息页 automator 直接 timeout
   （页面渲染错误吞掉导航响应）。类名一律英文，中文文案进 data。
2. **WXML 表达式不支持 `.split()` 等方法调用**（C47 同族再+1）：picker 显示文本必须 js 预计算。
3. **模拟器编译缓存**：wxml 改动后旧页面热重载不彻底，出现"改了没生效"假象——先 `simulator_refresh`
   再验证。
4. **软删行挡唯一索引**在 seed 场景重现（C50 同族）：seed 建书查重必须含软删行（复活语义）。

## 四、验证

- **全量门禁 PASS（退出码 0）**：`gate-runs/2026-08-30/gate-miniapp-v5.log`——
  **223 passed / 覆盖率 79.63%** / behave 12 scenarios / 架构关 / alembic check / tsc 全过。
- 过程波折（如实记录）：首轮 gate 卡死 23 分钟——根因是 `seed_dictionary.seed()` 全表拉
  `dictionary_words`（340 万行）进内存 set，而 conftest **每个测试模块**都调它；词库还是
  309 词时不暴露，全量导入后必卡死。已修：只查本次要补的词（IN 查询）。修后 pytest 7.5 分钟跑完。
- 模拟器端到端（重登后复验）：登录 → 首页任务台（词数 650/积分 10/未读 10/在借 1/续听 40%）
  → 图书馆筛选 → 书架三 tab → 详情状态卡 → 听书查词闭环 → 测验 locked 态，截图在 screenshots/。
- 数据一键恢复：`python -m scripts.seed_wm11_demo && python -m scripts.seed_demo_library`
  （pytest/gate 清库后执行，模拟器需退出重登）。

## 五、遗留与建议（下一批）

0. ~~打卡日历不显示历史标记~~ → **已修**（见 §二B #1：纯样式优先级问题，当时抽查用的
   child_id=1 是错误参数造成的误报）。
1. **次级页深修**：打卡/榜单/护照/活动/押金/退款/我的 7 页上一任样式可用但未逐页精修（本轮抽查
   我的页：会员信息卡偏薄，缺到期日/押金状态）。
2. **miniapp 我的书架 shelf.js 清理**：伪需求文件已无引用方，收尾时删除。
3. **后端完善批**（等前端定型）：books 可借副本数字段（预约前可见"是否可借"）、
   admin-web schema.d.ts 同步、2000 本性能压测（分页深翻页）。
4. **演示书封面美化**：Pillow 几何封面为占位性质，正式录入时换真实封面图。
5. **quiz 判定口径**：词数流水让演示孩部分书直接 passed——真实用户路径是听完→解锁→通过，演示
   数据特意造了两种态（locked/passed）都有截图可验。

## 六、截图索引

| 文件 | 内容 |
|---|---|
| baseline-index.jpg / v5-index-top.jpg / v5-index-bottom.jpg | 首页改造前/首屏/底屏 |
| v5-books-filter.jpg | 图书馆 AR 筛选态（真封面 2 列卡） |
| v5-shelf-borrows/favorites/reservations.jpg | 书架三 tab |
| v5-book-detail.jpg | 详情页状态化按钮+简介 |
| v5-reader-top.jpg / v5-reader-dict.jpg | 听书页 / 查词结果闭环 |
| v5-messages.jpg | 消息中心卡片化 |
| v5-quiz.jpg | 测验 locked 引导态 |
| v5-messages-tabbar.jpg | 新 tabBar 图标 |

---

**健壮性加固（E-20260830-12，2026-08-30 晚）**：merge_intervals 对一维 intervals 脏数据
曾 500（TypeError）。已修：`_clean_intervals()` 清洗非 [a,b]/非数字/start>=end/负起点区间 +
`json.loads` 容错。测试 test_merge_intervals_dirty_data + test_report_progress_polluted_intervals_no_500。
门禁 225/79.63%（gate-onended-defense.log）。
