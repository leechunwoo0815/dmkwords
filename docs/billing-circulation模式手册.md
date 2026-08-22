# Billing / Circulation 模式手册

> 版本：2026-08-18 ｜ 提取人：架构师（15 年经验口径）
> 来源：旧项目 deposit/order/refund/borrow/book/child-deletion/consent 七个 service（共 4650 行）
> + 交叉验证 60+ 个单元测试断言 + 错误记忆库 8 条红线
> 定位：**独立可读**。写新项目 billing（收费/押金/退款）与 circulation（借还/库存）域时，
> 把本手册当 checklist 用——每个模式都对应旧项目一次真实翻车（带编号可溯源）。
> 旧代码本体在 `../backend/domain-deposit/` 与 `../backend/domain-reference/`（隔离区，只读不拷）。

---

## 〇、三条心法（先背这个）

1. **数据库是最后防线，不是唯一防线**。服务层先查+行锁前置拦截（给用户友好报错），
   DB 唯一索引/条件更新兜底（给并发兜住）。只有一层必挂：只有服务层=竞态漏网，
   只有 DB 层=用户看到裸 500。
2. **先落库，再调外部网关**。任何跨网络调用（微信支付/退款）都不能抱在 DB 事务里——
   事务悬挂会拖垮连接池。正确姿势见模式 P2 三段式。
3. **锁要锁对行**。锁"不存在的行"等于没锁（旧项目 L3-035：0 条活跃记录时
   `with_for_update` 锁不到任何东西，并发插入照样超借）。锁主体行（Child/User），
   而不是锁"可能为空的结果集"。

---

## 一、Billing 域模式（资金相关）

### P1 资金状态机：状态集合 + 转移矩阵 + 双状态镜像

**问题**：资金状态被并发回调/管理端操作/定时任务三方写入，无矩阵则任意跳变。

**解法**：
```
押金：UNPAID → PENDING → PAID → REFUND_PENDING → REFUNDING → REFUNDED
                    ↓(超时F78)        ↑(reject/取消)   ↓
                   UNPAID             PAID          (失败回退 REFUND_PENDING)
      PAID → DEDUCTED（扣减，终态）
订单：PENDING → PAID → REFUND_PROCESSING → REFUND_DONE / REFUND_FAILED
      PENDING → CLOSED（取消/超时）；PAID ← 迟到支付可从 CLOSED 激活
```
- 每次转移前：`with_for_update()` 重取记录 + `if record.status != 期望态: raise ConflictError`
- **非法转移必须显式拒绝**（不是静默跳过），报错文案带当前状态
- **双状态镜像**：DepositRecord.status 与 Child.deposit_status 同步更新（同一事务内），
  主体表镜像避免业务查询每次 join 财务表
- 终态（REFUNDED/DEDUCTED）拒绝一切转移，包括"看起来无害"的重复回调

**旧项目翻车**：F-006（非 PAID 申请退款）、F-005（REFUNDING 钱在途被取消）、
F-031（乱序回调把 REFUNDED 覆盖回 PAID）、F-007（退款中订单被支付回调反转）。
**测试背书**：`test_f052_state_matrix.py`、`test_deposit_service.py`。

### P2 三段式事务（防事务悬挂）——最重要

**问题**：`db.begin() → 调微信 → 更新状态 → commit`。微信慢 30s，行锁和连接全挂死。

**解法**（押金缴纳/退款审核/减半退还三处同构）：
```
Phase 1: 创建 PENDING 记录（或置 REFUNDING）→ commit 释放行锁
Phase 2: 事务外调用网关（无任何 DB 锁）—— 异常不回滚 Phase 1（见下）
Phase 3: with_for_update 重取记录：
         - 若回调已把它置成终态（PAID）→ 直接返回（回调赢了）
         - 否则按网关结果更新状态 → commit
```
Phase 2 失败的特殊处理（F-030，反直觉但正确）：**保持 PENDING，不回滚**。
因为网络超时窗口内微信可能已受理支付，置回 UNPAID 会丢回调匹配
（回调只接受 PENDING→PAID）。善后交给僵尸单清理（P4）。

**测试背书**：`test_deposit_service.py::test_pay_deposit_duplicate`。

### P3 幂等回调：三层守卫（F-084）

**问题**：微信回调会重发（重试风暴）、乱序（失败通知晚于成功通知）、重复入账。

**解法**——回调处理必须过三关：
```
① 入口层：trade_no 查重（同一流水号不得关联两笔订单）+ trade_state 消费
   （非 SUCCESS 不标记已支付）+ 金额比对（回调金额 ≠ 记录金额 → 拒绝）
② 执行层：with_for_update 重取订单 + 状态守卫
   （REFUND_DONE/REFUNDING 的订单忽略支付回调，防资金状态反转）
③ 幂等层：已 PAID/已 REFUNDED 的重复回调直接 return 200 成功
   （不抛错——抛错会触发微信更猛烈的重试）
```
退款回调同理：`mark_refunded` 里 COMPLETED 状态直接返回；
`handle_refund_failed`（CLOSED/ABNORMAL 终态）先查 REFUND_DONE 则忽略（F-031 乱序守卫）。

**旧项目翻车**：F26（trade_no 重复入账，DB 唯一索引兜底才没出事）、
F75-③（trade_state 没消费，非成功回调也置 PAID）、L2-008（重复退款回调 404 触发重试风暴）。
**测试背书**：`test_f12_refund_callback_guard.py`（含 second_callback_does_not_double_deduct_fine）、
`test_f084_duplicate_refund.py`。

### P4 僵尸单清理（F78 + F-030 善后）

**问题**：用户放弃支付 → PENDING 记录永久残留 → `get_active_by_child` 把它当活跃记录
→ 该孩子**永远无法再次缴纳**。

**解法**：
- 定时任务扫描超时 PENDING（窗口 > 微信支付单有效期，默认 150 分钟），复位 UNPAID
- 清理时**行锁 + 状态守卫重取**（F-16a/L2-044 模式）：
  先 `SELECT id` 拿候选 → 逐条 `SELECT ... WHERE status=PENDING FOR UPDATE` 重取
  → 拿不到（已被回调置 PAID）就跳过。防"查询后回调恰好到达"的竞态
- 罚款缴款单超时无"废弃"态 → 软删除（金额变化后旧单本来就不复用）
- 交互侧兜底：缴纳入口遇 PENDING 且已超时 → 先复位 UNPAID 再放行（F78）

**测试背书**：`test_p2_f58_f68.py`、`test_deposit_service.py`。

### P5 退款单号持久化复用（F38）

**问题**：每次重试生成新 `out_refund_no` → 微信当新退款 → **重复打款**。

**解法**：申请退款时就生成并落库 `out_refund_no`；执行/重试一律复用该值（微信幂等键）。
例外（F67 补打款场景）：同单第二次退款必须新单号（`RF{id}_{uuid}`），因为微信幂等键
按单号去重——同号不同金额会被拒。**两类单号分列存储**（partial_refund_no 与
out_refund_no 分字段，防互相污染）。

### P6 金额完整性五规则

1. 全程 `Decimal`，禁 float（宪法红线 1）；
2. 元→分转换用四舍五入 `yuan_to_cents`，禁 `int()` 截断（F-033：截断每笔少收 1 分）；
3. 乘法后 `quantize("0.01", ROUND_HALF_UP)`，防跨 DB 舍入不一致（F-03c/L2-010）；
4. **公式算完最后一步才取整**，中间步骤不取整（退款预览与实际退款才不会分叉）；
5. 微信退款 V3 的 `total_amount` 必须传**原支付单实付金额**，不是当前余额、不是原价
   （F37/F54：微信按支付单校验 268892183）。所以支付时必须快照 `original_amount`。

### P7 快照冻结模式（防配置漂移）

**问题**：价格/时长是 SystemConfig 可变的。下单时 365 天、回调时配置改成 300 天，
退款计算就错了。

**解法**——下单时把一切影响后续计算的值冻结进订单行：
- `duration_days`（F-050）：退款/升级计算与激活侧统一用快照，NULL 才走配置兜底
- `original_amount`（F54/A-P1-5）：重缴是新支付单，快照必须=本次实付，勿沿用旧记录
- `upgrade_deduct`（L3-017）：升级单落差额，激活 handler 据此走"重置起算"而非叠加
- `paid_member_ever`（F5）：资格类布尔快照写 user 表，数据 purge 后资格仍成立
  （写入也要行锁 F-003，防并发双订单双写）
- 涉及"剩余价值"的计算（升级差价 F16）：按**订单实付金额**折算，不按现价
  （现价含折扣会高估剩余价值 → 少收差价）

### P8 并发防重：同类 PENDING 单拦截（F22/F-067/A-P1-6）

**问题**：同孩子同类型订单可重复下单，超时关闭前堆叠；名额类（亲子课时段）并发超卖。

**解法**：
```
existing = query(Order).filter(同child同type, status=PENDING, is_deleted=0)
           .with_for_update().first()   # 行锁串行化 + 查重二合一
if existing: raise ConflictError("已有待支付订单")
```
名额防超卖的正确口径（L3-020）：占名额 = `PAID + (PENDING 且未超时)`。
只统计 PAID 会漏：多用户同时 PENDING 全过检查 → 全支付 → 超卖。
时段行本身也要 `with_for_update`（F-067），名额计数与报名同一事务。
**测试背书**：`test_loop4_a_p1_6_upgrade_concurrency.py`。

### P9 退款的业务前置网（申请前全查）

退款申请前必须过五道闸（漏一道就是资金漏洞或客诉）：
1. 订单归属校验（user_id）+ 已支付状态
2. `assert_no_pending_transfer`：权益转移中 → 拒绝（转移/退款/删除三方互斥）
3. 未归还图书 > 0 → 拒绝（BorrowRecord BORROWING/OVERDUE 计数，**带行锁**）
4. 年度滥用拦截：365 天内同孩子已退过（APPROVED + COMPLETED 都算，F51/F25；
   用 `timedelta(days=365)` 不用 `replace(year-1)`——闰年 2/29 抛 ValueError）
5. 金额计算：服务端算 used_days（不信任前端）；未缴罚款自动抵扣退余额（B11）；
   可退金额 ≤ 0 → 拒绝创建 0 元退款单（F-009）

小额自动审核（E1）走同一套闸，只是免人工。审核动作本身要行锁防双审
（`status != PENDING 则 ConflictError`）。
**测试背书**：`test_refund_service.py`（duplicate/active_borrows/annual_limit 三个 blocker 测试）。

---

## 二、Circulation 域模式（借还/库存）

### P10 锁主体行串行化（L3-035——防超借的根）

**问题**：借书上限定"查活跃记录数→比对上限→插入"。
两并发事务都查到 9 本（快照读看不到对方未提交的插入）→ 都插入 → 11 本超借。
且 0 条活跃记录时 `with_for_update` 锁不到任何行，等于无锁。

**解法**：**先锁 Child 主体行**再查活跃记录：
```
child = query(Child).filter(id=..., is_deleted=0).with_for_update().first()
# Child 行锁把"同 child 的借书"串行化；后续活跃记录查询是当前读，能看到前序事务
active = query(BorrowRecord).filter(child_id=..., status in (BORROWING, OVERDUE)).count()
if active >= max_borrow: raise ValidationError(友好文案)
```
所有"以 child 为主体的写操作"（借书/预约取书/缴款 F-066）都遵守同一把锁，天然互斥。
**测试背书**：`test_borrow_service.py`、`test_concurrency.py`、MySQL 实证
`verify_mysql_concurrency.py`（注意：with_for_update 在 SQLite 是 no-op，
并发验证必须真 MySQL）。

### P11 SQL 原子库存更新（不锁不查，一条 UPDATE）

**问题**：`读 stock → Python 判断 → 写回` 三步，并发下互相覆盖（丢失更新）。

**解法**：条件更新，靠数据库原子性：
```
updated = query(Book).filter(id=..., available_stock > 0, is_deleted=0)\
    .update({available_stock: available_stock - 1})
if not updated: raise ValidationError("该书暂无库存")
```
带 `available_stock > 0` 条件=自带防超卖；`updated==0` 即无库存或行不存在。
回补库存同款（+1 无需条件）。注意库存读-改-写**必须行锁**的场景
（F-001/004：报损/丢失并发）与此区分：计数走原子 UPDATE，多字段联动走行锁。

### P12 副本行锁 + 状态字典（F69）

副本（BookCopy）借出前 `with_for_update` 锁行 + 状态检查；
不可借状态给**分状态文案**（维修中/已报废/已损坏/已丢失），已借出的附带预计归还日期
（客诉直降）。归还后副本必须回 AVAILABLE（F44，事件处理器里做）。
条码查重也要带行锁（F-115：先查后插无锁 → 并发双建同条码）。
**测试背书**：`test_bookcopy_status.py::test_borrowed_blocked`、`test_b5_f091_f115_uniqueness.py`。

### P13 事件链事务纪律（F64）

同步事件总线（共享同一 Session/事务）下的规矩：
- **事件处理器内禁止自行 commit**——由链路发起方统一提交，
  否则中途落库后失败无法整体回滚
- 事件处理器的辅助操作（扣库存/改副本状态）设计成"不 commit"方法，
  注释里显式声明（`decrease_available_stock` 等都带此声明）
- 事件只在**确定生效的路径**发（F39：DepositPaidEvent 仅即时支付路径发，
  生产网关 prepay 成功≠已付款，借书资格必须等回调才生效）

### P14 扫码即借（不存在则建）

条码已存在 → 直接借；不存在 → 校验必填字段（title/author/isbn/ar/age）→
按 ISBN 找书或建书 → 建副本 → 原子递增库存 → 走统一 `borrow_book`。
NOT NULL 列显式写入（F47），避免依赖 DB 默认值在迁移后漂移。

---

## 三、横切模式

### P15 软删除查询口径（F-016）

一切业务查询默认带 `is_deleted == 0`（资金回滚链、退款执行链、事件处理器全都要）。
漏一处 = 软删数据被复活/误操作。管理端列表走独立入口放开（F-116：下架书管理端可见）。
软删数据在退款回滚链中**整链不动作**（订单不存在/已软删 → return，不动退款单）。

### P16 儿童数据级联删除（合规 P0）

完整流程（顺序不可乱）：
```
前置阻塞器：未还书 / 押金未结清（PAID/PENDING/REFUNDING）/ 退款审核中 → 拒绝
请求：软删 child + deletion_requested_at（24h 冷静期可取消）
定时执行：备份 CSV → 收集语音文件路径 → 物理删除非财务表
         → 清冷静期标记 → commit → 删语音文件 → 通知监护人
```
关键纪律：
- **备份先行**（删前 dump CSV；CSV 值以 =/+/-/@ 开头加 `'` 前缀防公式注入 L3-027）
- **文件路径先收集后删**（删行后就查不到了）
- **先 commit 再删文件**（顺序颠倒：DB 回滚但文件没了=不可逆事故）
- **路径穿越防护**：`resolve()` 后必须 `startswith(uploads_dir + os.sep)`
  （补 os.sep 防逃逸到 uploads_backup 同前缀目录，F-12a/L2-033）
- 财务表法定保留（borrow/deposit/order/refund/damage/transfer 六表），
  由独立 purge 机制按保留期清理——**删除服务不碰**
- 表清单显式维护（child_id 直连 / 经 quiz_id 特殊级联 / user_id 关联 三类分开列）

**测试背书**：`test_child_deletion.py`（4 个 blocker + 冷静期 + 撤回级联）、
`test_f024_voice_delete_traversal.py`。

### P17 同意撤回 = 级联删除触发器（consent 三件套）

- 同意记录落 `consent_text_hash + version + ip + ua`（证据链，审计可查）
- `child_data` 类型撤回 = 对该用户**所有**孩子发起级联删除：
  先**全量**前置校验（任一孩子阻塞则整体拒绝，不留半个删了半个没删的中间态），
  再标记撤回，再逐个发起删除请求
- 每类同意只认最新一条有效记录

### P18 拒绝文案设计（面向家长/孩子的语气）

业务拒绝不是错误码堆砌：额度满 → "小书架满啦！先还一本，再借新的吧～"；
押金未缴 → "缴纳押金后即可借阅实体书哦～"；已借出 → 附预计归还日期。
**规则**：管理端操作报错带状态值（`当前状态(PAID)不允许...`，运维可查），
C 端用户报错带行动指引（下一步做什么）。

---

## 四、反模式清单（旧项目确认翻车，新项目禁止）

| 反模式 | 后果 | 溯源 |
|---|---|---|
| 网关调用抱在 DB 事务里 | 事务悬挂拖垮连接池 | P2 心法 |
| prepay 失败置回 UNPAID | 丢回调匹配，用户永久无法缴纳 | F-030 |
| 重复回调抛 404/500 | 微信重试风暴 | L2-008 |
| 每次重试新生成退款单号 | 重复打款 | F38 |
| 只统计 PAID 算名额 | PENDING 并发全过 → 超卖 | L3-020 |
| 锁"可能为空的结果集" | 等于没锁，并发插入漏网 | L3-035 |
| 退款剩余价值按现价算 | 折扣价高估 → 少收差价 | F16 |
| `replace(year=y-1)` 算一年前 | 闰年 2/29 崩溃 | F25 |
| 金额 int() 截断转分 | 每笔少收 1 分 | F-033 |
| total_amount 传当前余额 | 微信校验拒绝（要原支付单实付） | F37/F54 |
| 先删文件后 commit | DB 回滚文件没了，不可逆 | P16 |
| 事件处理器里自行 commit | 链路中途失败无法整体回滚 | F64 |
| 退款多入口（4 个近似方法） | 入口间守卫不一致，互相打架 | deposit 疤痕 |

---

## 五、新项目落地对照

| 新项目域 | 用到的模式 |
|---|---|
| billing（押金/收费/退款） | P1-P9 全部（P1 状态机 + P2 三段式是骨架） |
| circulation（借还/库存） | P10-P14 + P15 |
| identity（儿童合规删除/同意） | P16、P17 + P15 |
| report/admin（对账/告警） | P4（僵尸单监控）、P18（告警文案） |

**实施顺序建议**：先建状态机表（P1 矩阵直接进代码常量 + 单测穷举合法/非法转移，
参考 `test_f052_state_matrix.py` 写法）→ 再包三段式外壳（P2）→ 逐个补回调守卫（P3）。
每个模式落地时，把对应"旧项目翻车编号"写进代码注释，review 时可对号入座。

---

## 六、测试背书索引（新项目照此建测试）

| 模式 | 旧项目测试文件（design-reuse/tests/unit/） |
|---|---|
| P1 状态矩阵 | test_f052_state_matrix.py |
| P2/P4 押金链路 | test_deposit_service.py、test_p2_f58_f68.py |
| P3 回调幂等 | test_f12_refund_callback_guard.py、test_f084_duplicate_refund.py |
| P8 并发防重 | test_loop4_a_p1_6_upgrade_concurrency.py、test_f053_cancel_order_lock.py |
| P9 退款闸门 | test_refund_service.py |
| P10 超借 | test_borrow_service.py + scripts/verify_mysql_concurrency.py（真 MySQL） |
| P12 副本 | test_bookcopy_status.py、test_b5_f091_f115_uniqueness.py |
| P16 级联删除 | test_child_deletion.py、test_f024_voice_delete_traversal.py |

> 验证口径提醒（来自项目宪法）：with_for_update 在 SQLite 是 no-op，
> 所有 P10/P11 类并发断言必须用 `verify_mysql_concurrency.py` 在真实 MySQL 上实证。
