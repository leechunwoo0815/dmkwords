# Admin-Web 视觉 redesign 审查意见

> ⚠️ **已作废**：本意见被「绘本视觉系统规范」（docs/15）取代并全面实施（方向从「暖纸书房改良」改为「绘本风重塑」，含 theme.ts 删除）；仅作决策过程留痕。当前视觉事实源 = `docs/15-AdminWeb-MiniApp-绘本视觉系统规范-20260825.md`（V1.2）。

> 审查范围：admin-web/src/ 全局风格（以图书管理页为典型样本）  
> 审查结论：当前后台是「标准 antd 企业模板 + 暖纸书房 color token」的半成品，设计概念（theme.ts）与实现严重脱节。  
> 严重度：P2（体验债，不阻塞功能，但直接影响品牌认知与馆员日常使用愉悦度）  

---

## 执行摘要

`theme.ts` 的「暖纸书房」概念本身不坏，但**三个核心决策把方向带偏了**：

1. **衬线标题字体（Georgia + Songti SC）** → 旧报纸/政府 OA 感，与「少儿英语分级阅读」品牌完全冲突；
2. **牛津蓝 `#2c4a6e` 作主色** → 沉稳到沉闷，蜜杏橙 `#e8945a` 被完全雪藏；
3. **零插画、零装饰、零品牌锚点** → 概念写得再好，页面上看不到任何「绘本」「书房」「儿童」元素。

结果：截图看起来就是一个换了米白底的通用 SaaS 后台，馆员每天面对的界面和面对钉钉/飞书后台没有本质区别。

---

## 一、逐层问题诊断（截图 + 源码证据）

### 1.1 Sidebar 品牌区（Layout.tsx:46-68）

| 问题 | 证据 | 影响 |
|---|---|---|
| 纯文字 logo，零图标/插画 | `DmkWords<br/>少儿英语分级阅读` 两段文字，无 SVG/图片 | 没有品牌记忆点，像临时 demo |
| 菜单图标是 antd 默认线性图标 | `BookOutlined`, `TeamOutlined` 等，1.5px 细线 | 冷淡、无温度，像企业 OA |
| sidebar 背景 `#fffefa` + 边框 `#e4dcc8` | theme.ts `paper.surface` / `paper.border` | 米黄+灰黄 = 旧档案室，不是"暖纸书房" |
| 选中态只有文字变色 | `itemSelectedBg: brand.primarySoft` (#e6edf5) | 浅蓝灰高亮和"暖纸"概念冲突 |
| 宽度 208px 偏窄 | `width={208}` | 图标+文字拥挤，现代 SaaS 常用 240-260px |

### 1.2 页面标题与操作区（BookManage.tsx:120-136）

| 问题 | 证据 | 影响 |
|---|---|---|
| 标题用 Georgia + Songti SC | `fontFamily: "Georgia, 'Songti SC', serif"` | 衬线体在屏幕上显老，宋体自带"严肃出版物"感 |
| 副标题是灰色小号字 | `Typography.Paragraph type="secondary"` | 信息层级弱，像免责声明 |
| 主操作按钮是 flat 蓝色 | `Button type="primary"` → antd 默认主色 | 没有"实体按压感"，theme.ts 的 `hardShadow` 未生效 |
| 筛选器是标准 antd Tabs | `Tabs items=[...]` | 占一整行，视觉噪音大，像 CRM 后台 |

### 1.3 数据表格（BookManage.tsx:180-245）

| 问题 | 证据 | 影响 |
|---|---|---|
| 封面列只有文字标签 | `<Tag>未传</Tag>` / `<Tag color="green">已传</Tag>` | 馆员无法一眼看到书长什么样，完全违背"图书馆"直觉 |
| 状态标签用 antd 默认语义色 | `color="green"` / `color="orange"` | 绿色=成功、红色=错误，但这是图书状态不是系统状态，语义混乱 |
| 操作列是文字链接 | `编辑` / `下架` / `删除` 三个文字按钮 | 占 160px 宽，视觉噪音大，且"下架"和"删除"都是红色系，易误触 |
| 表格白底灰线，无定制 | `size="middle"`，标准 antd Table | 数据密度高但无层次，像 Excel 网页版 |
| 行 hover 是冷蓝灰 | theme.ts `rowHoverBg: '#dde7f2'` | 和"暖纸"底色冲突 |

### 1.4 全局 Token 执行问题（theme.ts）

| Token | 设计意图 | 实际效果 | 差距 |
|---|---|---|---|
| `hardShadow: '3px 3px 0 rgba(38,36,25,0.12)'` | 实体按压感 | 按钮看起来仍是 flat | antd Button `primaryShadow` token 可能未正确应用，或被其他样式覆盖 |
| `brand.secondary: '#e8945a'` 蜜杏橙 | 绘本活力，仅装饰 | **全局零使用** | 这是唯一能带来"活力"的颜色，被完全浪费 |
| `fontDisplay: Georgia + Songti` | 去 AI 味，衬线标题 | 旧报纸感 | 方向正确但字体选择错了——应选现代圆体或更活泼的衬线体 |
| `borderRadius: 10` | 编辑感收紧 | 组件仍是标准圆角 | 10px 和 antd 默认 6px 差异太小，感知不明显 |

---

## 二、Redesign 方向建议（6 个维度）

### 2.1 色彩系统重建

**当前问题**：主色太沉，辅色雪藏，整体偏冷。

**建议方案**：

| 角色 | 当前值 | 建议值 | 理由 |
|---|---|---|---|
| 品牌主色 | `#2c4a6e` 牛津蓝 | `#3B6A9E`（提亮）或 `#4A90D9`（天空蓝） | 保留"书卷气"但增加明亮度，避免沉闷 |
| 活力强调色 | `#e8945a` 蜜杏橙（未使用） | `#F97316` 活力橙 或保留 `#e8945a` 但**强制使用** | 用于：选中态、徽章、图标、按钮 hover、装饰块 |
| 成功/上架 | `#35703c` 叶绿 | `#22C55E` 嫩绿 | 更鲜明，像"新芽"而非"老叶" |
| 警告/待处理 | `#8d6610` 深芥末 | `#F59E0B` 琥珀黄 | 更温暖，像"书签"色 |
| 背景 | `#f6f2e9` 暖纸米白 | 保留但配合更亮的 surface | 概念ok，但需要更干净的对比 |
| 文字 | `#262419` 暖炭墨 | 保留 | 这个没问题，比纯黑柔和 |

**关键动作**：把蜜杏橙 `#e8945a` 用进 sidebar 激活指示器、页面标题装饰块、主按钮 hover、空状态插画点缀。

### 2.2 字体系统重建

**当前问题**：Georgia + Songti SC = 旧报纸。

**建议方案**：

| 用途 | 当前 | 建议 | 代码位置 |
|---|---|---|---|
| 标题/显示 | `Georgia, 'Songti SC', serif` | `Nunito, 'PingFang SC', 'Microsoft YaHei', sans-serif` | theme.ts:42, Layout.tsx:50, BookManage.tsx:123, Dashboard.tsx:52 |
| 正文/数据 | system sans | 保留 `fontBody` | theme.ts:44 |
| 数字/统计 | system sans | `Nunito` 或 `Inter`（等宽感） | Dashboard 卡片大数字 |

**为什么不继续用衬线**：
- 衬线体在屏幕上低分辨率渲染时发虚，对长时间操作的馆员不友好；
- 宋体（Songti SC）在中文语境下=严肃/古典/政府，与"少儿"品牌冲突；
- 若坚持"书卷气"，应选现代衬线如 `Merriweather`（更粗、更圆润），而非 Georgia。

### 2.3 Sidebar 品牌锚点

**建议改动**：

1. **Logo 区加图标**：在 "DmkWords" 左侧放一个 24px 的 SVG 图标——可以是📚书本、🌟星星、或一个简笔画小熊。不要只是文字。
2. **菜单图标彩色化**：当前选中项的图标改为品牌主色（或蜜杏橙），未选中用 `#6f685a` 暖灰。
3. **选中态加左边框**：`activeBarHeight: 3` 不够明显。改为 4px 圆角竖条（`border-radius: 0 4px 4px 0`），颜色用蜜杏橙 `#e8945a`，不是浅蓝灰。
4. **宽度加宽**：208px → 240px，图标和文字间距加大（`gap: 12px`）。
5. **底部加装饰**：sidebar 底部可以有一行小字"让阅读成为快乐"或一个小插画尾巴，增加温度。

### 2.4 表格 Redesign

**建议改动**：

1. **封面列放缩略图**：
   ```tsx
   // 当前
   render: (v) => (v ? <Tag color="green">已传</Tag> : <Tag>未传</Tag>)
   // 建议
   render: (v, r) => (
     <img src={r.cover_thumbnail || '/placeholder-book.png'} 
          style={{ width: 40, height: 56, borderRadius: 4, objectFit: 'cover' }} />
   )
   ```
   如果后端还没返回 `cover_thumbnail`，至少放一个有品牌色的占位图标（一本打开的书的线框图）。

2. **状态标签自定义**：
   封装 `<BookStatusTag status={s} />`，不用 antd 默认 Tag：
   - 上架：`bg: #dcfce7, color: #166534, border-radius: 999px, padding: 2px 10px`
   - 下架：`bg: #f3f4f6, color: #6b7280, border-radius: 999px`
   - 待配置：`bg: #fef3c7, color: #92400e, border-radius: 999px`
   - 已传封面/音频：不用标签，直接在缩略图/图标上打一个小绿点。

3. **操作列图标化**：
   - 编辑 → 铅笔图标（`EditOutlined`）
   - 下架/上架 → 眼睛开/关图标（`EyeOutlined` / `EyeInvisibleOutlined`）
   - 删除 → 垃圾桶图标（`DeleteOutlined`），hover 变红
   - 三个图标横向排列，间距 8px，整体宽度从 160px 缩到 80px

4. **行 hover**：从冷蓝灰 `#dde7f2` → 暖奶油 `#faf6ed`，和全局底色一致。

5. **选中行指示**：左边 3px 竖线 + 浅橙背景 `#fff7ed`，不是默认浅蓝。

### 2.5 Header & 操作区

**建议改动**：

1. **页面标题加装饰**：标题左侧放一个 8x28px 的圆角矩形色块（蜜杏橙 `#e8945a`），像"书签条"。
2. **主按钮 redesign**：`新书入库` 用主色渐变（`linear-gradient(135deg, #3B6A9E, #4A90D9)`）+ 白色文字 + 轻微阴影，不是 flat 蓝。
3. **筛选器从 Tabs → Segmented**：
   ```tsx
   // 当前
   <Tabs items={[...]} />
   // 建议
   <Segmented options={['全部','上架中','已下架',...]} />
   ```
   Segmented 更紧凑，pill 形状更像现代 SaaS 的筛选器。theme.ts 里已经配了 Segmented token，但代码里没用过。
4. **搜索框**：加大圆角到 `border-radius: 12px`，placeholder 更友好："搜索书名、作者或 ISBN..."

### 2.6 插画与空状态

**当前问题**：空表格时显示 antd 默认 "暂无数据"，加载用 spinner。

**建议**：

1. **空状态插画**：找一个免费插画库（见 §四）搜 "empty library" 或 "no books"，放一个 120px 的 SVG 插画 + 文案：
   > "书架还空着呢 📚\n点击右上角「新书入库」添加第一本书吧！"
2. **加载状态**：用骨架屏（`Skeleton`）而不是 spinner，骨架屏的 shimmer 颜色用主色浅底。
3. **Dashboard 数据卡片**：当前是标准 Card + 线性图标。建议：
   - 每个卡片左上角放一个小彩色圆角块（不同业务用不同色：会员=蓝、借阅=绿、逾期=橙）
   - 大数字用 `font-weight: 700, font-size: 32px`
   - 卡片加轻微阴影或边框高亮

---

## 三、代码位置映射（不改文件，只标位置）

| 改动项 | 文件 | 行号 | 备注 |
|---|---|---|---|
| 换掉全局衬线字体 | `theme.ts` | 42 | `fontDisplay` 定义 |
| 换掉标题字体 | `Layout.tsx` | 50 | sidebar logo 区 |
| 换掉标题字体 | `BookManage.tsx` | 123 | 页面标题 |
| 换掉标题字体 | `Dashboard.tsx` | 52 | 页面标题 |
| 检查 Button shadow 生效 | `theme.ts` | 91-93 | `primaryShadow` / `defaultShadow` |
| 封面列加缩略图 | `BookManage.tsx` | 203-206 | `columns` 中 cover_path render |
| 状态标签自定义 | `BookManage.tsx` | 200-215 | AR / 封面 / 音频 / 状态 四列 |
| 操作列图标化 | `BookManage.tsx` | 217-243 | 编辑/下架/删除三按钮 |
| Tabs → Segmented | `BookManage.tsx` | 164-179 | 筛选器 |
| 行 hover 颜色 | `theme.ts` | 101 | `rowHoverBg` |
| 选中行指示 | `BookManage.tsx` | 187-191 | `rowSelection` 样式 |
| sidebar 宽度 | `Layout.tsx` | 46 | `width={208}` |
| sidebar 激活态 | `theme.ts` | 140-144 | `Menu` token |
| 会员状态标签色 | `MemberManage.tsx` | 51-54 | `MEMBER_COLOR` 映射 |
| 会员状态标签色 | `CirculationDesk.tsx` | 32-35 | `MEMBER_COLOR` 映射 |
| 退款状态标签色 | `RefundCenter.tsx` | 33-38 | `STATUS_COLOR` 映射 |

---

## 四、实施建议（分阶段，避免改崩）

**Phase 1：Token 层（1-2 天，风险最低）**
- 改 `theme.ts`：字体、主色提亮、rowHoverBg、Menu activeBar、Tag defaultBg
- 所有页面**零改动**，全局自动生效
- 跑 gate.sh 确保 tsc 0 错

**Phase 2：Sidebar + Header（1 天）**
- Layout.tsx：logo 区加 SVG 图标、sidebar 加宽、菜单图标彩色化
- 各页面标题区：加左侧装饰色块
- 验证所有页面无布局错位

**Phase 3：表格组件封装（2-3 天）**
- 新建 `components/StatusTag.tsx`、`components/ActionButtons.tsx`、`components/CoverThumbnail.tsx`
- 逐个页面替换：BookManage → MemberManage → CirculationDesk → RefundCenter
- 每替换一个页面跑一次 gate.sh

**Phase 4：插画与空状态（1 天）**
- 引入插画 SVG（内联，不依赖外部 CDN）
- 空状态、加载态、Dashboard 卡片美化
- 最终全量 gate.sh 验收

---

## 五、外部资源推荐

### 5.1 OpenDesign（用户提到的工具）

已确认可用：开源本地优先 AI 设计工作台，支持接入 Claude Code，内置 31 套设计技能 + 72 套品牌规范。

**建议用法**：
1. 在 OpenDesign 里选 `dashboard` skill + `Soft Minimal` 或 `Neutral Modern` 设计系统；
2. 输入需求："少儿英语分级阅读图书馆后台管理界面，温暖绘本风格，主色珊瑚橙+天空蓝，sidebar 导航，图书管理表格带封面缩略图"；
3. 让它生成高保真原型（HTML），导出后作为前端实现的视觉参考；
4. **不要直接用它生成的代码**——代码质量不可控，只拿它当"设计稿"。

### 5.2 免费插画库

| 资源 | 风格 | 用途 |
|---|---|---|
| **unDraw** | 扁平矢量 | 空状态、Dashboard 装饰 |
| **Storyset** | 手绘/故事感 | 空状态、页头插画（搜 "library", "reading", "books"）|
| **Humaaans** | 人物插画 | 会员管理页装饰 |
| **IRA Design** | 渐变矢量 | 品牌页头、Hero 区 |

### 5.3 图标库替换

当前用 `@ant-design/icons`（线性）。建议补充或替换为：

| 库 | 特点 | 安装 |
|---|---|---|
| `@ant-design/icons` (Filled) | 面性图标，更饱满 | 已安装，换 import `{ BookFilled }` |
| `lucide-react` | 线条更精致、圆角更友好 | `pnpm add lucide-react` |
| `@heroicons/react` | 粗细适中，现代感 | `pnpm add @heroicons/react` |

**最低成本改动**：不换库，只把当前用的线性图标改成 `Filled` 变体（如 `BookOutlined` → `BookFilled`），并给当前选中项的图标加品牌色。

### 5.4 字体加载

| 字体 | 用途 | 加载方式 |
|---|---|---|
| `Nunito` (Google Fonts) | 标题 + 数字 | `<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700&display=swap" rel="stylesheet">` |
| `PingFang SC` / `Microsoft YaHei` | 中文正文 | 系统字体，无需加载 |

---

## 六、诚实标注

1. **本次审查只读未改**，所有建议均未验证在真实浏览器中的渲染效果；
2. **配色建议基于屏幕截图推断**，实际印刷色/暖纸质感需在真实设备上校准；
3. **OpenDesign 未实际试用**，仅基于公开文档判断其能力边界；
4. **字体变更（Nunito）需评估加载性能**，若追求首屏速度可用系统字体栈替代；
5. **表格缩略图列需后端支持 `cover_thumbnail`**，若后端无此字段，建议先用 CSS 占位图（带品牌色的书本 SVG）过渡。

---

*审查人：外部专家*  
*日期：2026-08-25*  
*关联文档：theme.ts (v2 2026-08-18), Layout.tsx, BookManage.tsx, Dashboard.tsx*
