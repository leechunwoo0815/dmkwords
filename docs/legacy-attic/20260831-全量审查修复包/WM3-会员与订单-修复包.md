# WM3 会员与订单 · 修复包

> 投喂时机：验收 WM3 时（或验收发现问题的合批修复）。
> 来源：20260831 全量深度审查。已引用 P0/P1 包的卡片不在此重复施工。
> WM3 状态：MANUAL_TEST（UX 基线修复 W1-W12 已完成，等验收）。

---

## 一、验收重点关注（验收 WM3 前必读）

### 必撞清单（测了也白测，先修后复核）

1. **收款确认按钮连点/双开窗口确认同一订单**——P1-F2 后端无锁 + HC-F1 前端无防双击，
   网络慢时双击会双记账（押金台账两笔）。验收时**不要刻意连点**，等修复后专门复核
2. **订单退款 Modal 输入长说明提交失败**——弹窗直接关闭丢掉你刚输入的退款说明（WM3-F2）

### 测不出清单（不要浪费时间构造）

- 收款/99 元资格的并发穿透（P1-F2/P1-F6）——已列入 P1 包
- keyword 搜索传 `%` 的全表模糊（LIKE 未转义，横切-架构债 HC-A6）

### 验收可顺带复核的已知闭环项（正面确认）

- 会员到期即时判定（D1：读时不等定时任务）——已闭环，测"到期当天仍有效"
- 演示异常态家长 13800007777（观察期孩/待评估孩/过期孩）——W10 seed 已就绪
- 待确认计数徽标与订单 Tab 计数一致性（W3）

---

## 二、任务卡

### WM3-F1【中】订单列表 N+1：每单两次单查 Child/Parent，page_size=100 时一次请求 201 条 SQL

- **验收可见性**：验收测不出（功能正确，仅性能）；订单页数据多时可感变慢
- **证据**：`backend/domain/identity/service.py:589-595`：

```text
for o in orders:
    child = self.db.query(Child).filter(Child.id == o.child_id).first() if o.child_id else None
    parent = self.db.query(Parent).filter(Parent.id == o.parent_id).first()
```

- **现象与根因**：后台订单页是高频页面，列表循环内逐单查库。根因：未用批量 IN 模式。
  项目内已有正确范式（`admin/todo_service.py:258-266` handled_by 批量取名）。
- **修复方案**：

```text
# list_orders 循环前批量预取（复用项目既有范式）
    child_ids = {o.child_id for o in orders if o.child_id}
    parent_ids = {o.parent_id for o in orders}
    children = {c.id: c for c in self.db.query(Child)
                .filter(Child.id.in_(child_ids), Child.is_deleted == 0).all()} if child_ids else {}
    parents = {p.id: p for p in self.db.query(Parent)
               .filter(Parent.id.in_(parent_ids), Parent.is_deleted == 0).all()}
    for o in orders:
        child = children.get(o.child_id) if o.child_id else None
        parent = parents.get(o.parent_id)
```

- **测试要求**：既有订单列表测试回归（字段不变）；可选加 SQL 计数断言
  （echo 模式断言请求数为常数）
- **验证等级**：B 级（identity 域）
- **标注**：【事实】

### WM3-F2【中】订单退款 Modal.confirm 失败即关弹窗、丢退款说明（同页其余两处写法正确）

- **验收可见性**：**验收可撞**——退款说明输到一半提交失败（如后端校验拒绝），弹窗关闭，文案全丢
- **证据**：`admin-web/src/pages/MemberManage.tsx:206-208`：

```tsx
// 失败 catch 后不 rethrow → antd 视为 resolved → 弹窗关闭
catch (e) { message.error(...); }     // ← 缺 return Promise.reject(e)
```

对照同文件 `:252`、`:279` 的正确写法（catch 内 `return Promise.reject(e)`——失败弹窗保留）。
- **现象与根因**：同页三处 Modal.confirm 只有一处漏 rethrow，行为不一致；失败时用户输入的退款说明丢失。
- **修复方案**：

```tsx
// MemberManage.tsx 退款 Modal.confirm 的 onOk 内
catch (e) {
  message.error(e instanceof Error ? e.message : "退款失败");
  return Promise.reject(e);   // ← 补这行：失败保持弹窗打开，保留用户输入
}
```

- **测试要求**：无前端单测框架；手动验证：构造失败（如对不可退订单发起退款）→ 弹窗保留、说明文案还在
- **验证等级**：A 级（tsc）
- **施工顺序**：可与横切前端包 HC-F1 的 MemberManage 防双击改动同批（同文件）
- **标注**：【事实】

### WM3-F3【低】superadmin 角色字面量硬编码：退款按钮显隐用 role 而非权限码体系

- **验收可见性**：验收测不出（当前角色模型下行为正确）；架构口径不一致
- **证据**：`admin-web/src/pages/MemberManage.tsx:465`：

```tsx
{user?.role === "superadmin" && <Button ...>退款</Button>}
```

同页 Layout.tsx:82 退款中心菜单用的恰是 `audit.view` 权限码——两处判定体系不一致。
权限码由 `/me/permissions` 下发（auth.tsx:27-31 ✓ 符合宪法），仅此处绕开。
- **修复方案**：改 `hasPermission(permissions, "refund.review")`（或与菜单一致的
  `audit.view`，以后端 require_super_admin 的实际语义为准——查 identity/router.py
  退款审核端点的权限依赖，与后端口径对齐）。
- **测试要求**：staff01 登录看不到退款按钮、admin 能看到（手动复核）
- **验证等级**：A 级（tsc）
- **标注**：【事实】

---

## 三、引用卡（不在本包施工）

| 引用 | 主题 | 主卡位置 |
|---|---|---|
| P1-F2 | 收款确认无行锁（双击双记账） | `P1-立即修复-资金并发行锁.md` |
| P1-F6 | 99 元首单资格并发穿透 | 同上 |
| HC-F1 | 收款/退款弹窗防双击（MemberManage 两处） | `横切-前端质量-修复包.md` |
| HC-A6 | LIKE 通配符未转义（orders keyword 5 处之一） | `横切-架构债-修复包.md` |
| HC-F4 | 时间格式化统一（订单 created_at 直出 ISO） | `横切-前端质量-修复包.md` |

## 四、本包施工顺序与验证

WM3-F1（后端 B 级）与 WM3-F2/F3（前端 A 级）相互独立可并行；
全部完成后合批跑一次 `bash scripts/gate.sh full`（C 级，因跨前后端）。
