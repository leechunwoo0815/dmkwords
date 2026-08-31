# WM9 线下活动 · 修复包

> 投喂时机：验收 WM9 时。来源：20260831 全量深度审查。
> WM9 状态：MANUAL_TEST（发布/报名/入场券/签到/退款矩阵/99 元链已交付，等验收）。
> **本包含退款双轨主战场（WM9-F1，与 WM10 联动）+ 时区疑云实测项（WM9-F4）**。

---

## 一、验收重点关注（验收 WM9 前必读）

### 必撞清单

1. **活动时间显示差 8 小时（WM9-F4 时区疑云，需实测裁决）**——发布开始时间选
   15:00 的活动，看列表"开始时间"是否显示 15:00。若显示 07:00 即坐实
   （`toISOString()` 发 UTC + 后端原样存 + 前端原样切的组合）
2. **活动退款审核后 99 元资格口径（WM9-F1 双轨）**——走"门店取消活动→逐单审核"
   通道退款后，该家长再买 99 元首单是否被拒。当前**不会被拒**（双轨之一不写
   refund_status，资格判定读过期口径）——这既是卡也是验收可见的口径分裂

### 测不出清单

- 并发抢最后 1 个名额（审查确认活动报名持 Activity 行锁零超卖——**安全**，无需测）
- 99 元双端并发（P1-F6）

### 验收可顺带复核（审查确认的正面项）

- 报名名额并发安全（activity/service.py:402-434 行锁模板——全项目并发最标准的一段）
- 活动批量退款通知场景（admin.activity_batch_refund 六场景之一，WM13 已接线）

---

## 二、任务卡

### WM9-F1【高】活动退款双轨：第二轨不写 order.refund_status、无退款单留痕（与 WM10 联动）

- **验收可见性**：**验收可撞**——门店取消通道退款后查 99 元资格/退款中心列表，
  口径与家长申请通道不一致（见上方必撞清单 2）
- **证据**【事实，审查者亲验两处代码原文】：
  - 路径①（活动退款审核通道）`backend/domain/activity/service.py:359-364`：

```text
if approve:
    e.status = ActivityEnrollment.STATUS_REFUNDED
    if e.order_id:
        order = self.db.query(Order).filter(Order.id == e.order_id).first()
        if order and order.status == Order.STATUS_PAID:
            order.status = Order.STATUS_REFUNDED          # ← 不改 refund_status
```

  - 路径②（退款中心通道）`backend/domain/identity/wm10_service.py:355-364`（同业务动作）：
    `order.status = Order.STATUS_REFUNDED` **且**
    `order.refund_status = Order.REFUND_STATUS_REFUNDED` **且**创建 RefundRequest 留痕
  - 消费方依赖该字段：99 元资格判定 `identity/service.py:357-367`
    `Order.refund_status != Order.REFUND_STATUS_REFUNDED`
- **现象与根因**：TYPE_ACTIVITY 订单退款有两条互不知晓的路径：① 门店取消活动 →
  refund_review（不创建 RefundRequest——资金退款**零单据留痕**，仅 publish_audit；
  只改 order.status 不改 refund_status）；② 家长申请 → RefundService 七态机。
  退款七态机（R-308）只覆盖路径②。根因：WM9 先于 WM10 交付，"退款执行"各自实现
  未做全库同口径核对（违反宪法"改基线状态机全库 rg 同口径核对"精神）。
- **影响**：活动退款资金对账无退款单可查；按 refund_status 判定的消费方（99 元资格、
  WM13 待办、未来对账脚本）在路径① 下读过期口径。
- **修复方案**（二选一，推荐 A；请用户裁决）：
  - **A（收敛到退款中心，推荐）**：路径① 的 refund_review 改为调用
    `RefundService`（apply 已存在则 review+execute，或直接建 refunded 终态退款单
    + 走 execute 的活动分支 `_refund_activity_enrollment`）——所有退款单一出口。
    改动集中在 activity/service.py 的 refund_review，删本地 order 改写逻辑
  - **B（补字段对齐，最小改）**：refund_review 的 approve 分支补
    `order.refund_status = Order.REFUND_STATUS_REFUNDED` + 创建 RefundRequest
    （kind=order, status=refunded, amount=订单实付）留痕——双轨并存但口径一致
- **测试要求**：`tests/unit/test_wm9_refund_dual_track.py`（红测试）：
  1. TYPE_ACTIVITY 订单走路径①（取消活动→审核通过）→ 断言
     `order.refund_status == "refunded"` **且** refund_requests 表有对应记录
     （**修复前两者皆无，RED**）
  2. 同一单走路径② 的既有测试回归（七态机不弱化）
  3. 路径① 退款后该家长 99 元资格恢复断言（再建首单不抛 ConflictError）
- **验证等级**：C 级（跨 activity/identity 两域 + 资金状态机）→ gate.sh full
- **施工顺序**：**与 WM10-F1（退款并发锁）同批**——P1-F1 加锁后的 RefundRequest
  路径是本卡收敛的目标出口，先有锁再有双轨收敛
- **标注**：【事实】（双轨与字段差异亲验）；对账风险【推断】

### WM9-F2【中】活动费用请求模型用 float：违反金额 Decimal 铁律 + 无上限

- **验收可见性**：验收可撞（输 19.99 之类的值理论上会有精度残差；传超大值 DB 500）
- **证据**【事实】：`backend/domain/activity/router.py:25`：
  `fee: float = Field(0, ge=0)`（无上限）→ 落库 Numeric(10,2)
  （`activity/models.py:32`）；`service.py:161` `fee=req.fee` 直传；
  `service.py:118` `float(a.fee) == 0` 浮点比较。
- **现象与根因**：违反"金额一律 Decimal"红线（全库唯一例外——其余金额链路全 Decimal）；
  `fee=99999999999` 超 Numeric 范围 DB 报错 500。
- **修复方案**：

```text
# router.py
    fee: Decimal = Field(Decimal("0"), ge=0, le=Decimal("99999999.99"),
                         description="活动费用（元）")
# service.py:118 浮点比较改：
    if a.fee == 0:
```

- **测试要求**：`POST /api/admin/activities` 传 `fee: "19.99"`/超大值/负值分别断言
  200/422/422；DB 中 SELECT fee 精确两位
- **验证等级**：B 级（activity 域）
- **标注**：【事实】

### WM9-F3【中】活动列表 N+1：每行一次 _quota_used count

- **证据**【事实】：`backend/domain/activity/service.py:124-129`——list_upcoming
  limit 50，每行调 `_quota_used`（count 查询）→ 51 条 SQL。
- **修复方案**：改一次 GROUP BY 聚合：

```text
# 列表查询后，一次取出全部活动的已报名数：
    used_map = dict(
        self.db.query(ActivityEnrollment.activity_id, func.count(ActivityEnrollment.id))
        .filter(ActivityEnrollment.activity_id.in_([a.id for a in activities]),
                ActivityEnrollment.status == ActivityEnrollment.STATUS_ENROLLED,
                ActivityEnrollment.is_deleted == 0)
        .group_by(ActivityEnrollment.activity_id)
        .all()
    )
# 行内用 used_map.get(a.id, 0)
```

- **测试要求**：既有活动列表测试回归（名额显示不变）
- **验证等级**：B 级（activity 域）
- **标注**：【事实】

### WM9-F4【高·需实测裁决】活动时间 toISOString() 发 UTC，疑似与显示切片组合差 8 小时

- **验收可见性**：**验收实测项**——发布开始时间 15:00 的活动，看列表显示
- **证据**【事实】（组合证据，结论【推断】需实测）：
  - 发送侧 `admin-web/src/pages/ActivityManage.tsx:64`：
    `start_at: v.start_at.toISOString()`（DatePicker 选 15:00 CST → 发 `07:00:00.000Z`）
  - 同页 `:67` enroll_deadline 同款
  - 显示侧 `:159`：`v.replace("T", " ").slice(0, 16)` 原样切片（若后端原样回显
    `07:00:00` 则显示 07:00——差 8 小时）
- **裁决方法**（验收第一步）：发布一个开始时间为当天 15:00 的活动 → 看列表
  「开始时间」列。显示 15:00 = 无问题（后端做了时区转换）；显示 07:00 = 坐实
- **修复方案**（若坐实）：

```tsx
// ActivityManage.tsx 提交改 dayjs 本地格式（与后端 naive 存储约定对齐）：
start_at: v.start_at.format("YYYY-MM-DDTHH:mm:ss"),
enroll_deadline: v.enroll_deadline.format("YYYY-MM-DDTHH:mm:ss"),
```

  同时 grep 后端 activity 域有无对 Z 后缀的解析（若无即 naive 存储坐实）。
  **存量数据**：已发布的活动若有 8 小时偏差需修正（列清单报用户裁决，禁止静默改）。
- **测试要求**：若坐实——红测试：创建 15:00 活动 → GET 列表断言 start_at 含 "15:00"
- **验证等级**：A 级（前端）+ B 级（若后端补解析）
- **标注**：证据【事实】；偏差结论【推断】（需实测裁决）

### WM9-F5【低】报名名单 Drawer 闪空态 + 活动退款审核 onOk 无防双击

- **证据**【事实】：`admin-web/src/pages/ActivityManage.tsx:95-100`（先 setEnrollments([])
  再拉取，加载瞬间闪 PaintEmpty 空态）；`:121`（退款审核 onOk 无 confirmLoading）。
- **修复方案**：enrollmentsLoading state + Table loading（拉取中不闪空态）；
  防双击统一走 HC-F1 模式。
- **验证等级**：A 级（tsc）
- **标注**：【事实】

---

## 三、引用卡

| 引用 | 主题 | 主卡位置 |
|---|---|---|
| P1-F1 | 退款 execute/review 行锁（双轨收敛的前置） | `P1-立即修复-资金并发行锁.md` |
| P1-F6 | 99 元资格并发复查 | 同上 |
| WM10-F1 | 退款中心侧同族卡（refunding 态推进） | `WM10-退款退会转让-修复包.md` |
| HC-F3 | 活动退款待审列表假空态（ActivityManage:54） | `横切-前端质量-修复包.md` |
| HC-F1 | 发布活动/退款审核弹窗防双击 | 同上 |

## 四、本包施工顺序与验证

1. **验收第一步先做 WM9-F4 实测裁决**（发布 15:00 活动看显示——裁决结果决定是否施工）
2. WM9-F2（fee Decimal，半小时）+ WM9-F3（N+1）独立批
3. WM9-F1（退款双轨，用户裁决 A/B 后）**与 P1-F1 + WM10-F1 同批**——退款单出口统一
   的大批，C 级 gate
4. WM9-F5 随手批

完成后：`python -m pytest tests/unit/test_wm9_activity.py tests/unit/test_wm9_refund_dual_track.py -q`
+ C 级 gate + 管理端动线（发布→报名→取消→退款审核→查 99 元资格→退款中心列表核对）。
