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
| Button | `borderRadius: 10` | `borderRadius: 16`（大胶囊） | theme.ts |
| Card | `borderRadius: 14` | `borderRadius: 20` | theme.ts |
| Modal | `borderRadius: 14` | `borderRadius: 24` | theme.ts |
| Table 行 | `borderRadius: 0` | 每行独立卡片 `borderRadius: 12` | 自定义 Table 组件 |
| Input | `borderRadius: 10` | `borderRadius: 12` | theme.ts |
| Tag | `borderRadius: 10` | `borderRadius: 999px`（pill） | theme.ts |
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
- 字体栈一致（miniapp 用 `ZCOOL KuaiLe` + `PingFang SC`）

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

1. **新建 `theme-paint.ts`**：基于当前 `theme.ts`，覆盖：
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

### 未实施项

- **miniapp 小程序端绘本风**：本规范同样适用，但尚未在小程序端实施，需单独安排迭代。

---

## 十二、运营增强组件（2026-08-28）

### 12.1 PaintPagination 分页组件

admin-web 列表页统一使用绘本风分页底栏，替代 antd 原生 Pagination。

**行为**：
- 显示当前页码范围、总条数、总页数
- 快速跳转输入框 + 15/30/50/70/100 每页条数预设按钮
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

*规范制定：外部专家*  
*日期：2026-08-25（运营增强同步至 2026-08-28）*  
*版本：V1.2*  
*关联文档：theme-paint.ts, Layout.tsx, BookManage.tsx, BookDetail.tsx, Dashboard.tsx, miniapp/app.wxss*
