# WM7-WM8 测验成长榜单 · 修复包

> 投喂时机：验收 WM7/WM8 时。来源：20260831 全量深度审查。
> 两模块问题量少（各 2-3 项低级 + 1 中级），合并为"成长线"一个包。
> WM7 状态：MANUAL_TEST（Quiz/词数/积分/等级/里程碑已交付）。
> WM8 状态：MANUAL_TEST（五榜单/护照/周月报图片/生词本/收藏夹已交付）。

---

## 一、验收重点关注（验收 WM7/8 前必读）

### 必撞清单

1. **成长管理页会员状态列显示英文原始值（WM78-F1）**——列表直接显示
   `formal`/`observation` 等英文，对照会员管理页有中文标签映射（已知事故模式复发）

### 测不出清单

- 进步榜/成长档案的 N+1 与全表加载（性能类，数据量大才暴露）
- 双设备同刻提交 Quiz 的并发（审查确认 quiz submit 持 Child 行锁——**安全**，无需测）

### 验收可顺带复核（审查确认的正面项）

- Quiz 终身 3 次/未提交不占次数/历史最高分（有测试覆盖）
- 有效词数唯一约束 + 入账永不回收（growth/models.py:27 唯一索引在）
- 排行榜隐私：英文名/昵称+头像，无真实姓名手机号（board_service.py:30-31 亲验）
- quiz submit 持 Child 行锁串行化入账（growth/service.py:539——并发模板正确示范）

---

## 二、任务卡

### WM78-F1【低】成长管理页会员状态列显示英文原始值（已知事故模式④复发）

- **验收可见性**：**验收必撞**——打开成长管理页孩子列表即可见 formal/observation 英文
- **证据**：`admin-web/src/pages/GrowthManage.tsx:178`：

```tsx
{ title: "会员状态", dataIndex: "member_status", width: 100 }   // 无 render 无映射
```

对照正确实现：`MemberManage.tsx:53-56` 有 MEMBER_LABEL 映射。
- **现象与根因**：E-20260828-07（antd Select 原始值泄漏）同族事故模式：状态值直出。
  且 MEMBER_LABEL/MEMBER_COLOR 映射在 3 个页面各自复制了一份
  （MemberManage.tsx:53、CirculationDesk.tsx:29）——三处维护漂移。
- **修复方案**（顺带收敛重复）：

```tsx
// ① 抽到 src/constants/member.ts（新常量文件，或并入既有 constants 目录）：
export const MEMBER_LABEL: Record<string, string> = {
  formal: "正式会员", observation: "观察期", pending_evaluation: "待评估",
  withdrawn: "已退会", none: "未入会",
};
// ② GrowthManage.tsx:178 改：
{ title: "会员状态", dataIndex: "member_status", width: 100,
  render: (s: string) => <Tag>{MEMBER_LABEL[s] ?? s}</Tag> }
// ③ MemberManage / CirculationDesk 的本地副本改 import 共享常量
```

- **测试要求**：手动复核三页会员状态显示中文
- **验证等级**：A 级（tsc）
- **标注**：【事实】

### WM78-F2【中】成长档案 quiz 状态全量加载 Book 表：仅为取书名把全馆书目拉进内存

- **验收可见性**：验收测不出（2000 本规模暂无感）；书目增长后每次档案页请求全表载入
- **证据**：`backend/domain/growth/service.py:326`：

```text
books = {b.id: b for b in self.db.query(Book).filter(Book.is_deleted == 0).all()}
```

- **现象与根因**：仅为取孩子尝试过的书目标题，而所需 book_ids 上一行 `rows` 里就有。
  对照 E-20260830-03（dictionary_words 全表拉取卡死 23 分钟）同族模式。
- **修复方案**：

```text
# 修复后（用 rows 里已有的 book_ids 精确查询）
    book_ids = {r.book_id for r in rows}
    books = {b.id: b for b in self.db.query(Book)
             .filter(Book.id.in_(book_ids), Book.is_deleted == 0).all()}
```

- **测试要求**：既有成长档案测试回归（书名仍正确显示）
- **验证等级**：B 级（growth 域）
- **标注**：【事实】

### WM78-F3【低】进步榜 N+1：_entries 已带 Child 对象又逐 cid 重查

- **证据**：`backend/domain/growth/board_service.py:102-113`——progress 榜先用 dict
  丢弃 Child 对象，再逐 cid 重查 `Child`。
- **修复方案**：直接复用 `_entries` 返回值中的 Child 对象（删掉逐 cid 查询循环）。
- **测试要求**：既有榜单测试回归（名次/名字不变）
- **验证等级**：B 级（growth 域）
- **标注**：【事实】

### WM78-F4【低】报告图片无 alt + 词数/积分流水 Drawer 无分页

- **证据**：`admin-web/src/pages/GrowthManage.tsx:288`（报告 `<Image>` 无 alt）；
  `:224,238`（词数/积分流水在 Drawer 内全量渲染无分页）。
- **修复方案**：Image 加 `alt="成长报告图片"`；流水加 antd Table pagination（客户端分页
  即可，量级有限）。
- **验证等级**：A 级（tsc）
- **标注**：【事实】

---

## 三、引用卡

| 引用 | 主题 | 主卡位置 |
|---|---|---|
| HC-F4 | 时间显示统一（GrowthManage 两处 ISO 直出） | `横切-前端质量-修复包.md` |
| HC-F3 | 报告图片加载失败态 | `横切-前端质量-修复包.md` |
| HC-F1 | 积分调整弹窗防双击（GrowthManage:297） | `横切-前端质量-修复包.md` |
| HC-T4 | wm10_withdrawal 78% 覆盖率（成长关联退会结算分支） | `横切-门禁与测试体系-修复包.md` |

## 四、本包施工顺序与验证

WM78-F1（前端 A 级）与 WM78-F2/F3（后端 B 级）独立并行；WM78-F4 随手批。
全部完成后：tsc + `python -m pytest tests/unit/test_wm7_growth.py tests/unit/test_wm8_boards.py -q`
+ 合批 C 级 gate。
