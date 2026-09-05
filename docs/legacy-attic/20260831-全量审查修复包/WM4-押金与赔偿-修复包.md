# WM4 押金与赔偿 · 修复包

> 投喂时机：验收 WM4 时。来源：20260831 全量深度审查。
> WM4 状态：MANUAL_TEST（押金状态机/扣除/补缴/待结清已交付，等验收）。
> 本包不含 P0-F4（押金负数金额）与 P1-F3（押金并发双扣）——见 P0/P1 包。

---

## 一、验收重点关注（验收 WM4 前必读）

### 必撞清单

1. **押金赔偿弹窗输负数**——P0-F4 修复前：-100 会真实反向加钱（余额 200 → 300）。
   **修复后**：应弹 422"赔偿金额必须大于 0"。修复完成前不要测负数
2. **缴押金/补缴失败无提示**——WM4-F2：失败静默（无 message.error），馆员以为已创建。
   验收时若后端报错而界面无反馈，就是这张卡
3. **押金流水 Drawer 打开空白**——WM4-F2 的 openLedgers 同款无 catch + 无 loading

### 测不出清单

- 双管理员同刻扣款超扣（P1-F3）
- 押金列表同孩子多张 PAID 订单时行重复（WM4-F1 扇出——需软删押金重建再缴的特定数据才触发）

### 验收可顺带复核

- 押金状态机五态（paid/partially_deducted/fully_deducted/refunding/refunded）中文标签
- 待结清（unpaid_balance）的补缴链路

---

## 二、任务卡

### WM4-F1【中】押金列表死 join + 软删订单未过滤 + 多订单扇出（行重复/total 虚高）

- **验收可见性**：验收测不出（需同 child 两张 PAID 押金订单的特定数据）；数据积累后自然暴露
- **证据**：`backend/domain/billing/service.py:218-236`：

```text
q = (
    self.db.query(Deposit, Child, Order)
    .join(Child, Deposit.child_id == Child.id)          # Child 无 is_deleted 过滤
    .outerjoin(
        Order,
        (Order.child_id == Child.id)
        & (Order.order_type == Order.TYPE_DEPOSIT)
        & (Order.status == Order.STATUS_PAID),          # Order 无 is_deleted 过滤
    )
    .filter(Deposit.is_deleted == 0)
)
```

且 router 侧 `_order` 完全未使用（`billing/router.py:61`：`for dep, child, _order in rows`）。
- **现象与根因**：① 同一 child 存在多张 PAID 押金订单（软删旧押金后重建再缴）时 join 扇出——押金行重复出现、`total` 虚高、分页错乱；② 软删订单仍参与 join；③ 白付多表 join 成本。根因：历史遗留 join，响应早已不用 Order 字段。
- **修复方案**：直接删除 outerjoin：

```text
q = (
    self.db.query(Deposit, Child)
    .join(Child, Deposit.child_id == Child.id)
    .filter(Deposit.is_deleted == 0, Child.is_deleted == 0)
)
# router 侧解包改 for dep, child in rows
```

- **测试要求**：`tests/unit/test_wm4_deposit.py` 增用例：同 child 两张 PAID deposit 订单
  （直改库造），断言 `GET /api/admin/deposits` 该押金**仅出现一次**且 total 正确
  （**修复前重复出现，RED**）；普通列表回归。
- **验证等级**：B 级（billing 域）
- **标注**：【事实】（join 存在且未使用亲验；扇出触发为推断场景，测试实证）

### WM4-F2【高·前端】押金页「缴押金/补缴/流水」三处 async 操作无 try/catch，失败静默无反馈

- **验收可见性**：**验收必撞（构造失败场景时）**——接口报错界面无任何提示
- **证据**：`admin-web/src/pages/DepositManage.tsx:115-124`：

```tsx
<Button type="link" size="small" onClick={async () => {
  const order = await apiCreateDepositOrder(r.child_id);   // ← 无 try/catch
  message.success(...);
}}>缴押金</Button>
```

同页 `:70-73` `openLedgers` 的 `setLedgers(await apiGetDepositLedgers(...))` 同样裸奔，且 Drawer 无 loading 态。
- **现象与根因**：失败 = unhandled promise rejection，馆员看不到任何反馈，以为已创建押金订单（资金相关操作！）；流水 Drawer 失败永远空白。根因：未按项目统一模式包 try/catch。
- **修复方案**：

```tsx
// 缴押金/补缴按钮 onClick
onClick={async () => {
  try {
    const order = await apiCreateDepositOrder(r.child_id);
    message.success(`押金订单已创建（${order.order_no}），请线下收款后确认`);
    await refresh();
  } catch (e) {
    message.error(e instanceof Error ? e.message : "创建失败");
  }
}}

// openLedgers 加 try/catch + ledgerLoading state + Drawer 加 loading/spinner
```

- **测试要求**：无前端单测；手动 mock 接口 500（DevTools 断网）→ 点缴押金/打开流水，
  断言出现错误 toast / Drawer 显示错误态而非空白
- **验证等级**：A 级（tsc）
- **施工顺序**：与 HC-F1 的 DepositManage 赔偿弹窗防双击同批（同文件改动一次到位）
- **标注**：【事实】

### WM4-F3【低】押金台账 balance_after 已有（正面确认）+ 金额显示统一（引用）

- **说明**：审查确认 DepositLedger 有 `balance_after` 字段（`billing/models.py:41`）——
  押金流水的对账基础已在，此为项目优点（可部分弥补审计 detail 缺前值的问题）。
  金额显示统一（49.90 显示 49.9 的问题）在 `横切-前端质量-修复包.md` HC-F5 统一处理
  （formatMoney 工具），本包不重复。
- **标注**：【事实】

---

## 三、引用卡

| 引用 | 主题 | 主卡位置 |
|---|---|---|
| P0-F4 | 赔偿金额负数反向加钱（验收前必修） | `P0-立即修复-安全漏洞五项.md` |
| P1-F3 | 押金并发双扣（超扣防护失效） | `P1-立即修复-资金并发行锁.md` |
| HC-F1 | 赔偿登记弹窗防双击（DepositManage） | `横切-前端质量-修复包.md` |
| HC-A6 | LIKE 通配符未转义（deposits keyword 5 处之一） | `横切-架构债-修复包.md` |

## 四、本包施工顺序与验证

P0-F4/P1-F3 先行（已在 P0/P1 包）；本包 WM4-F1（后端 B 级）与 WM4-F2（前端 A 级）
独立并行；完成后合批 C 级 gate 一次。
