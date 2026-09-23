# DmkWords 绘本视觉系统规范 V1.2

> 适用范围：admin-web（管理后台）+ miniapp（微信小程序）  
> 核心目标：让界面"活"起来——像翻开一本少儿绘本，不是走进一间档案室  
> 原则：形态 > 颜色 > 字体。先改组件骨架，再填色，最后加插画。

---

## 一、插画风格定位

### 1.1 选择：扁平水彩风（Flat Watercolor）

不是卡通写实（太复杂），不是纯扁平（太冷），不是 3D（性能贵）。**扁平水彩 = 简洁形状 + 水彩纹理叠加 + 手绘边框感**。

| 风格 | 优点 | 缺点 | 是否采纳 |
|---|---|---|---|
| 卡通写实（Disney） | 吸引孩子 | SVG 复杂、文件大、难统一 | ❌ |
| 纯扁平（Material） | 易实现 | 冷淡、像 SaaS | ❌ |
| 3D 插画 | 视觉冲击 | 加载慢、小程序包体积爆炸 | ❌ |
| **扁平水彩** | 手绘温度 + 性能友好 + 可 SVG 化 | 需设计师出统一规范 | ✅ |
| Kawaii（圆润可爱） | 非常适合少儿 | 偏日式、和"英语分级阅读"品牌冲突 | ⚠️ 局部用 |

**关键特征**：
- 形状：圆润、无锐角、有机曲线（像气球、云朵、水滴）
- 边框：粗黑线（2-3px）+ 轻微手绘抖动（SVG stroke-dasharray 模拟）
- 填色：纯色块 + 水彩噪点纹理（CSS `background-image: url(data:image/svg+xml,...)` 内联）
- 角色：简笔画小熊/小兔子/小星星，大眼睛，无嘴或微笑嘴

### 1.2 角色设定（DmkWords 家族）

每个页面/功能模块有一个"守护角色"，像绘本里的固定角色：

| 页面/模块 | 角色 | 形象 | 用途 |
|---|---|---|---|
| 图书管理 | 📚 书虫贝贝 | 绿色毛毛虫，抱着一本书 | 空状态、上传提示、成功 toast |
| 会员管理 | 🌟 星星点点 | 黄色五角星，有手脚 | 会员等级、积分展示 |
| 借阅操作台 | 🐻 借阅熊 | 棕色小熊，背书包 | 扫码界面、借还成功动画 |
| 成长与测验 | 🏆 奖杯兔 | 白色兔子，举着奖杯 | Quiz 结果、里程碑达成 |
| 退款中心 | 💰 金币猫 | 橘色猫咪，抱着金币 | 空状态、审核通过提示 |
| Dashboard | 🌈 彩虹桥 | 简笔画彩虹 + 云朵 | 数据加载、空状态 |
| 全局错误 | 😢 迷路鸟 | 蓝色小鸟，歪头 | 404、网络错误、操作失败 |

**规范**：角色高度 80-120px（admin-web）、60-80px（miniapp），SVG 内联，不使用外部图片。

### 1.3 空状态文案规范

不要"暂无数据"。要像绘本对话：

| 场景 | 旧文案 | 新文案 |
|---|---|---|
| 图书列表空 | 暂无数据 | "书架还空着呢！让贝贝帮你添加第一本书吧 🐛" |
| 会员列表空 | 暂无数据 | "还没有小读者加入呢，快去邀请吧 ⭐" |
| 借阅记录空 | 暂无数据 | "今天还没有小朋友来借书哦，小熊在等你 🐻" |
| 逾期列表空 | 暂无数据 | "太棒了！所有书都准时回家啦 🎉" |
| 活动列表空 | 暂无数据 | "还没有活动呢，让星星点点帮你策划一个吧 ✨" |
| 退款列表空 | 暂无数据 | "没有待处理的退款，金币猫很安心 😺" |

---

## 二、组件形态规范（形态 > 颜色）

### 2.1 圆角系统

当前 antd 默认圆角太小（6px），像手术刀。绘本风需要**大圆角**，像气球。

| 组件 | 当前 | 建议 | 代码位置 |
|---|---|---|---|
| Button | `borderRadius: 10` | `borderRadius: 16`（大胶囊） | `admin-web/src/theme-paint.ts` |
| Card | `borderRadius: 14` | `borderRadius: 20` | `admin-web/src/theme-paint.ts` |
| Modal | `borderRadius: 14` | `borderRadius: 24` | `admin-web/src/theme-paint.ts` |
| Table 行 | `borderRadius: 0` | 每行独立卡片 `borderRadius: 12` | 自定义 Table 组件 |
| Input | `borderRadius: 10` | `borderRadius: 12` | `admin-web/src/theme-paint.ts` |
| Tag | `borderRadius: 10` | `borderRadius: 999px`（pill） | `admin-web/src/theme-paint.ts` |
| Avatar | `borderRadius: 50%` | 保留，但加 2px 手绘边框 | 自定义 Avatar 组件 |
| 图片/封面 | `borderRadius: 4` | `borderRadius: 12` | 全局 CSS |

### 2.2 边框系统

当前 antd 边框 1px 细线，像Excel。绘本风需要**粗边框 + 手绘感**。

```css
/* 基础手绘边框 */
.paint-border {
  border: 2.5px solid #3B2F2F;
  border-radius: 16px;
  /* 轻微手绘抖动：四边圆角轻微不一致 */
  border-radius: 16px 18px 14px 20px;
}

/* 粗边框按钮 */
.paint-button {
  border: 2.5px solid #3B2F2F;
  border-radius: 999px;
  box-shadow: 3px 3px 0 #3B2F2F;
  transition: all 0.15s ease;
}
.paint-button:active {
  box-shadow: 1px 1px 0 #3B2F2F;
  transform: translate(2px, 2px);
}
```

**规范**：
- 主按钮：`border: 2.5px solid #3B2F2F` + `box-shadow: 3px 3px 0 #3B2F2F` + 按压时 shadow 收缩
- 卡片：`border: 2px solid #3B2F2F` + `box-shadow: 4px 4px 0 rgba(59,47,47,0.08)`
- 输入框：`border: 2px solid #D4C5B5`（聚焦时变 `#3B2F2F`）
- 标签/徽章：粗边框 pill，像贴纸

### 2.3 阴影系统

当前 antd 弥散阴影（`0 4px 12px rgba(0,0,0,0.05)`）太 SaaS。绘本风用**硬边偏移阴影**，像贴纸贴在纸上。

| 场景 | 当前 | 建议 |
|---|---|---|
| 主按钮 | `boxShadow: hardShadow` | `box-shadow: 3px 3px 0 #3B2F2F`（实体按压感） |
| 卡片悬浮 | `boxShadowSecondary` | `box-shadow: 4px 4px 0 #3B2F2F` |
| 下拉菜单 | antd 默认 | `box-shadow: 3px 3px 0 #3B2F2F` + 粗边框 |
| Modal | antd 默认 | `box-shadow: 6px 6px 0 #3B2F2F` + 粗边框 |

### 2.4 按钮形态

当前 antd 按钮是矩形/圆角矩形。绘本风按钮是**大胶囊 + 粗边框 + 硬阴影**。

```tsx
// 当前
<Button type="primary">新书入库</Button>
// → 蓝色矩形，flat

// 建议
<button className="paint-button paint-button--primary">
  📚 新书入库
</button>
// → 大胶囊，粗黑边，彩色底，右下硬阴影，按压收缩
```

**按钮变体**：

| 变体 | 背景 | 边框 | 文字 | 图标 |
|---|---|---|---|---|
| Primary | `#FF6B35` 活力橙 | `#3B2F2F` 2.5px | `#FFFFFF` | 左侧固定 |
| Secondary | `#FFF5EB` 奶油白 | `#3B2F2F` 2.5px | `#3B2F2F` | 可选 |
| Success | `#4ADE80` 嫩绿 | `#3B2F2F` 2.5px | `#FFFFFF` | 左侧固定 |
| Danger | `#EF4444` 番茄红 | `#3B2F2F` 2.5px | `#FFFFFF` | 左侧固定 |
| Ghost | `transparent` | `#3B2F2F` 2px dashed | `#3B2F2F` | 可选 |

### 2.5 表格 Redesign

当前 antd Table = Excel 网页版。绘本风表格是**卡片列表**，不是表格。

```tsx
// 当前：标准 Table
<Table dataSource={books} columns={[...]} />

// 建议：卡片列表（图书管理）
<div className="book-card-list">
  {books.map(book => (
    <div className="book-card" key={book.id}>
      <img className="book-card__cover" src={book.cover} />
      <div className="book-card__info">
        <div className="book-card__title">{book.title}</div>
        <div className="book-card__meta">AR {book.ar_level} · {book.word_count}词</div>
        <div className="book-card__tags">
          <span className="paint-tag paint-tag--green">上架中</span>
          {!book.cover_path && <span className="paint-tag paint-tag--orange">缺封面</span>}
        </div>
      </div>
      <div className="book-card__actions">
        <button className="paint-icon-btn">✎</button>
        <button className="paint-icon-btn">🗑</button>
      </div>
    </div>
  ))}
</div>
```

**Book Card 样式**：
```css
.book-card {
  display: flex;
  gap: 16px;
  padding: 16px;
  background: #FFFDF7;
  border: 2px solid #3B2F2F;
  border-radius: 16px 18px 14px 20px; /* 手绘不一致圆角 */
  box-shadow: 3px 3px 0 rgba(59,47,47,0.1);
}
.book-card__cover {
  width: 56px;
  height: 80px;
  border-radius: 8px;
  border: 2px solid #3B2F2F;
  object-fit: cover;
}
```

**其他页面表格**：会员管理、退款中心等数据密集页保留 Table，但行样式卡片化（圆角 + 粗边框 +  hover 轻微上浮）。

### 2.6 标签/徽章（Tag）

当前 antd Tag = 小色块。绘本风 Tag = **贴纸**。

```css
.paint-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  border: 2px solid #3B2F2F;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}
.paint-tag--green { background: #DCFCE7; color: #166534; }
.paint-tag--orange { background: #FFEDD5; color: #9A3412; }
.paint-tag--blue { background: #DBEAFE; color: #1E40AF; }
.paint-tag--red { background: #FEE2E2; color: #991B1B; }
.paint-tag--purple { background: #F3E8FF; color: #6B21A8; }
```

---

### 2.7 头像与等级头像框（WM15-A/B 引入）

- **系统内置头像 24 枚**：12 动物 × 2 配色，白名单常量 `AVATAR_IDS`（后端校验，非法值拒收）；
  三端同源（小程序本地包 `miniapp/icons/avatars/`、管理端 `public/`、后端 `assets/`）；
  家长在「我的」页自助选择，管理端建档/编辑亦可选。
- **等级头像框四档**（越高越华丽，服务收集欲）：A-B **星芒** / C-E **银环** / F-H **金冠** / I+ **彩虹**；
  资产 `miniapp/icons/frames/*`（由 `scripts/gen_fix34_frames.py` 生成），
  规格常量在 `miniapp/utils/frames.js`（含 `FRAME_SCALE` 缩放基准）。
- **统一叠层组件** `miniapp/components/avatar-ring`：入参 `src` / `level` / `size` / `gm`；
  **六端消费点**（信息流、名片页、点赞墙、榜单、我的页、**阅读护照**）一律复用它，
  禁止各自拼图。（阅读护照 2026-09-17 接入，此前是手搓圆圈 + 写死 emoji。）
- **馆方专属资产** `miniapp/icons/special/gm_{avatar,frame}.png`：馆长金光头像 + 鎏金冠冕外框，
  **不进 `AVATAR_IDS` 白名单**（孩子不可冒用馆方身份）。
  画法约束：金冠/光晕只用**同心环**叠加，禁止在透明画布上做整片光晕填充
  （`art.glow` 走 `paste(mask)`，会在中央糊出一个实心色块）。

## 三、色彩系统（绘本调色板）

### 3.1 当前问题

当前色彩 = 企业 SaaS 色（蓝绿橙红）涂暖纸底。绘本色彩 = **高饱和、多色并用、有纹理**。

### 3.2 绘本调色板

以**Eric Carle（好饿的毛毛虫作者）**和**Dr. Seuss** 的配色为参考：

| Token | 值 | 用途 |
|---|---|---|
| `canvas` | `#FDF8F0` | 全局底色（比当前 `#f6f2e9` 更暖更亮） |
| `paper` | `#FFFDF7` | 卡片/面板底色 |
| `ink` | `#3B2F2F` | 文字 + 边框（深棕，不是纯黑） |
| `ink-light` | `#6B5B5B` | 次要文字 |
| **primary** | `#FF6B35` | 活力橙（主按钮、重点、选中） |
| **secondary** | `#4ADE80` | 嫩芽绿（成功、上架、正向） |
| **accent-yellow** | `#FCD34D` | 太阳黄（警告、高亮、星星） |
| **accent-blue** | `#60A5FA` | 天空蓝（信息、链接、水元素） |
| **accent-pink** | `#F472B6` | 樱花粉（装饰、女孩向、爱心） |
| **accent-purple** | `#A78BFA` | 薰衣草紫（VIP、特殊、梦幻） |
| **danger** | `#EF4444` | 番茄红（删除、逾期、错误） |

**关键**：
- 不是"选一个主色其他辅助"，而是"多色并用，每个页面有自己的主色调"
- 图书管理 = 橙色 + 绿色；会员管理 = 蓝色 + 粉色；借阅 = 棕色 + 黄色
- 所有彩色底都要加轻微噪点纹理（CSS 或 SVG 背景）

### 3.3 水彩纹理

```css
/* CSS 内联水彩噪点纹理 */
.watercolor-texture {
  background-color: #FF6B35;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='100'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.08'/%3E%3C/svg%3E");
}
```

### 3.4 规范 Token 名 ↔ wxss 变量名对照（**以 `miniapp/app.wxss` 为准**）

> §3.2 的 Token 名是设计稿口径；小程序实现统一用 `miniapp/app.wxss` 的变量名——**值相同、名字不同**。落笔写代码以本表右列为准。

| 规范 Token（§3.2） | 值 | `miniapp/app.wxss` 变量 | 备注 |
|---|---|---|---|
| `canvas` | `#FDF8F0` | `--bg` | 规范名 `canvas` 在 wxss 中无同名变量 |
| `paper` | `#FFFDF7` | `--surface` | 同上 |
| `ink` | `#3B2F2F` | `--fg` | 同上 |
| `ink-light` | `#6B5B5B` | `--muted` | 同上 |
| `primary` | `#FF6B35` | `--accent` | wxss 保留兼容别名 `--primary: var(--accent)` |
| `secondary` | `#4ADE80` | `--secondary` | 名字一致 |
| `accent-yellow` | `#FCD34D` | `--sun` | 规范名在 wxss 中无同名变量 |
| `accent-blue` | `#60A5FA` | `--sky` | 同上 |
| `accent-pink` | `#F472B6` | `--sakura` | 同上 |
| `accent-purple` | `#A78BFA` | `--lavender` | 同上 |
| `danger` | `#EF4444` | `--error` | 同上 |

**说明**：
- §3.2 的 11 个 Token 在 `app.wxss` 中均有等价变量（值逐一相同）；规范侧名字 `canvas`/`paper`/`ink`/`ink-light`/`accent-yellow`/`accent-blue`/`accent-pink`/`accent-purple`/`danger` **不作为 wxss 变量存在**。
- `app.wxss` 另有规范表未列的扩展令牌（如 `--gold` / `--success` / `--warning` / `--border` / `--radius-*` 等），同以 `app.wxss` 为准。

---

## 四、字体系统

### 4.1 当前问题

`Georgia + Songti SC` = 旧报纸。绘本字体 = **圆润、友好、有手写感**。

### 4.2 字体栈

| 用途 | 当前 | 建议 |
|---|---|---|
| 标题/显示 | `Georgia, Songti SC` | `Nunito, 'ZCOOL KuaiLe', 'PingFang SC', sans-serif` |
| 正文 | system sans | `Nunito, 'PingFang SC', 'Microsoft YaHei', sans-serif` |
| 数字/统计 | system sans | `Nunito`（等宽感圆体） |
| 装饰/引用 | — | `'ZCOOL KuaiLe'`（站酷快乐体，免费商用） |

**ZCOOL KuaiLe（站酷快乐体）**：免费商用，圆润活泼，有手写感，非常适合少儿品牌。
- CDN：`https://cdn.jsdelivr.net/npm/zcool-kuaile-regular@1.0.0/index.css`
- 或本地：`npm install @chinese-fonts/zcblk`

**Nunito**：Google Fonts，圆角无衬线，友好且专业。
- CDN：`<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap" rel="stylesheet">`

### 4.3 字号规范

| 层级 | 大小 | 字重 | 用途 |
|---|---|---|---|
| Display | 32px | 800 | Dashboard 大数字 |
| H1 | 24px | 700 | 页面标题 |
| H2 | 18px | 700 | 卡片标题、Modal 标题 |
| H3 | 16px | 600 | 小标题、标签组 |
| Body | 14px | 400 | 正文、表格内容 |
| Caption | 12px | 400 | 辅助文字、时间戳 |
| Badge | 11px | 700 | 标签、徽章 |

---

## 五、图标系统

### 5.1 当前问题

`@ant-design/icons` = 1.5px 细线线性图标，冷淡。

### 5.2 建议方案

**方案 A（最低成本）**：不换库，只换用法
- 所有图标改用 `Filled` 变体（`BookOutlined` → `BookFilled`）
- 给图标加圆形底色 + 粗边框，像贴纸

```css
.paint-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 2px solid #3B2F2F;
  background: #FCD34D;
  font-size: 16px;
}
```

**方案 B（推荐）**：引入 `lucide-react`
- 线条更粗（2px）、圆角更友好
- 配合圆形底色使用

**方案 C（最佳）**：定制 SVG 图标
- 每个功能图标都是"角色图标"（小熊、星星、书本）
- 统一风格：面性 + 粗黑边 + 表情
- 放在 `admin-web/src/assets/icons/` 和 `miniapp/assets/icons/`

### 5.3 Sidebar 图标彩色化

每个菜单项的图标固定一个底色，像彩色按钮：

| 菜单 | 图标底色 | 图标色 |
|---|---|---|
| 仪表盘 | `#FCD34D` 黄 | `#3B2F2F` |
| 图书管理 | `#FF6B35` 橙 | `#FFFFFF` |
| 会员管理 | `#60A5FA` 蓝 | `#FFFFFF` |
| 押金与赔偿 | `#A78BFA` 紫 | `#FFFFFF` |
| 借阅操作台 | `#4ADE80` 绿 | `#3B2F2F` |
| 预约管理 | `#F472B6` 粉 | `#FFFFFF` |
| 成长与测验 | `#FCD34D` 黄 | `#3B2F2F` |
| 线下活动 | `#FF6B35` 橙 | `#FFFFFF` |
| 退款中心 | `#EF4444` 红 | `#FFFFFF` |
| 员工管理 | `#60A5FA` 蓝 | `#FFFFFF` |
| 系统配置 | `#6B5B5B` 灰 | `#FFFFFF` |
| 审计日志 | `#A78BFA` 紫 | `#FFFFFF` |

---

## 六、动效系统

### 6.1 原则

微弹、橡皮筋、有质量感。不要线性滑动，不要淡入淡出。

### 6.2 具体规范

| 场景 | 当前 | 建议 |
|---|---|---|
| 按钮 hover | `opacity` 变化 | `transform: scale(1.05)` + `box-shadow` 扩大 |
| 按钮点击 | `active` 颜色变深 | `transform: translate(2px, 2px)` + shadow 收缩（像按下贴纸） |
| 卡片 hover | 无 | `transform: translateY(-4px)` + shadow 扩大 |
| 元素进入 | 无 | `animation: bounceIn 0.4s`（弹跳进入） |
| Modal 打开 | fade | `transform: scale(0.9) → scale(1)` + `bounce` |
| Toast/消息 | slide | `transform: translateY(-20px) → translateY(0)` + `bounce` |
| 加载 | Spin | 角色动画（书虫贝贝翻书） |
| 成功反馈 | message.success | 角色弹出 + 彩纸飘落（confetti） |

### 6.3 CSS 动画

```css
@keyframes bounceIn {
  0% { opacity: 0; transform: scale(0.3); }
  50% { opacity: 1; transform: scale(1.05); }
  70% { transform: scale(0.9); }
  100% { transform: scale(1); }
}

@keyframes wiggle {
  0%, 100% { transform: rotate(0deg); }
  25% { transform: rotate(-3deg); }
  75% { transform: rotate(3deg); }
}

/* 角色空闲动画 */
.bookworm-idle {
  animation: wiggle 3s ease-in-out infinite;
}
```

---

## 七、admin-web vs miniapp 差异

### 7.1 共同规范

- 色彩系统一致
- 角色插画一致
- 圆角/边框/阴影规范一致
- 字体栈一致（miniapp `--font-display`: `ZCOOL KuaiLe` / `Yuanti SC` / `YouYuan` / `PingFang SC`；`--font-body`: `Nunito` 系统栈；见 `miniapp/app.wxss`）

### 7.2 admin-web 特有

- 空间更大：sidebar 240px、卡片更大、padding 更宽
- 表格保留但卡片化：数据密集页用卡片行
- 角色插画 100-120px
- 支持 hover 动效
- Modal 更大、更复杂

### 7.3 miniapp 特有

- 空间紧凑：底部 tab 导航、列表项更紧凑
- 卡片列表为主：图书列表、会员列表都是卡片
- 角色插画 60-80px
- 动效更克制（性能考虑）
- 页面转场：滑动 + 轻微 bounce
- 底部固定操作栏：大胶囊按钮，像游戏 UI

---

## 八、实现路径（分 4 个迭代）

### 迭代 1：骨架重塑（2-3 天）

**目标**：改组件形态，不加插画。

1. **新建 `theme-paint.ts`**：基于当前 ``admin-web/src/theme-paint.ts``，覆盖：
   - 圆角系统（Button 16、Card 20、Modal 24、Tag 999px）
   - 边框系统（所有组件加粗边框 token）
   - 阴影系统（硬边偏移）
   - 色彩 token（绘本调色板）
2. **新建全局 CSS**：`styles/paint.css`
   - `.paint-button`、`.paint-card`、`.paint-tag`、`.paint-border`
   - 水彩噪点纹理
   - 动画 keyframes
3. **替换主按钮**：所有 `Button type="primary"` → `paint-button paint-button--primary`
4. **验证**：gate.sh（tsc 0 错）

### 迭代 2：字体 + Sidebar（1-2 天）

1. **加载字体**：Nunito + ZCOOL KuaiLe（CDN 或本地）
2. **替换标题字体**：Layout logo、页面标题、Modal 标题
3. **Sidebar 彩色图标**：每个菜单项加圆形彩色底色
4. **Sidebar 加宽**：208px → 240px

### 迭代 3：表格卡片化 + 角色插画（3-4 天）

1. **BookManage 卡片列表**：从 Table → 卡片列表（大工程）
2. **角色 SVG**：设计/生成 8 个角色（书虫贝贝、星星点点等）
   - 可用 AI 生成统一风格 SVG（Midjourney/Stable Diffusion → SVG 化）
   - 或找设计师出统一规范
3. **空状态组件**：`<EmptyState character="bookworm" message="..." />`
4. **加载态组件**：`<LoadingCharacter character="bookworm" />`
5. **逐个页面替换空状态**：BookManage、MemberManage、CirculationDesk 等

### 迭代 4：动效 + 细节打磨（2-3 天）

1. **按钮动效**：hover scale + click translate + shadow 收缩
2. **卡片动效**：hover 上浮
3. **Modal 动效**：bounceIn
4. **Toast 动效**：弹跳进入
5. **成功反馈**：彩纸飘落（轻量 confetti）
6. **全局走查**：每个页面、每个交互状态
7. **性能优化**：SVG 压缩、CSS 压缩、字体子集化

---

## 九、技术栈建议

| 需求 | 工具 | 备注 |
|---|---|---|
| 角色 SVG 生成 | Midjourney / DALL-E → Figma → SVG | 统一风格后批量生成 |
| 字体加载 | Google Fonts CDN + 本地 fallback | Nunito 用 CDN，ZCOOL KuaiLe 可本地 |
| 动画库 | 纯 CSS（推荐）或 `framer-motion` | CSS 性能更好，framer-motion 更灵活 |
| Confetti | `canvas-confetti` | `pnpm add canvas-confetti`，轻量 |
| 水彩纹理 | CSS `feTurbulence` SVG filter | 纯 CSS，零依赖 |
| 图标 | `lucide-react` + 自定义 SVG | 先 lucide，后逐步替换自定义 |

---

## 十、诚实标注

1. **本规范基于行业参考 + 截图推断**，未经过真实用户测试；
2. **角色 SVG 需要设计师出规范**，AI 生成后需统一风格调整；
3. **表格卡片化是最大工程**，BookManage 从 Table 改卡片列表涉及分页、排序、筛选重构；
4. **ZCOOL KuaiLe 字体需确认商用授权**，站酷快乐体是免费商用，但需保留版权声明；
5. **水彩纹理 CSS filter 在低端设备可能有性能问题**，建议提供降级方案（纯色底）；
6. **大圆角 + 粗边框在数据密集页面可能降低信息密度**，需在实际数据中测试可读性。

---

## 十一、admin-web 实施记录与技术坑（2026-08-25）

### 实施方式

admin-web 端未按本规范原定的 4 个迭代分阶段实施，而是一次性整体落地，以配合用户全量审查验收。

### 已落地范围

- `admin-web/src/theme-paint.ts`：绘本调色板、圆角/边框/阴影 token
- `admin-web/src/styles/paint.css`：Button/Input/Tag/Card/Modal/Tabs/Table/Pagination/Menu/Upload/Dropdown/Form/Typography 全局覆盖
- `admin-web/src/components/PaintEmpty.tsx`：8 角色空状态插画
- `admin-web/src/components/PaintLoading.tsx`：7 角色加载态插画
- `admin-web/src/components/PreviewImage.tsx`：**全后台唯一**的图片/凭证预览（缩略图 + 全屏干净预览；见 §十三）
- `admin-web/index.html`：Nunito + ZCOOL KuaiLe 字体加载
- `admin-web/src/pages/Layout.tsx`：Sidebar 加宽 240px、菜单图标按 route key 彩色化
- 10 个页面标题字体替换为 `var(--font-display)`
- 各页面 `Table` 按模块传入 `PaintEmpty` 角色

### 已规避的技术坑

| 坑 | 根因 | 修复 |
|---|---|---|
| 弹窗按钮全部失效 | `paint.css` 给 `.ant-modal-wrap .ant-modal` 加了 `animation: paint-bounce-in`（含 `transform: scale()`），破坏 antd 5 pointer-events 层级 | 删除 Modal 自定义动画，回退到 antd 自带动画 |
| 按钮偶尔点不上 | `.ant-btn:active { transform: translate(1px,1px) }` 使按钮按下时移出鼠标点击区域 | 删除 `:active` 时的 `transform` |
| 菜单图标颜色随权限错位 | 用 `.ant-menu-item:nth-child(N)` 匹配颜色，但 `Layout.tsx` 按权限过滤后菜单数量变化 | 改为 `Layout.tsx` 按 route key 传 `iconBgMap` + `iconColorMap` |
| 字体硬编码难维护 | 16 处内联 `fontFamily: "'ZCOOL KuaiLe', ..."` | 统一改为 `var(--font-display)` |

### miniapp 小程序端绘本风（2026-08-30 已落地）

- **已实施**：`miniapp/app.wxss` 落地整套绘本令牌（改值不改名，**全部页面 wxss 零改动继承**——2026-09-20 实测 39 个 `.wxss`，原文写的"43 个"是估数）；`miniapp/components/avatar-ring/` 等级头像框叠层组件；`miniapp/icons/frames/` 四档头像框资产。
- **机械门禁**：`scripts/check_miniapp_style.py`（R1-**R14** 规则 + S1-S3 三重自证），已进 `scripts/gate.sh` 第 5 步「契约与反假绿」。

---

## 十二、运营增强组件（2026-08-28）

### 12.1 PaintPagination 分页组件

admin-web 列表页统一使用绘本风分页底栏，替代 antd 原生 Pagination。

**行为**：
- 显示当前页码范围、总条数、总页数
- 快速跳转输入框 + 15/30/50/70/100 每页条数预设按钮
- **跳至页输入即时生效**：输入合法页码即跳转，无需回车/失焦（与「每页显示」输入一致）；用 `lastJumpRef` 去重，避免重复请求
- 与 `usePaintPagination` 配套，统一 `pageSize` / `currentPage` 状态
- 已接入 9 个列表页：`BookManage`、`AuditLog`、`DepositManage`、`MemberManage`、`Reservations`、`RefundCenter`、`ActivityManage`、`GrowthManage`、`SystemConfig`

**视觉约束**：
- 使用 `theme-paint.ts` 圆角/边框/阴影 token
- 当前页高亮用 `primary` 橙色
- 保持与 `paint.css` 按钮形态一致

### 12.2 批量操作模式

**使用场景**：BookManage 图书批量上架/下架。

**交互**：
- 表格行首复选框支持跨页选择（`preserveSelectedRowKeys: true`）
- 顶部批量操作栏显示已选数量
- 二次确认后调用后端批量接口，刷新当前页

**后端约定**：
- `POST /api/admin/books/batch-toggle-status`
- Body: `{ ids: number[], status: 0 | 1 }`
- 事务内逐个切换，失败回滚

### 12.3 上传进度反馈

**使用场景**：BookDetail 封面/音频上传。

**实现**：
- 前端改用 `XMLHttpRequest`，监听 `onprogress`
- 实时更新 `Progress` 组件百分比
- 上传中禁用保存/返回按钮，防止并发

### 12.4 表格横向滚动条显式化

**问题**：macOS 默认 overlay 滚动条在表格内隐藏，鼠标用户看不到可横向滚动；纯 CSS `::-webkit-scrollbar` 无法强制。

**最终方案（`c369b4b`）**：
- 新增 `PaintHScrollbar` 组件：表格下方自绘可拖动 thumb + 点击轨道跳转，100% 始终可见
- `paint.css` 隐藏 `.ant-table-content::-webkit-scrollbar:horizontal`，避免与自定义滚动条重复
- `Layout.tsx` 内层 `AntLayout` / `Content` 设 `minWidth: 0`，防止分页组件撑开整页

### 12.5 布局：侧边栏固定、内容独立滚动

**问题**：整页随内容滚动，左侧导航滚出屏幕。

**规约（`5980516`）**：
- 外层 `AntLayout`：`height: 100vh; overflow: hidden`
- `Sider`：`position: sticky; top: 0; height: 100vh`
- 右侧 `Content`：`flex: 1 1 auto; overflow: auto`（Header 固定不滚）

### 12.6 媒体上传覆盖后的缓存失效

**问题**：封面/音频固定文件名 + 固定 URL，重传后浏览器/audio 元素仍显示旧内容。

**规约（`5980516`）**：
- 后端存储文件名带随机后缀（`save_cover_jpg` / `save_audio_mp3`），重传生成新路径并删除旧文件
- 前端 `apiMediaUrl(bookId, kind, version)` 追加 `v=<文件路径>` 参数；BookDetail 传 `book.cover_path` / `book.audio_path`
- 重传后路径变 → URL 变 → 浏览器/`<audio>` 强制加载新内容（含新时长）

---

## 十三、媒体预览统一规范（2026-09-15，用户裁定）

### 13.1 唯一基准

**用户原话**：「图书详情编辑页那个封面预览非常好，非常清新干净，我要全后台所有有图片或者凭证
预览的地方统一采用这个效果……我不希望看到收款凭证那种**带窗口的预览**，很不好用。」

基准 = 图书详情「封面与音频」卡里的封面预览：

| 维度 | 规约 |
|---|---|
| 缩略图 | 圆角 6、`1px solid var(--paint-border)`、纸色底（`var(--paint-paper-dim)`）、`object-fit: cover`、`cursor: zoom-in` |
| 悬停遮罩 | 半透明黑 + 居中「预览」二字（`preview={{ mask: "预览" }}`，会顶掉 antd 默认的眼睛图标） |
| 点开之后 | antd `Image` 内建 preview：**全屏蒙层**（`colorBgMask`）+ 底部工具条（缩放 / 左转 / 右转 / 水平翻转 / 垂直翻转 / 放大镜 / 复位），`Esc` 或右上角 × 关闭 |
| 空态 | 虚线框 + `var(--paint-ink-light)` 小字（如「未上传」），尺寸与缩略图一致 |

### 13.2 禁止事项

- **禁止**用 `<Modal><img/></Modal>` 做预览（「带窗口的预览」）：图被压在 640px 弹窗里、
  `maxHeight: 70vh`、**没有缩放**，看收款凭证/转账截图等于拿放大镜看缩略图；
- **禁止**给同一交互写第 2 份实现：新页面要预览，先复用组件，不要「照着抄一遍」；
- **禁止**裸 `<img>` 展示可作为内容核对的图（封面/凭证/卡片/报告）而不给预览入口。

### 13.3 组件用法

`admin-web/src/components/PreviewImage.tsx`（当前 8 处已接入：BookDetail 封面、MemberManage 收款凭证
回看 + 凭证/评估报告选图本地预览、CircleManage 成就卡片、GrowthManage 周报/月报、ActivityManage 详情封面
与编辑封面、**ActivityManage 图文详情配图块预览**〔2026-09-20 新增〕）

```tsx
// ① 缩略图 + 点开（固定画框；height 省略 = 按原图比例撑满 width，用于长图报告）
<PreviewImage src={url} alt="封面" width={72} height={100} emptyText="未上传" />

// ② 已有按钮/图标当入口（查看凭证、上传前本地预览）——命令式打开
const viewer = useImageViewer();
<Button onClick={() => viewer.open(url)}>查看凭证</Button>
{viewer.node}
// Blob URL 的回收挂在关闭回调上，避免 Modal 时代「onCancel 里 revoke」的写法丢失：
viewer.open(objectUrl, () => URL.revokeObjectURL(objectUrl));
```

### 13.4 两个技术坑（详见错误库 §七十六）

| 坑 | 结论 |
|---|---|
| 受控预览宿主写 `style={{ display: "none" }}` 会不会把预览一起隐藏？ | **不会**。rc-image 把 `style` 落到 `<img>`（遮罩另读 `style.display`），Preview 由 Portal 挂到 `body`，所以在 Fragment 的兄弟位，不受影响 |
| `preview={{ mask: null }}` 是不是「用默认遮罩」？ | **不是**，是**不要遮罩**。antd 的合并是 `{mask: 默认, ...调用方}`，展开在后；要默认就别传这个键 |

---

## 十四、后端出图统一规范（2026-09-15，用户报障后立规）

**用户原话**：「生成的周报和月报，跟本项目的样式风格格格不入。」

### 14.1 铁律：凡是要出图的东西，一律走 `reading_circle/art.py`

后端**只有一套**绘图引擎：`backend/domain/reading_circle/art.py`（超采样画布 + 马卡龙渐变 +
白描边贴纸字 + 圆角气泡 + 云朵/星闪/彩虹 + 纸纹颗粒 + 吉祥物）。当前三类消费方：

| 资产 | 位置 | 色盘 |
|---|---|---|
| 阅读圈卡片（大图 + 缩略图） | `reading_circle/card_render.py` | 按卡型取 `art.PALETTES` |
| 头像 24 枚 / 勋章 9 枚 / 名片海报 | `scripts/gen_wm15_*`、`profile_service` | 按类型取 `art.PALETTES` / `KIND_BASE` |
| **周报 / 月报** | `growth/report_service.py::paint_report` | 周报=薰衣草紫 `weekly_report`；月报=薄荷绿 `books_count` |

- **禁止**在业务模块里 `ImageDraw.rectangle/rounded_rectangle + ImageFont.truetype(系统字体)`
  手搓图（2026-09-15 之前的报告图就是这么写的：深蓝横幅 + Hiragino，与绘本语言零交集）；
- 引擎里缺原语 → **往 art.py 加原语**（云朵/星星/气泡/贴纸字都已就绪），不要另起一套；
- 字体一律 `art.font_cn()`（中文，Hiragino Sans GB **W6**）+ `art.font_round()`（数字/字母，
  .SF NS Rounded）——数字用圆体是卡哇伊的关键信号，别退回系统默认；
- 出图后**必须自己看一遍**：`textlength` 先量字宽再定容器（别目测写死，见错误库 E-20260915-31）；
- 尺寸变更要同步检查消费端（管理端 `PreviewImage`、小程序报告页）——报告图固定 750×1100。

### 14.2 报告图版式（750×1100）

标题横幅 → 孩子·周期 → 主数字卡（本期词数 + **孩子自己的头像动物**做吉祥物探头）→
2×2 统计卡（读完/打卡/测验/正确率）→ 鼓励语 → 日期条 → 馆标。
零阅读周期不摆空卡，鼓励语换成「这个周期还没有阅读记录，今晚挑一本开始吧～」。

### 14.3 机械兜底与它的边界

`tests/unit/test_wm8_features.py::test_report_image_drawn_on_picture_book_palette` 用**像素**
锁死「报告图画在哪个色盘上」（左上角背景 = `art.PALETTES[...]["top"]`，容差 30 抵纸纹噪点），
已自证喂旧版深蓝横幅会红。**它只锁色盘归属，锁不住排版退化——排版必须目视。**

## 十五、小程序 UI 图标体系（2026-09-16 立规）

### 15.1 为什么禁用 emoji 当图标

用户长期提的"视觉割裂"里，emoji 是最典型的一类：**同一个 emoji 在 iOS / Android /
微信开发者工具是三种画风**（形状、配色、粗细都不同），颜色与绘本令牌无关，也无法与卡片
描边/圆角对齐；系统升级还会换脸。故：**图标槽位一律用自家 PNG 资产，不用 emoji**。

### 15.2 资产与生成

- 生成器：`scripts/gen_ui_icons.py`（`python -m scripts.gen_ui_icons [--sheet]`）
- 资产：`miniapp/icons/ui/*.png`（96×96 透明，37 枚；与 tabbar 图标同规格）
- 画法：`backend/domain/reading_circle/art.py` 引擎——粗墨线描边（INK `#5B4636`）+
  马卡龙实色 + 白高光 + 轻纸纹，**与头像/勋章/成就卡同一套语言**（别再新起一种画法）
- 命名：概念名（`calendar` / `trophy` / `bookmark` / `headphone` / `search` / `cover` / `lock` /
  `party` / `warning` / `tip` / `globe` / `sparkle` / `edit` / `refund` / `crown` / `door` /
  `transfer` / `clock` / `pin` / `heart(-off)` / `star(-off)` / `wallet` / `receipt` / `card` /
  `chart` / `report` / `clipboard` / `ticket` / `child` / `phone` / `key` / `empty` …）

### 15.3 用法

```xml
<!-- 直接引用 -->
<image class="fi-icon" src="/icons/ui/calendar.png" mode="aspectFit" />
<!-- 条件切换（同一槽位多态） -->
<image class="like-icon" src="/icons/ui/{{liked ? 'heart' : 'heart-off'}}.png" mode="aspectFit" />
<!-- 共享空态组件：传概念名，不传 emoji -->
<empty-state icon-name="bell" title="暂无消息" desc="有新消息时会第一时间通知你" />
```

尺寸写在页面的槽位类里（`width/height`，rpx），**不要**改资产的像素尺寸去适配单页。

### 15.4 例外：文字字形不是 emoji

`✓ ✕ ★ ☆ ▶ ◀ ▲ ▼ ● ○ ■ □ ◆ ◇ ※` 这些**排版字形**属设计系统（`已打卡 ✓`、成绩环的 ✓/✕），
不在禁用范围——门禁 R13a 有 `TYPO_GLYPHS` 白名单把它们排除。
（★/☆ 在白名单里但**当前无页面在用**：星级已是 `icons/ui/star.png`/`star-off.png` 资产，
2026-09-17 起为亮黄 `#FFD84D`。）

### 15.5 emoji 豁免口径：**文案语气**可以用，**图标位**不可以（2026-09-20，fix44 R10 / 专家 Q12 裁定）

用户裁定"不加检查器"（机械判定会把语气 emoji 也一起禁掉，误伤面大于收益），改为**写清边界**：

| 位置 | 口径 | 例 |
|---|---|---|
| **通知/文案正文的语气 emoji** | ✅ **允许**（豁免） | 「馆长亲赞 ✨」「太棒了 ✨」「今天是我的生日 🎂」「馆内最高荣誉 · 已点缀在你的帖子上 ✨」 |
| **图标槽位**（`class` 含 `icon`/`emoji` 的元素里当图标用） | ❌ 禁止（R13a 拦） | 曾把 `calendar`/`trophy` 写成 emoji —— 已换 `/icons/ui/*.png` |
| **排版字形** | ✅ 允许（见 §15.4） | `已打卡 ✓`、成绩环 ✓/✕ |

> **为什么语气 emoji 可以留**：它们参与的是**文案情绪**，不是布局与视觉系统；三端画风差异
> 在"一句话里的点缀"上不构成割裂。而一旦成为图标（独立占位、需要与描边/圆角/令牌对齐），
> 三端画风差异就会直接暴露——那才是 §15.1 要治的病。

**已知灰区（如实登记，本轮不动；红线 30：不进则明说）**：下列地方 emoji **实质在当图标用**，
但形态上逃过 R13a（emoji 在 JS 数据里、或 class 不含 icon）：

- `components/error-view/error-view.js` 的 `icons = { error:'😔', network:'📡', empty:'📭', permission:'🔒' }`；
- `pages/index/index.wxml` 的章节标题前缀（`🎧 继续听` / `📖 今日推荐` / `🏠 还没有孩子档案`）；
- `pages/member/member.wxml:44` 头像兜底 `'👶'`（孩子无头像时）。

> 这三处**不在本轮 fix44 范围**（改动面涉及多页布局与资产补齐），登记为**已知缺口**：
> 后续若要收口，做法同 §15.2（用 `gen_ui_icons.py` 出资产 + 换 `icon-name`），
> **不要**零散手写 emoji 继续扩散。

### 15.6 机械门禁 R13（`scripts/check_miniapp_style.py`）

| 规则 | 判定 |
|---|---|
| R13a | 图标槽位（class 含 `icon` / `emoji`）里出现 emoji → FAIL |
| R13b | `/icons/ui/*.png` 与 `icon-name="x"` 引用的资产不存在 → FAIL |

三重自证：注入 emoji 必命中、注入不存在的图标名必命中、**存在的图标名不得误报**（首版把
`check-result-icon` 的 ✓、`fc-close-icon` 的 ✕ 误报成违规，已加白名单并留自证）。

### 15.7 机械门禁 R14：WXML 结构（2026-09-20 补，`scripts/check_miniapp_style.py`）

| 规则 | 判定 |
|---|---|
| R14 | WXML 注释必须以 `-->` 收尾（**写成 JS 风格 `/* */` 会吞掉半个模板**）；标签必须正确配对 |

来由（活动模块 B 批施工自伤）：我在 wxml 里写了 JS 风格注释，编译报错行**指向别处**（实测差 60 行），
模拟器还一直停在旧帧骗人——最后靠读开发者工具自己的日志 + 写忠实解析器才定位。
教训入库错误记忆库 **§八十三**（含"轻量按行扫描器会撒谎：它报闭合 ✓，编译器报错"）。
检查器含注入自证（喂坏注释必红）。

### 15.8 机械门禁 R16：类名两端对账（2026-09-23 补，`scripts/check_miniapp_style.py`）

**来由（G2 定标）**：R12 只查**一个方向**——WXML 用到、本页 wxss 没定义（悬空类名）。
反方向是盲区，代价实测过：我的订单类型图标 JS 给 `type1`、WXSS 定义的是 `.order-icon-type1`
→ **底色从上线起从未生效、且无任何报错**（错误库 §一百；R12 扫不到，因为那是"样式定义了、名字对不上"）。

| 规则 | 判定 | 是否进退出码 |
|---|---|---|
| **R16a 死类名** | 本页 wxss 定义、但本页 wxml（含插值可判部分，见 `_class_tokens_from_attr`）+ js 文本里**都找不到**的类名 → 死样式或"名字对不上" | **暂只报告**（先抽样估误报率，<10% 再冻结基线；见 `docs/09 G2`） |
| **R16b JS 提供的类名必须存在** | JS 里以类名身份给出的字符串（`cls:` / `className:` / `class:` 的值）必须在该页 wxss 作用域（含 `app.wxss` 与 `@import`）内有定义 → 否则端上**静默不生效** | **进**（当前全库 0 违规，锁住不许再犯） |

**规范条款（R16b 的源头口径）**：**类名必须整名提供，禁止把前缀留在模板里、名字放在 JS 数据里**。
- ✅ 允许：`class="badge-{{cond ? 'paid' : 'x'}}`（前缀 + 字面量分支，静态可判 → R12/R16a 都能解析）
- ❌ 禁止：`class="order-icon-{{item.icon}}"` 且 JS 只给 `'type1'`（**两端拼出来的名字没有任何检查器能对上**，
  这正是订单图标那条 bug 的形态）
- 反例的修法（已落地）：JS 直接给全名 `cls: 'order-icon-type1'`，模板 `class="{{item.cls}}"`。

### 14.4 边界（如实标注，别把规矩说过头）

`scripts/seed_demo_library.py` 里**演示书封面/活动封面**仍是自己一套画法（天空渐变 + 太阳 + 云），
走的是**演示数据生成器**而非产品资产链路；它的产出是绘本感的插画封面、用户也未报障，
故本轮**不动**。若将来要收敛，方向同样是"抽成 art.py 原语 + 生成器调用"。

## 十六、图片体积规范（2026-09-17 立规，用户裁定）

> 用户原话：「未来服务器磁盘空间可能没那么大…**只要是上传或者是自动生成的图片，就必须控制大小**，
> 不管运营人员上传多大的图片，后端都应该能自动压缩，自动生成的图片也要控制一下大小，
> 保证小程序端显示清晰即可」。

### 16.1 一条铁律：图片落盘只有两个出口

| 出口 | 用于 | 位置 |
|---|---|---|
| `file_storage.normalize_image()` | **所有上传图**（EXIF 摆正 → 长边限幅 → JPEG → 体积兜底降质） | `backend/common/file_storage.py` |
| `art.Canvas.finish(path, quality=)` | **所有生成图**（卡片/报告/海报；需要 alpha 的资产传 `quality=None` 出 PNG） | `backend/domain/reading_circle/art.py` |

**禁止**在业务代码里自己 `img.save(...)` / `cv.img.resize(...).save(...)`——历史上"同一业务两套画法"
已经犯过三次（报告图、活动封面、海报），体积口径再分叉就是第四次。

### 16.2 目标值（实测口径，数值全在 SystemConfig）

| 类型 | 显示需求 | 目标 | 实测 |
|---|---|---|---|
| 阅读圈大图 750×1180 | 信息流点开全屏 + 长按存相册 | JPEG q85 | **769KB → 67KB** |
| 阅读圈缩略图 618×618 | 信息流卡片满宽 | JPEG q85 | **317KB → 28KB** |
| 周报/月报 750×1100 | 全屏长图 + 存相册 | JPEG q85 | **781KB → 92KB** |
| 图书封面 | 列表 340rpx / 详情 220rpx | 长边 ≤1080 JPEG | 600×900 → 32KB |
| 活动封面（横版） | banner | 长边 ≤1200 JPEG | 900×320 → 18KB |
| **活动图文配图** | 详情页图文（2026-09-20 新增） | 长边 ≤1200 JPEG | 实测 1200×800 自绘插画 → 38KB |
| 收款凭证 / 观察报告 | **要放大看清小字** | 长边 ≤1600 JPEG | 1080 宽 → 55KB |

配置键：`image_upload_max_mb` / `image_upload_max_output_kb` / `image_jpeg_quality` /
`image_generated_jpeg_quality` / `image_cover_max_edge` / `image_activity_cover_max_edge` / `image_doc_max_edge`。
（**活动封面与活动图文配图共用 `image_activity_cover_max_edge`**〔2026-09-20〕——图文配图没另立配置键，
落盘口径 `file_storage._POLICY_KEYS["activity_detail"]` 直接引用它。）

### 16.3 三条工程约束（踩过才知道）

1. **为什么必须 JPEG**：本项目的插画一律带 `paper_grain` 纸纹噪点，**PNG 对噪点几乎压不动**
   （卡片 761KB 基本都是噪点）。海报早在 fix34-R2 就改过 JPEG，卡片与报告是漏网的同类。
2. **改图必须换文件名（2026-09-23 起统一为「内容寻址」）**：小程序 `<image>` 按 **URL** 缓存，
   原地覆盖会让端上永远吃旧图。生成图文件名带**内容指纹**（`sha256(字节)[:12]`，单一来源
   `file_storage.content_tag`）或规格版本号（缩略图 `thumb_v4_*`）：
   **内容变 ⇒ 指纹变 ⇒ 名字变 ⇒ 缓存必刷**；**内容不变 ⇒ 同名复用（连重绘都省、不新增文件）**。
   改前是随机 tag（`secrets.token_hex(6)` / `uuid4()[:8]`）⇒ 清库+seed 每轮新增 **76 cover + 40 circle**
   孤儿文件（`docs/09 G7`；实测单 ISBN 累积 57~59 个同名变体），故统一改内容寻址；
   上传图原地重编码不需要换名（内容没变，只是变小了）。
3. **报告图按内容摘要幂等**：报告图端点**每次请求都会出图**，旧实现用 随机 uuid 命名 命名 ⇒
   家长每看一次就多一个文件（磁盘无上限增长）。现按 `sha256(report_data)[:10]` 命名并清理同
   (孩子,类型) 的历史文件：同内容零新增、内容变则换名。

### 16.4 EXIF 方向（顺带修的历史缺陷）

`Image.open(...).convert("RGB")` **不会应用 EXIF Orientation**：运营用手机竖拍上传凭证/报告，
存下来是躺着的（全项目此前无 `exif_transpose`）。统一管线已加，并有测试锁死。

### 16.5 机械门禁：媒体纪律三条（2026-09-20，fix44 R4）

§16.1 的"铁律"与 §16.3 第 2 条原来**只写在文档里**（靠人记得 → 历史上已犯三次）。
现由 `scripts/check_media_discipline.py` 机化，接在 `gate.sh` 第 [5] 步（改规则必同步其 docstring 清单）：

| 规则 | 内容 | 拦截形状 |
|---|---|---|
| **M1 落盘单出口** | `backend/` 内写媒体文件只许三个出口：`file_storage.py`（上传/回压）、`art.py`（`Canvas.finish`）、`card_render.py`（`save_jpeg`） | 其它后端文件出现 `open(...,"wb")` 或 `img.save(<路径>)`（含 `img.save("x.png")` 字面量路径） |
| **M2 破缓存单出口** | `?v=` 只许出现在 `backend/common/file_utils.py`（`media_version` 三函数） | `backend/` `admin-web/src/` `miniapp/` 别处手写 `?v=`（写死→永远吃旧图；每次新值→缓存永不命中） |
| **M3 清理脚本默认 dry-run** | `scripts/` 下 clean/purge/optimize/regen/trash 类脚本必须有**正向** apply 开关（`--apply`/`--trash`/`--no-dry-run`/`--force`），破坏性调用必须在守卫内 | ① 缺正向开关；② 破坏性调用（`os.remove`/`unlink`/`open wb`/…）在守卫外；③ 把安全阀做成 opt-in 的 `--dry-run`（`action="store_true"` 且非 `default=True`） |

**范围说明**（红线 30：门禁只管一部分就必须说清）：M1 只管 `backend/`——`scripts/` 下的一次性生成/运维
脚本不跑在生产请求路径上，不在 M1 范围；其中会删东西的另受 M3 管。判定"删自己的临时产物"（`out = ROOT / "tmp-icon-opt.png"` → `out.unlink()`）不计 M3b 违例。

**本轮实修**：`scripts/regen_covers.py` 原先是 opt-in `--dry-run`（**默认就写盘 + 删旧封面 + commit**），
与全项目清理脚本纪律相反 → 改为默认 dry-run + `--apply`。检查器实树注入自证：M1/M2/M3 三类违例全命中，
还原后全绿（`python scripts/check_media_discipline.py --self-test` 可单跑 S1 注入自检）。

### 16.6 清理/回压脚本的三个"靠运气守住"的陷阱（2026-09-20，fix44 R3）

| # | 陷阱 | 真因 | 修法（含回归测试） |
|---|---|---|---|
| ① | `cleanup_uploads` 的**证据目录保护名存实亡** | `PROTECTED_PREFIXES` 里写 `"-samples/"`，判定用 `rel.startswith(p)`——真实目录名是 `wm15-samples/…` → **永远匹配不到**；当时没出事只因它同时也不在可再生白名单里 | 新增 `PROTECTED_SUFFIX_DIRS`，按**路径首段后缀**匹配（`wm15-samples/` ✓ `fix34-samples/` ✓） |
| ② | `cover/activity/` 与书封**同前缀不同口径**，回压会串 | 回压脚本靠 DB 引用集区分"哪些文件算数"，口径却按前缀一刀切 | `_UPLOAD_SCOPES` 增 `"cover/activity/"` + 新增 `scope_of()` **最长前缀**匹配（与字典顺序解耦） |
| ③ | 活动封面被按书封口径回压 | 前缀只有 `"cover/"` → `activity_cover`(1200) 当成 `cover`(1080) | 同上；回归测试另断言两个口径的默认长边**必须不同**（哪天被改成同值就该有人回头看） |
| ④ | **新增媒体目录未进清理脚本 → 被当孤儿删**（2026-09-20 活动图文配图） | `cleanup_uploads` 有两张名单：**引用集**（DB 里被引用的文件）与**保护名单**；新目录一处漏接就会被清掉 | `"activity_detail/"` 入 `PROTECTED_PREFIXES` + 引用集解析 `Activity.detail_blocks` 图片路径（`cleanup_uploads.py`）；测试 `test_wm9b_activity_detail_blocks.py::test_cleanup_registers_detail_images` |
| ⑤ | 图文配图**不在** `optimize_uploads` 的回压范围 | 回压脚本的 `_UPLOAD_SCOPES` 只覆盖封面/凭证/观察报告（`cover/`、`cover/activity/`、`voucher/`、`observation/`），**没有** `activity_detail/` | 如实登记，不假装已覆盖：图文配图目前只靠**落盘时**的 `activity_detail` 口径（长边 1200 JPEG）控制体积；若将来需要回压，先加 scope 再跑（别直接改前缀，见 ②） |

> 回归锁：`tests/unit/test_fix44_media_scope.py`（3 例）。这三条属"守住了但靠运气"——
> 靠运气的东西迟早会输，故一律改成显式规则 + 测试锁死。

### 16.7 报告图实测数据（2026-09-20，fix44 R5；此前三项全无数据）

对 `uploads/reports/report_weekly_1_*.jpg`（演示孩周报，内容摘要 `ea03a0e2bc`）实测：

| 项 | 实测值 | 口径说明 |
|---|---|---|
| 产出规格 | **750×1100 px / 101 KB** | JPEG q85（`image_generated_jpeg_quality`） |
| 生成耗时（冷） | **634 ms**（直调 `generate_image`）/ **765 ms**（HTTP 家长入口） | 含绘制超采样画布 + LANCZOS 降采样 + JPEG 编码 + 老摘要文件清理 |
| 生成耗时（命中复用） | **7.4 ms** / **16 ms** | 内容摘要不变 → 连重绘都省（2026-09-17 改的内容寻址） |
| 小字笔画（页脚馆标 23px） | 行带高 9 px，笔画段中位 **2 px**，最细 1 px（1px 段占 23%） | 墨色阈值 L<110 自动定位行带；**深色系文字**才测（白字压彩底不适用该阈值） |
| 小字笔画（统计卡标签 26px） | 笔画段中位 **2 px**，最细 1 px（1px 段占 14%） | 同上 |
| 屏显 | 750 px 图 = 手机 375 pt 宽的 **2x retina**；页脚 23 px ≈ 屏显 11.5 pt | **主用途（存相册/微信分享）：清晰** |
| 打印（300 dpi，A5） | 6.35×9.31 cm；页脚 23 px = **1.95 mm ≈ 6 pt**；最细笔画 1 px = **0.085 mm** | 低于胶印 0.1 mm 安全线 → 属"看得见但读着累" |
| 放大打印可行吗 | LANCZOS 2x/3x 后平均梯度 6.9 / 4.7（细节由插值补，**不加信息**） | 真要打印须**重绘 2x（1500×2200）出新规格档**，不能靠放大 |

**结论**：报告图定位为**屏幕分享物**（当前设计目标），屏幕清晰度达标；
**打印不在当前范围**——若甲方将来要打印，需新增 2x 出图档位（新规格 + 新文件名，见 §16.3 第 2 条破缓存）。

---

*规范制定：外部专家*  
*日期：2026-08-25（运营增强同步至 2026-08-28；媒体预览统一与后端出图规范同步至 2026-09-15；图片体积规范同步至 2026-09-17；媒体纪律机械门禁同步至 2026-09-20）*
*版本：V1.5*  
*关联文档：theme-paint.ts, Layout.tsx, BookManage.tsx, BookDetail.tsx, Dashboard.tsx, PreviewImage.tsx, reading_circle/art.py, growth/report_service.py, miniapp/app.wxss*


---

## 十七、页面大标题统一（`PageTitle`，2026-09-21 用户裁定后全站铺开）

**用户原话**：「最上面的大标题改个字体增加个底色框……其他很多页面都留了太多白，都统一成会员管理这种留白。」
第一版做成深底霓虹绿（`tone="neon"`），用户反馈「颜色风格好像不太搭」→ 定稿走**暖色绘本版**。

| 项 | 口径 |
|---|---|
| 组件 | `admin-web/src/components/PageTitle.tsx`（**一处改、全站一致**；两版配色都在 CSS 里，`tone` 一个 prop 切换） |
| 字体 | 等宽栈（`ui-monospace / SF Mono / Menlo / Consolas`）+ 字距 3px——与正文/卡片标题区分开，"科技感"由**字体与字距**提供，不靠冷色 |
| 配色 | 纸感底（`--paint-paper → --paint-paper-dim` 渐变）+ 深墨 3px 描边 + 硬阴影（`--shadow-hard-sm`）+ 主色橙左条 + 黄点收口；**全部走令牌**，与导航/Tabs/按钮同族 |
| 留白口径 | 标题 `margin: 0`——AntD `Typography.Title` 默认 `margin-top: 1.2em`（≈26px）是"顶上一大片白"的真凶；改后实测"内容区顶边→标题"= **24px**（会员管理参照 27px） |
| 已铺开页面（**9 页，实测无残留 level-4 标题**） | 仪表盘（今日概览）/ 图书管理 / 图书详情 / 会员管理 / 押金与赔偿 / 借阅操作台 / 员工管理 / 系统配置 / 审计日志（带右侧按钮的表头行同步把 `alignItems: baseline → center` 与标题框居中对齐） |
| **未铺开（本身没有大标题，等用户定）** | `线下活动`/`成长与测验`/`预约管理`/`退款中心`/`通知中心`/`任务看板` 顶部直接是搜索行或按钮行、**原本就没有页面大标题**；`阅读圈`用的是 `Card title="阅读圈"`。给这些页面**新加大标题属于新增设计**，未擅自做——用户若要求统一，再补（组件已就绪，每页一行） |

| **使用边界（用户实测后定）** | **只给"页面名"用**（2–6 字固定标签）；**书名/人名等动态内容不许用**——长度不可控，等宽字距会把标题撑成大黑框（图书详情页试过，用户当场否掉"太大了，也很丑"），那类页面用普通 `Typography.Title` + `margin: 0` |

> 复现核对：浏览器里量 `document.querySelector(".page-title-chip")` 与 `.ant-layout-content` 的 `top` 差值，应 ≈24px；页面应无残留的 `Typography.Title level={4}`。


### 17.1 字体分工（2026-09-21 用户反馈后定）

用户原话：「字体可以用刚才那个，因为原来的字体看英文很难受，歪歪扭扭的。」

| 内容 | 字体 | 说明 |
|---|---|---|
| 中文标题 / 卡片标题 / 区块标题 | `--font-display`（ZCOOL KuaiLe） | 绘本风主字体，排中文最合适 |
| **英文 / 编号 / 数码类内容**（书名、动态英文标题、等宽标签） | **`--font-mono`**（`ui-monospace / SF Mono / Menlo / Consolas`） | ZCOOL KuaiLe 的拉丁字形偏"歪"，排英文可读性差；图书详情页书名已按此改（实测 `ui-monospace` 20px） |
| 正文 / 表格 / 表单 | `--font-body`（Nunito + 系统栈） | 既有口径不变 |

> **待用户定（已登记，未擅自改）**：`Layout.tsx`（侧边栏品牌 DmkWords）与 `Login.tsx`（登录页标题）里的英文品牌字目前仍是 `--font-display`——
> 那是**品牌标识**，换成等宽会失去手写绘本感，故保留原样；用户若觉得刺眼，改 `--font-mono` 即可。
> `Dashboard.tsx` 的统计大数字**已改等宽**（2026-09-21 用户第二轮答复：「侧边栏/登录页的 DmkWords 可以不动，但仪表盘上的数字确实过于丑陋了，也换一下吧」）——3 处 24px 数字实测 `ui-monospace`。


## 十八、仪表盘图形区（2026-09-21，投屏可用）

**需求**：用户要求仪表盘「不仅仅是数字，还有很酷炫的图，以后要投到店外电视机上」，
并裁定「就用仪表盘页面，不要再新增页」「至少得 6 个（图）」。

| 项 | 口径 |
|---|---|
| 位置 | `/`（今日概览）**待办卡之前**——第一眼是图；运营数字卡片与待办按用户裁定保留在下方 |
| 布局 | **三列等宽网格**（`.chart-grid`，`repeat(3, minmax(0,1fr))`；≤1100px 两列、≤760px 一列），六张卡两行对齐、等高（`.chart-card .ant-card-body { min-height: 212px }`） |
| 六张图 | ① 馆藏构成（甜甜圈 + 中心总数 + 带数值图例）② 今日流量（双柱 + 逾期）③ 健康度（续借率/测验通过率/退会率三环）④ 近 14 天借还趋势（面积 + 虚线）⑤ 热门书 TOP5（横向条形 + 书名）⑥ 小读者构成（按会员状态堆叠横条 + 图例） |
| 技术 | **零新依赖**：手写 SVG（圆环 = `stroke-dasharray`；面积 = `path` + `linearGradient`；条形 = 宽度百分比）+ CSS 动画 + paint 令牌 |
| 刷新 | 图形区自己 60 秒轮询 `/dashboard/charts`；3 张基于 overview 的图随父组件刷新 |
| **数字诚实（红线）** | 0 值**不画弧、不画柱身**——圆头线帽在长度为 0 时仍会留一个色点、柱图最小高度会留一小截色块，都会被读成"有一点值"，属界面假象（本轮自查发现并修） |
| 断网策略 | 拉取失败**保留上一帧**、不弹错误框（店里电视无人交互，弹窗会一直挂在屏上） |


## 十九、报告类图片展示规范（小程序，2026-09-21 用户报障后立规）

**报障原文**：「小程序端的评估报告和评语展示太丑了，线下的外教提供的评估报告一般都是竖版的图片……现在看也看不清，页面也很简陋。」

**什么是"报告类图片"**：竖版（高 > 宽）、以"整页文档"承载信息的图片——外教上传的观察期评估报告图、周报/月报生成图（750×1100，见 §十四）、收款凭证等。
→ 判断依据是**形态**（竖版整页文档），不是业务归属：以后凡是这类图，一律按本节处理。

**量化根因（为什么"看不清"）**：外教进度报告实测比例 **1:1.40**（样例 814×1143）；
旧实现把报告图塞进 `200rpx × 200rpx + mode="aspectFill"` 方框 = 屏上 100pt 宽 —— 报告里 24px 的字被压到约 **3pt**（且方形裁切把页面上下两端直接砍掉，页首"学生/教师/课程"与页尾"COMMENTS"落不进框）。

| 项 | 口径 |
|---|---|
| **整页展示（红线）** | 主展示位**必须整页可见**：`<image mode="widthFix">` + 满卡宽（本页卡宽 702rpx = 351pt）。**禁止**方形/近方形 `aspectFill` 缩略图当主展示 |
| **可读性下限** | 整页展示后正文小字换算 ≥ **9pt**：814px 宽报告 × 24px 字，满卡宽（351pt）= 10.3pt ✓；旧方图（100pt）= 2.9pt ✗ |
| **放大出口（红线）** | 每张整页图都要有"点开放大"，且必须走**原生 `wx.previewImage({urls, current})`**——自带双指缩放 / 左右翻页 / 长按保存。**禁止自造 overlay**：自造壳只能 `aspectFit`，放不大、翻不了页、存不下来——这是"看不清"的第二个原因（旧页 `.x-87d128` 全屏壳即此坑） |
| 多页 | 一期内多张 = 多张整页竖排 + 每页右上角「1/3」页码角标（`--sun` 底 + 墨线圆片），家长先知道"有几页" |
| 文档卡骨架 | 「**期次头 → 老师评语 → 整页图 → 操作提示**」四段；卡体 = 3rpx 墨线 + 手绘圆角 + `--shadow-hard-sm`（纸页感）；图区满卡宽、不加内边距（给足像素） |
| 评语形态 | `remark` 以「**老师评语**」呈现：引号贴纸 + 虚线行底，`--font-body` 30rpx / 行高 1.9；**排在整页图之上**——能直接读的文字先给家长，需要放大的图在后 |
| 期次口径 | 「第 N 期」= 按该孩子报告**上传先后**编号（最早 = 第 1 期），最新一期额外贴「最新」；**期次必须与上传日期同时出现**，避免被误读成机构的"课期" |
| 多期组织 | **最新一期展开 + 往期折叠**（默认收起）；仅 1 期时不出现「往期报告」区 |
| 缩略图（仅列表/往期用） | 允许 `aspectFill`，但比例必须**竖版 ≥3:4**（本页 180×240rpx），**禁止 1:1**；点缩略图同样走原生预览 |
| 图片尺寸口径 | 周报/月报生成图固定 750×1100（§十四）；**外教上传图不改比例、只等比压到 `doc` 策略 max_edge 1600**——小程序**不得假设固定比例**，一切按 `widthFix` 自适应 |
| 本批不做（登记，未采纳） | 把报告**结构化**（Reading/Writing/Listening/Speaking/Attendance/Assignments 六项成绩 + 教师 / 课程 / 年份字段）后由小程序原生排版：需管理端新增录入表单 + DB 列 + 与"图"双轨的出图口径；用户本轮只提"展示"，故**只改展示层**。若甲方要"原生成绩单"，按新任务包走 |

> 落地页：`miniapp/pages/member-pkg/observation-report/*`（外教报告 + 评语）、`miniapp/pages/member-pkg/report/*`（周报月报整页图补原生预览出口）。


## 二十、小程序视觉自查通道（微信开发者工具，2026-09-21 实测可用）

**需求来源**：用户要求"充分利用你的视觉能力，视觉问题不再由我来提"（组件间距 / 遮挡 / 毛坯房页面 / 该有的统计与交互）。小程序端必须有一条**模型自己能看图**的通道。

**唯一通道 = 微信开发者工具的 skill-cli**（`/usr/local/bin/wechatide`，v0.3.11）。**旧写法已失效**：`scripts/wechat_ide_mcp.py` 的 stdio JSON-RPC（`wechatide mcp`）在新版直连时报 `BrokenPipeError`——新版是 **CLI 直呼**，脚本已按新形状重写为薄封装（`python scripts/wechat_ide_mcp.py <tool> '<json>'` 仍然可用）。

| 动作 | 命令（`-c zcode` 是客户端名） |
|---|---|
| 登录态/授权检查 | `wechatide -c zcode check_wechatide_status` |
| 打开页面（含参数） | `wechatide -c zcode simulator_open_page --project <miniapp 绝对路径> --page pages/xx/xx --query "child_id=2&child_name=..."` |
| 截图（模型看图） | `wechatide -c zcode simulator_screenshot --project <...> --path /tmp/x.jpg --wait 3` |
| 滚动到某位置 | `wechatide -c zcode automation_viewport_action --project <...> --action pageScrollTo --scroll-top 1200 --wait 2` |
| 跑 JS / 取数 / 量尺寸 | `wechatide -c zcode automation_evaluate --project <...> --fn-source "$(cat /tmp/x.js)"` |
| 改完 wxml/wxss 后 | `wechatide -c zcode simulator_refresh --project <...>`（防编译缓存假象） |

**四条实战纪律**：
1. **授权要用户点**：首次连接 IDE 弹「MCP 客户端授权（zcode 申请使用 MCP）」——**模型点不了**（`osascript` 会被系统拒绝辅助访问 -25211）。让用户点「允许」即可，之后本机长期有效；别再花时间造 HTML 近似渲染装置（2026-09-21 试过，弯路）。
2. **量尺寸比目测准**：`automation_evaluate` 里用 `wx.createSelectorQuery().selectAll('.cls').boundingClientRect()` 拿真实 pt 值（例：整页报告图实测 402×563pt / 卡宽 402pt），把"字号够不够、有没有被裁"变成数字写进证据。
3. **登录态可切**：换家长不必点 UI——`automation_evaluate` 里直接 `wx.request` 打 `/api/miniapp/login`（{phone, code:'1234'}），再把 `token/parent/children/currentChildId` 写进 storage 即可（`utils/session.js` 的键名即口径）。
4. **`simulator_refresh` 会回到首页**：刷新后要重新 `simulator_open_page`，别以为页面自己回来了（2026-09-21 踩过）。


## 二十一、成就类页面呈现规范（生词本 / 护照 / 勋章族，2026-09-21 立，用户裁定后）

**用户原话（生词本）**：「我完全分不清哪个是我查过的词，**颜色和书名的颜色一样**，也没有分行显示，也没有字体大一点，
按理来说生词本是**孩子的成就感满满的体现**，肯定要有**统计的次数**，以及点击单词会**弹出闪卡**解释单词的释义。」

| 项 | 口径 |
|---|---|
| 页面定位 | 成就类页面 = **给孩子看的"我的战利品"**，不是数据表格。第一屏先回答"我攒了多少"，再给明细 |
| 大数字 | 主指标用**贴纸式大数字**（`--font-display` ≥40rpx 或等宽大号）+ 量词小字（"词/本/天"）；副指标 ≥3 个时用**等高三列**（数字 32–34rpx + 22rpx 标签），不许大小不一 |
| 统计必须有"新增"维度 | 只有累计数没有新鲜感 → **至少一个时间维度指标**（本周新增 N / 本期新增 N），由 `created_at` 现算，不加新表 |
| **行内信息分层（红线）** | 一条记录至少三层：**主体**（词/书名，最大最重）→ **属性**（音标/释义，正文档）→ **出处**（来源 + 日期，`--muted` 22rpx）。**禁止主体与出处同色同字号**——用户"分不清哪个是我查过的词、颜色和书名一样"说的就是这个 |
| 视觉锚点 | 有来源的条目给**出处书签小片**（书名 chip：`--sky-soft` 底 + `--sky-ink` 字 + 8rpx 圆角）——"这个词是哪本书里的"一眼可答。**列表里不放封面缩略图**：用户裁定「图片多了到时候会卡顿」（长列表 × 网络图 = 白屏/掉帧，收益不抵成本）；要图也只放在**单张详情**（闪卡/详情页） |
| 主色只给主体 | `--accent` 只用于主体词与关键数字；出处/辅助信息一律 `--muted`，避免"到处都是橙色"= 没有重点 |
| **删除降噪（红线）** | **列表行内不放删除按钮**（每行一个删除＝视觉噪音 + 误触）；移除动作只放**长按操作表**与**闪卡内按钮**两处，列表尾部一行 22rpx 小字提示长按 |
| 详情走闪卡 | 点行 → 闪卡（大词 + 音标 + 中文 + 英文释义 + 来源书 + 封面），点遮罩关闭。**不做生词发音**——PRD §8.1 明写"第一阶段不做：生词发音、复习提醒、生词积分"，不许自作主张加 |

### 21.1 徽章/成就的两态呈现（2026-09-21 补，与生词本同批）

**问题（用户口径）**：「没达到的就是没达到」——但**达到了也必须看得见**。
首版实现只有"未达成"三个字，达成项没有正向待遇：护照行里 6 枚里程碑只靠"少一行未达成字样"区分，
等级勋章页更是**用 emoji 当图标**（🌱🌿🌳🌲🏔️👑 + 🥉🥈🥇），全站资产都没用上。

| 状态 | 呈现（两页统一） |
|---|---|
| **已达成** | 卡底金纸痕（`--warning-soft`）+ **墨线实边** + 徽章资产**全彩** + 名称墨色加粗 + 金色「已达成」贴片（`--sun` 底 + 墨字） |
| **未达成** | 白底 + **虚线**边（`--border`）+ 徽章资产 `opacity: .4` + 名称 `--muted` + 淡字「未达成」 |
| 资产 | 六枚里程碑 = `miniapp/icons/badges/milestone_m1.png 起的六枚（m1–m6）`（六色奖牌，一一对应 `milestone_nodes`）；等级 = `level_template.png` 盾牌 + 字母叠加（两页同款）；连击 = `streak_7/30.png` |
| 数字 | 一律 `--font-mono`：手写体数字偏宽且不等宽，"102320" 在 4 等分格子里会顶到边（2026-09-21 目视） |
| **达成要有「三件套」** | 只画个亮卡等于没说清：① **状态词**（金色「已达成」/ 淡字「未达成」）；② **达成日期**（`milestone_awards[].awarded_at`，后端随 summary 一起回）；③ **进度与目标**——「里程碑 N/6 枚已解锁」+「下一枚 50万词 · 还差 39.8万词」（缺口用「万」单位，避免一排大数字） |
| **判定必须在 JS 里做（红线）** | WXML `{{ }}` **不支持方法调用**：`awarded.indexOf(item) !== -1` 静默得 `undefined`、条件**恒真** → 六枚里程碑全部显示「已达成」（老版本则是全奖杯图标）。判定放页面 JS 算好再绑定（已由 R15 机械门禁化） |

### 21.2 文案 emoji 一律清出（机械门禁 R13c，2026-09-21 补）

**发现**：R13a 只扫「图标槽位的**字面量**」，于是 **emoji 藏在 JS 数据里**（勋章页 `NODE_EMOJI` 数组、
错误组件 `icons = { error: '😔' }`）或落在**非 icon 槽位**（`section-title` 里的 🎧/📖）时，检查器**全瞎**——
实测全库仍有 **31 处 UI emoji 而 R13a 报 0**。

| 口径 | 说明 |
|---|---|
| **R13c（新增机械门禁）** | 扫 `miniapp/**/*.{wxml,js}` 全量文案：emoji **必须为 0**；跳过注释行；`TYPO_GLYPHS`（✓ ✕ ★ ☆ ▶ ● ◆ ※ 等排版字形）白名单放行。**范围只到小程序** |
| 口径边界（2026-09-21 补，接手会话实测后明确） | 本轮清的是**小程序端**。**管理端与后端导出仍有同族残留**（实测：`ErrorBoundary.tsx` 的 🎨、`CircleManage.tsx` 的「🌟 已赞」、`ScanCheckin.tsx` 的 ✅/⚠、`records_service.py` 借还导出 xlsx 单元格里的 ⚠）——**已登记为欠账**（`docs/09 §十二`）。口径目标定为：**用户可见文案清零**（含导出文件），而不是只清小程序 |
| 替换原则 | 图标位 → 自家 `/icons/ui/*.png` 资产（emoji 无令牌、三端渲染不一致）；纯文案 → 直接去掉；勾选/叉等**排版形态**保留字形 |
| 落地页 | 首页（继续听/今日推荐/空态/推荐卡角标）、阅读圈（馆长赞标记→crown 资产）、书籍详情、听书页、书架、购买页、我的（头像兜底）、共享组件 `error-view`（四种态→warning/globe/empty/lock 资产） |

### 21.3 文案 emoji 口径扩到管理端与导出文件（机械门禁 R13d，2026-09-23 落地）

§21.2 把口径定为「**用户可见文案清零（含导出文件）**」，但机械门禁当时**只覆盖小程序**——
管理端与后端导出的同族残留靠人眼。本节把门禁补齐（`docs/09 G6` 落地）：

| 口径 | 说明 |
|---|---|
| **R13d（新增机械门禁）** | ① `admin-web/src/**/*.{ts,tsx}`：emoji **必须为 0**（去注释后扫；`TYPO_GLYPHS` 排版字形放行）；② `backend/**/*.py`：**字符串字面量**里的 emoji 必须为 0——按 AST 取 `Constant[str]`，**docstring 跳过**（文档不是用户文案） |
| 落地清理（2026-09-23） | `ScanCheckin.tsx` 的 `⚠`→「注意」（`✕` 是白名单排版字形，保留）、成功 toast 去掉冗余 `✅`（`message.success` 自带绿勾）；`ErrorBoundary.tsx` 的 🎨→`WarningOutlined` 图标；`CircleManage.tsx` 的「🌟 已赞」→「已赞」（金色 Tag 即为标记）；`records_service.py` 导出 xlsx 单元格 `⚠ 共 N 条…`→`注意：共 N 条…` |
| 替换原则（同 §21.2） | 图标位 → **组件/资产**（管理端用 AntD 图标、小程序用 `/icons/ui/*.png`）；纯文案 → 直接去掉或换文字；勾选/叉等排版形态保留字形 |
| 红线（沿用 §二 前端宪法） | 金额/规则文案仍一律后端配置下发；本节的 emoji 检查只管**符号形态**，不改文案来源 |
