# DmkWords 小程序视觉改造交接报告（v3 + v4）

> 【已归档 20260831】历史过程文档，现行方案见 docs/miniapp-redesign-20260830.md

> 生成时间：2026-08-30 13:41
> 交接对象：后续继续美化小程序的大模型
> 当前状态：工作区未 commit/push，全部改动在本地
> 基准提交：d97b0f5（tag acceptance-baseline-wm3-wm11）

---

## 一、工作总览

本次改造分两阶段完成：

| 阶段 | 目标 | 完成度 |
|------|------|--------|
| **v3 绘本风换肤** | 只改视觉、不动业务，全局 token 换肤 + 23 个页面精修 | ✅ 已完成 |
| **v4 信息架构与交互重构** | 按用户真机测试反馈，重构首页/图书馆/书架/详情/听书/我的等核心页面 | ⚠️ 前端代码已改，但模拟器网络连通与封面图显示未最终验证 |

**当前最大风险**：微信开发者工具模拟器无法稳定访问本地后端（`127.0.0.1:8002` / `10.0.2.2:8002` / 局域网 IP 均尝试过），导致 v4 部分页面（封面图、书架/收藏真实数据）截图一直显示网络错误/空白。后续模型需优先解决“模拟器 ↔ 后端”连通问题，再验证视觉。

---

## 二、设计系统（app.wxss）

**策略：改值不改名**。`miniapp/app.wxss` 保留了 v1/v2 全部 43 个 CSS 变量名，存量页面无需改动即可继承新风格；同时新增了绘本风工具类。

### 2.1 核心 Token

| Token | 色值 | 用途 |
|-------|------|------|
| `--bg` | `#FDF8F0` | 页面背景（水彩画布米白） |
| `--surface` | `#FFFDF7` | 卡片/纸面背景 |
| `--fg` | `#3B2F2F` | 主文字（深棕墨） |
| `--muted` | `#6B5B5B` | 次要文字 |
| `--border` | `#D4C5B5` | 细线/分隔线 |
| `--accent` | `#FF6B35` | 主按钮、选中态、重点 |
| `--accent-soft` | `rgba(255,107,53,0.10)` | 橙色淡化背景 |
| `--secondary` | `#4ADE80` | 嫩芽绿（成功/正向） |
| `--success` | `#16A34A` | 深绿（白字按钮对比度） |
| `--warning` | `#B45309` | 琥珀文字 |
| `--error` | `#EF4444` | 番茄红 |
| `--gold` | `#F59E0B` | 成就/等级/星标 |
| `--sun` | `#FCD34D` | 太阳黄（tabBar 选中衬底） |
| `--sky` | `#60A5FA` | 天空蓝 |
| `--sakura` | `#F472B6` | 樱花粉 |
| `--lavender` | `#A78BFA` | 薰衣草紫 |

### 2.2 阴影与圆角

```css
--shadow-hard: 6rpx 6rpx 0 rgba(59, 47, 47, 0.90);
--shadow-hard-sm: 4rpx 4rpx 0 rgba(59, 47, 47, 0.85);
--shadow-soft: 0 8rpx 24rpx rgba(59, 47, 47, 0.08);
--radius: 32rpx;
--radius-sm: 24rpx;
--radius-lg: 40rpx;
--radius-xl: 48rpx;
```

### 2.3 字体

```css
--font-display: 'ZCOOL KuaiLe', 'Yuanti SC', 'YouYuan', 'PingFang SC', sans-serif;
--font-body: 'Nunito', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Helvetica Neue', sans-serif;
```

### 2.4 新增绘本工具类

| 类名 | 效果 |
|------|------|
| `.paint-sticker` | 圆形贴纸底（粗黑边 + 硬阴影） |
| `.paint-hand` | 手绘不一致圆角 `32rpx 36rpx 28rpx 40rpx` |
| `.watercolor` | 水彩噪点纹理叠加（`::after` + SVG noise） |

---

## 三、页面清单与改动摘要

### 3.1 主包 5 页

| 页面 | 路径 | v4 主要改动 |
|------|------|-------------|
| 首页 | `pages/index/index` | 外层包 `scroll-view`；新增 banner 卡、孩子切换 chips、推荐卡、快捷入口（大图标 + 小图标分层）、今日数据统计 |
| 图书馆 | `pages/books/books` | 改为 2 列封面大图卡片；使用 `utils/media.js` 补全封面 URL |
| 书架 | `pages/shelf/shelf` | 改为「书架/收藏」双 tab；书架走本地 `my_shelf_${childId}`；收藏走 API；列表项增加操作按钮 |
| 我的 | `pages/member/member` | 紧凑布局、4 列功能宫格、减少留白 |
| 登录 | `pages/login/login` | 绘本风样式覆盖 |

### 3.2 reading 分包 5 页

| 页面 | 路径 | v4 主要改动 |
|------|------|-------------|
| 听书 | `pages/reading-pkg/reader/reader` | `sliderMax` 优先用 `book.audio_duration`；查词加 loading/空结果/错误提示；结果卡片样式 |
| 书籍详情 | `pages/reading-pkg/book-detail/book-detail` | 新增书架/收藏状态管理；底部「开始听」主按钮 + 预约/测验/收藏图标化副按钮 |
| 测验 | `pages/reading-pkg/quiz/quiz` | 绘本风样式覆盖 |
| 测验结果 | `pages/reading-pkg/quiz-result/quiz-result` | 绘本风样式覆盖 |
| 生词本 | `pages/reading-pkg/vocabulary/vocabulary` | 绘本风样式覆盖 |

### 3.3 member-pkg 分包 6 页

| 页面 | 路径 | v4 主要改动 |
|------|------|-------------|
| 打卡 | `pages/member-pkg/checkin/checkin` | 数字放大、白字加阴影、文案加粗、橙底对比度修复 |
| 等级勋章 | `pages/member-pkg/achievement/achievement` | 绘本风样式覆盖 |
| 排行榜 | `pages/member-pkg/leaderboard/leaderboard` | 绘本风样式覆盖 |
| 阅读护照 | `pages/member-pkg/profile-card/profile-card` | 绘本风样式覆盖 |
| 观察期评估 | `pages/member-pkg/observation-report/observation-report` | 绘本风样式覆盖 |
| 阅读报告 | `pages/member-pkg/report/report` | 绘本风样式覆盖 |

### 3.4 order-pkg 分包 5 页

| 页面 | 路径 | v4 主要改动 |
|------|------|-------------|
| 我的预约 | `pages/order-pkg/reservation/reservation` | iOS `new Date` 兼容修复；绘本风样式覆盖 |
| 申请退款 | `pages/order-pkg/refund-apply/refund-apply` | 绘本风样式覆盖 |
| 权益转让 | `pages/order-pkg/benefit-transfer/benefit-transfer` | 绘本风样式覆盖 |
| 押金 | `pages/order-pkg/deposit/deposit` | 绘本风样式覆盖 |
| 消息中心 | `pages/order-pkg/messages/messages` | 绘本风样式覆盖 |

### 3.5 activity-pkg 分包 2 页

| 页面 | 路径 | v4 主要改动 |
|------|------|-------------|
| 线下活动 | `pages/activity-pkg/activity-list/activity-list` | 绘本风样式覆盖 |
| 活动详情 | `pages/activity-pkg/activity-detail/activity-detail` | 绘本风样式覆盖 |

### 3.6 全局组件

| 组件 | 路径 | 改动 |
|------|------|------|
| 自定义 tabBar | `custom-tab-bar/index.*` | 太阳黄贴纸风格、选中橙色、粗黑顶边 |
| 空态组件 | `components/empty-state/empty-state.wxss` | 绘本风覆盖 |
| 错误视图 | `components/error-view/error-view.wxss` | 绘本风覆盖 |
| 加载骨架屏 | `components/loading-skeleton/loading-skeleton.wxss` | 绘本风覆盖 |

---

## 四、新增/修改的 JS 工具与业务逻辑

### 4.1 新增 `miniapp/utils/media.js`

用途：后端返回相对路径 `/api/miniapp/covers/7`，小程序 `<image>` / `<audio>` 需要绝对 URL。

```js
// 核心函数
fullUrl(url, withToken = false)     // 补全 baseURL，可选追加 ?token=xxx
formatBook(book)                    // 处理单本书的 cover_url/audio_url
formatBooks(books)                  // 批量处理
```

**注意**：封面图 URL 默认会追加 `?token=xxx`，因为 `<image>` 无法携带 `Authorization` 头。后端对应接口已改为支持 query token。

### 4.2 新增 `miniapp/utils/shelf.js`

用途：客户端本地存储「我的书架」=「我正在读的书」。

```js
const MAX_SHELF = 30
key(childId) => `my_shelf_${childId}`
loadShelf(childId)
addToShelf(childId, book)    // 上限 30，重复检测
removeFromShelf(childId, bookId)
isInShelf(childId, bookId)
getShelfCount(childId)
```

### 4.3 书架/收藏业务语义（v4 核心变更）

| 概念 | 定义 | 数据存储 |
|------|------|----------|
| **书架** | 我正在读的书 | 本地 `wx.getStorageSync('my_shelf_${childId}')`，上限 30 本 |
| **收藏夹** | 想读/稍后读 | 后端 API `listFavorites` / `addFavorite` / `removeFavorite` |

**书架页交互**：
- 书架 tab：每本书显示「移除书架」+「预约借书」
- 收藏 tab：每本书显示「取消收藏」+「加入书架」
- 整行可点击进入详情

**详情页交互**：
- 未在书架：显示「加入书架」（大按钮区域或底部栏）
- 已在书架：显示「移除书架」
- 收藏：图标切换
- 预约借书：必须先加入书架

### 4.4 iOS `new Date` 兼容修复

修复文件：
- `miniapp/pages/member/member.js`
- `miniapp/pages/order-pkg/reservation/reservation.js`

将 `new Date("yyyy-MM-dd HH:mm:ss")` 替换为 iOS 可解析格式（斜杠分隔）。

### 4.5 听书页 `reader.js` 关键修改

- `duration` / `sliderMax` 优先从 `book.audio_duration` 初始化
- `onCanplay` 中再次校准时长
- 查词增加 `dictLoading` / `dictError` / `dictResult` 状态，结果卡片可见性优化

### 4.6 `miniapp/app.js` 改动

- `DEV_BASE_URL` 默认仍为 `http://127.0.0.1:8002`
- 新增注释说明：若模拟器/真机无法访问，需改为宿主机局域网 IP，并确保后端 `--host 0.0.0.0`

### 4.7 `project.private.config.json` 改动

- `"urlCheck": true` → `"urlCheck": false`（开发期允许本地 http 域名）

---

## 五、后端改动

**唯一改动文件**：`backend/domain/reading/miniapp_router.py`
**函数**：`book_cover`（`/api/miniapp/covers/{book_id}`）

### 5.1 改动原因

小程序 `<image>` 组件请求图片时无法携带 `Authorization` 头，原接口强制 Header 鉴权，导致封面图 401。

### 5.2 改动内容

- 入参增加 `token: str = ""`（query token）
- 入参增加 `authorization: str | None = Header(None, alias="Authorization")`（兼容旧 Header）
- 鉴权逻辑：优先 query token，其次 Authorization 头
- 查询书籍增加 `Book.is_deleted == 0` 条件

### 5.3 专家排查风险点

1. `Book` 模型是否有 `is_deleted` 字段？
2. `_parent_from_token` 校验 JWT `type == "parent"`，空 token 会抛 401
3. `Header(None, alias="Authorization")` 与 FastAPI 版本兼容性
4. 后端启动和 `/health` 正常，不代表封面接口完全正常（本 agent 因代理/进程环境问题未完整验证）

详细后端交接报告见：`docs/backend-change-report-20260830.md`

---

## 六、当前已知问题与待办（后续模型优先处理）

### P0 — 阻塞验收

1. **模拟器无法访问后端**
   - 现象：截图中封面图、书架/收藏列表显示网络错误/空白
   - 已尝试：后端 `--host 0.0.0.0`、`DEV_BASE_URL` 切到 `127.0.0.1` / `10.0.2.2` / `192.168.18.162`
   - 建议后续模型：
     - 确认后端确实在运行（`curl http://127.0.0.1:8002/health`）
     - 确认 WeChat DevTools 设置里「不校验合法域名」已开启（`project.private.config.json` 已设 `urlCheck: false`）
     - 使用宿主机真实局域网 IP（如 `http://192.168.x.x:8002`）
     - 检查本机防火墙/安全软件是否拦截 8002 端口
     - 使用 `wx.request` 先测试一个简单接口（如 `/health`），确认基础连通

2. **封面图接口未完整验证**
   - 后端 `book_cover` 虽然能 import / 启动，但未在模拟器里确认封面能正常显示
   - 建议后续模型：
     - 用浏览器/Postman 访问 `http://<host>:8002/api/miniapp/covers/1?token=<valid_token>`，确认返回图片
     - 在模拟器 Network 面板确认 `<image>` 请求 URL 和响应状态

### P1 — 视觉/交互优化

3. **首页信息架构**
   - v4 已做 banner + 推荐 + 快捷入口 + 统计，但用户可能认为还不够好
   - 建议：根据真机截图继续优化信息层级、图标大小、间距

4. **图书馆 2 列大图**
   - 封面图若加载成功，需确认 2 列布局在真机上是否美观
   - 建议：检查封面占位图、标题行数截断、卡片高度一致性

5. **书架与收藏交互**
   - 当前书架数据纯本地，退出登录/换设备会丢失
   - 建议：与产品确认是否需要同步到后端；若需要，需新增后端接口

6. **详情页底部按钮**
   - v4 已改为「开始听」主按钮 + 图标副按钮
   - 建议：根据用户反馈继续微调按钮尺寸、间距、图标清晰度

7. **听书页进度条**
   - `sliderMax` 逻辑已改，但需要在真实音频上验证是否还会“没播完就满”
   - 建议：用一本有真实音频的书完整测试

8. **查词功能**
   - 已加 loading/错误提示，但未验证查词 API 是否正常返回
   - 建议：输入一个真实单词测试完整流程

### P2 — 代码质量

9. **硬编码颜色残留**
   - v3 已清零大部分，但 v4 新增代码中可能引入新的硬编码色
   - 建议：全局搜索 `#` 色值，统一替换为 token

10. **大括号/语法检查**
    - v3 已全量检查，v4 改动后建议再跑一遍检查脚本

11. **图标资源**
    - tabBar 图标路径仍为 `icons/home.png` 等，未替换为绘本风图标
    - 建议：替换为风格统一的绘本图标（或继续用 emoji + 贴纸底）

---

## 七、如何继续工作

### 7.1 环境准备

1. 启动后端：
   ```bash
   cd /Users/litianyu/cc-projects/dmkwords
   .venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8002
   ```
   或使用：
   ```bash
   ./scripts/dev.sh start
   ```
   （`scripts/dev.sh` 已加入 `--noproxy '* '` 绕过系统代理）

2. 打开微信开发者工具，导入 `miniapp` 目录。

3. 确保 `project.private.config.json` 中 `urlCheck: false`。

4. 若模拟器无法访问 `127.0.0.1:8002`：
   - 查看宿主机局域网 IP：`ipconfig`（Windows）或 `ifconfig`（macOS）
   - 修改 `miniapp/app.js` 中的 `DEV_BASE_URL` 为 `http://<局域网IP>:8002`
   - 重新编译

### 7.2 推荐工作顺序

1. **先通网络**：确保模拟器能请求到 `/health` 和 `/api/miniapp/books`
2. **再验封面**：确认 `/api/miniapp/covers/{id}?token=xxx` 能返回图片
3. **逐页截图**：每改一页，用模拟器截图验证
4. **跑门禁**：`scripts/gate.sh`（ruff / behave / 单元测试 / tsc）
5. **更新报告**：修改 `docs/miniapp-paint-handover-20260830.md`，记录新完成项和新问题

### 7.3 关键文件速查

| 目的 | 文件 |
|------|------|
| 设计 token | `miniapp/app.wxss` |
| 全局导航/tabBar | `miniapp/app.json`、`custom-tab-bar/index.*` |
| 网络基础 URL | `miniapp/app.js` |
| 封面/音频 URL 补全 | `miniapp/utils/media.js` |
| 书架本地存储 | `miniapp/utils/shelf.js` |
| 后端封面接口 | `backend/domain/reading/miniapp_router.py` |
| 开发启动脚本 | `scripts/dev.sh` |

---

## 八、证据与截图位置

| 目录 | 内容 |
|------|------|
| `docs/miniapp-paint-v3/` | v3 完整报告 `full-report.html` + 23 张模拟器截图 |
| `docs/miniapp-paint-v3/screenshots/` | v3 各页面截图 |
| `docs/miniapp-paint-v4/screenshots/` | v4 关键页截图（index/books/shelf/member/book-detail/reader/checkin），但部分因网络问题显示异常 |
| `docs/backend-change-report-20260830.md` | 后端改动交接报告 |
| `docs/miniapp-paint-handover-20260830.md` | 本文件 |

---

## 九、Git 状态

当前工作区有 46 个已修改文件 + 2 个新增文件（`miniapp/utils/media.js`、`miniapp/utils/shelf.js`）。

```bash
git status --short
git diff --stat
```

**未 commit/push**。后续模型可直接在此基础上继续修改，也可按需回滚部分文件。

---

## 十、回滚建议

若需要放弃本次全部前端改动：

```bash
# 只回滚前端改动（保留后端改动报告）
git checkout -- miniapp/ scripts/dev.sh project.private.config.json

# 删除新增工具文件
rm miniapp/utils/media.js miniapp/utils/shelf.js
```

若需要同时回滚后端改动：

```bash
git checkout -- backend/domain/reading/miniapp_router.py
```

---

## 十一、给后续模型的特别提醒

1. **不要轻信模拟器截图**：当前环境存在代理/网络映射问题，截图中的“网络错误”不一定代表代码错误。先用 `wx.request` 测通基础接口。
2. **先验证后美化**：在封面图能正常加载之前，很多视觉调整（图书馆 2 列、书架封面）无法准确评估。
3. **保持 token 命名兼容**：继续使用 `app.wxss` 中已定义的变量名，不要重命名，避免 43 个页面全部失效。
4. **业务逻辑谨慎修改**：书架/收藏语义是 v4 新定义，若用户再次调整需求，需同步修改 `shelf.js` + `shelf.js` + `book-detail.js`。
5. **每轮迭代后全量审查**：用户要求 WM1-WM10 模块全量审查，输出 P0/P1/P2 分级报告，审查期间禁止修改文件。
