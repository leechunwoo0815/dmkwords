# WM5 借阅操作台 · 修复包

> 投喂时机：验收 WM5 时。来源：20260831 全量深度审查。
> WM5 状态：MANUAL_TEST（借/还/续/逾期扣减/人工放行/未入会开关已交付，等验收）。
> **本包含一个验收必撞的功能错误（WM5-F1 续借错行）**——验收前修掉可省一轮白测。

---

## 一、验收重点关注（验收 WM5 前必读）

### 必撞清单（测了也白测，先修后复核）

1. **续借错行（WM5-F1）**：孩子在借 ≥2 本书时，点**第 2 行**的「续借」按钮，实际
   被续的是**第 1 行**（到期日 +7 天落在错书上，且烧掉错书的终身 1 次续借资格）。
   前台完全无法察觉。**验收前必修**——一行改动
2. **还书弹窗「标记遗失/转维护」成功后弹窗不关（WM5-F2）**：可再次点击，
   对已 lost 副本重复调用；正常归架失败则无任何提示（onOk 无 catch）

### 测不出清单

- 双还覆盖 / overdue_mark 覆盖已还（P1-F5）
- 预约核销与借书的死锁窗口（P1-F8）
- 同孩子同书并发双预约（P1-F4 相关，主卡 WM6-F2）

### 验收可顺带复核

- 人工放行的留痕（审计含 reason + warnings 异常快照）——审查确认代码与测试均达标
- 未入会放行限 1 本 + 72h 借期（D1 闭环项）
- AR 超范围软提示不拦截

---

## 二、任务卡

### WM5-F1【高】借阅操作台「续借」按钮操作错误记录：续错行（一行修复）

- **验收可见性**：**验收必撞**——在借 2 本书点第 2 行续借，+7 天落在第 1 行
- **证据**：`admin-web/src/pages/CirculationDesk.tsx:229-235`：

```tsx
{ title: "续借", dataIndex: "renew_used", width: 90,
  render: (v: number) => v >= 1 ? <Tag>已用</Tag> : <Button type="link" size="small"
    onClick={async () => {
      try {
        await apiRenew(card.records.find((r) => r.renew_used === 0)?.id ?? 0);  // ← 全局查找，与被点击行无关
        message.success("续借成功（+7 天）");
        await refresh();
      } catch (e) { message.error(...); }
    }}>续借</Button> }
```

- **现象与根因**：antd Table 的 column render 签名为 `(value, record, index)`——本列
  只声明了第一个参数 `v`，onClick 里退化为 `card.records.find(...)` 全局查找第一条
  可续借记录。孩子在借多本书时，点任何一行的续借都续第 1 行。
  **影响借阅状态机**（错误记录被 +7 天并消耗续借次数，每本终身仅 1 次）。
- **修复方案**（一行）：

```tsx
render: (v: number, r: RecordType) => v >= 1 ? <Tag>已用</Tag> : (
  <Button type="link" size="small" onClick={async () => {
    try {
      await apiRenew(r.id);          // ← 用本行记录 id
      message.success("续借成功（+7 天）");
      await refresh();
    } catch (e) { message.error(e instanceof Error ? e.message : "续借失败"); }
  }}>续借</Button>
)
```

- **测试要求**：无前端单测；后端已限制每本终身 1 次续借——修复后手动验证：
  借阅台搜一个在借 2 本书的孩子，点第 2 行续借 → 到期日 +7 天必须落在第 2 行
- **验证等级**：A 级（tsc）
- **标注**：【事实】（审查者亲验代码原文）

### WM5-F2【高·前端】还书弹窗：异常分支无错误处理 + 标记遗失/转维护后弹窗不关闭可重复点

- **验收可见性**：**验收必撞**——点「标记遗失」成功后弹窗停留，可再次点击
- **证据**：`admin-web/src/pages/CirculationDesk.tsx:131-152`：

```tsx
// L131-135 正常归架 onOk 无 try/catch：
onOk: async () => {
  await apiReturnBook(record.copy_id, "normal");   // 失败 → unhandled rejection，无提示
  ...
},
// L139-152「标记遗失」「转维护」按钮：成功后未调用关闭（modal.confirm 实例无 destroy）
```

- **现象与根因**：① 正常归架失败无提示；② 标记遗失成功后弹窗不关，再点一次对已
  lost 副本重复调用（与 P1-F5 后端双还覆盖叠加）。根因：未保存 `modal.confirm`
  返回实例、成功分支缺 `destroy()`。
- **修复方案**：

```tsx
const modal = AntdApp.useApp().modal;   // 项目已有 useApp 模式
const m = modal.confirm({
  ...
  onOk: async () => {
    try {
      await apiReturnBook(record.copy_id, "normal");
      message.success("归还成功");
      m.destroy();               // ← 成功后关闭
      await refresh();
    } catch (e) {
      message.error(e instanceof Error ? e.message : "归还失败");
      return Promise.reject(e);  // ← 失败保持弹窗
    }
  },
  // footer 里的「标记遗失」「转维护」按钮同样补 try/catch + 成功后 m.destroy()
});
```

- **测试要求**：手动验证：点标记遗失成功 → 弹窗关闭；断网点归架 → 错误 toast
- **验证等级**：A 级（tsc）
- **施工顺序**：与 WM5-F1 同文件同批（CirculationDesk.tsx 一次改完）
- **标注**：【事实】

### WM5-F3【中】可借上限公式两套：借书路径扣逾期、预约路径不扣（隐式依赖拦截顺序）

- **验收可见性**：验收测不出（当前行为等价——靠前置硬拦兜住）
- **证据**：借书（宪法公式）`backend/domain/circulation/service.py:224-228`：

```text
quota = borrow_limit - overdue_count - active_count
if quota <= 0 and not override_reason:
    raise ValidationError(
        f"可借上限已满（上限 {borrow_limit}，逾期 {overdue_count} 本，在借 {active_count} 本）")
```

预约（第二实现，**不含逾期扣减**）`backend/domain/reading/service.py:332-335`：

```text
if active_borrows + active_reservations >= borrow_limit:
    raise ValidationError(
        f"借阅额度已满（在借 {active_borrows} + 预约 {active_reservations} / 上限 {borrow_limit}）")
```

- **现象与根因**：宪法红线"可借上限 = 30 − 逾期未还数；预约占额度"。当前不出错的
  唯一原因是 `reading/service.py:287-298` 前置硬拦"有逾期未还图书，请先归还"——
  逾期孩子到不了额度检查。**隐式依赖拦截顺序**：一旦未来给预约加"馆员放行逾期"
  （借书侧已有 override_reason），预约额度立即多算逾期数。WM6/WM5 分批交付未抽公共函数。
- **修复方案**：抽公共计算（推荐放 circulation 域导出，两域调用）：

```text
# circulation/service.py 增公开方法（或独立 quota 模块）
def available_quota(self, child: Child) -> int:
    overdue = ...  # 既有 overdue_count 逻辑
    active = ...
    return max(0, child_quota - overdue - active)

# reading/service.py 预约额度检查改：
    quota = CirculationService(self.db).available_quota(child)
    if quota <= 0:
        raise ValidationError("借阅额度已满（含逾期占用）")
```

  注意保留预约前置硬拦（逾期不可预约的业务语义不变，此卡只统一**计算口径**）。
- **测试要求**：参数化测试（逾期 0/1/N × 在借 × 预约组合）断言两路径同入同出；
  既有借阅/预约测试回归不弱化。
- **验证等级**：C 级（跨 circulation/reading 两域公共逻辑）→ gate.sh full
- **施工顺序**：可与 P1-F4 同批（同在 reading/service.py 预约逻辑区）
- **标注**：【事实】（公式差异）；当前行为等价为【推断】（依赖前置拦截的推演）

### WM5-F4【低】查询方法内藏写副作用：overdue_list 顺手标逾期并 commit

- **证据**：`backend/domain/circulation/service.py:495-498`：

```text
        # 顺手把状态标成 overdue
        for record, *_ in rows:
            record.status = BorrowRecord.STATUS_OVERDUE
        self.db.commit()
```

- **现象与根因**：管理端列表查询承担逾期落库（另有定时任务 overdue_mark 干同一件事
  ——同一状态转移两处写，幂等所以无害但重复）。根因：读接口混写副作用。
- **修复方案**：overdue_list 改纯读（删除循环与 commit，展示时按 due_at 判定逾期视图）；
  落库只留定时任务。**若删除影响"列表看到 OVERDUE 标签"**（status 仍 active 未被任务扫到），
  则渲染层用 `due_at < now` 补判定。
- **测试要求**：既有逾期列表测试回归（显示不变）；任务侧 overdue_mark 测试不变
- **验证等级**：B 级（circulation 域）
- **施工顺序**：P1-F5（overdue_mark 守卫）之后——两卡同函数族，先固化任务侧写路径再清列表侧
- **标注**：【事实】

### WM5-F5【低】借阅台用正则匹配后端错误文案决定「人工放行」分支——脆弱契约

- **证据**：`admin-web/src/pages/CirculationDesk.tsx:87`：

```tsx
if (!override && /押金|上限|未入会|过期/.test(errText) && !/限 1 本/.test(errText))
```

- **现象与根因**：后端改错误文案（如"押金未缴纳"换个措辞）→ 放行入口静默失效。
  根因：ApiError 只有 status/message 无结构化 code，前端只能靠文案匹配。
- **修复方案**（最小改）：后端 ValidationError 增加可选 `error_code`（如
  `borrow_deposit_required` / `borrow_limit_full`），借阅相关错误文案下发时带 code；
  前端匹配改 code。若不想动后端，至少把正则关键词与后端文案常量抽到一处对照注释
  （后端改文案时 grep 此注释）。
- **测试要求**：放行按钮在押金/上限/未入会/过期错误下出现、在"限 1 本"错误下不出现（手动复核）
- **验证等级**：B 级（若动后端 error_code）/ A 级（仅前端注释方案）
- **标注**：【事实】

### WM5-F6【低】逾期名单静默 catch + 无 loading

- **证据**：`admin-web/src/pages/CirculationDesk.tsx:48`（`.catch(() => undefined)`）+
  `:248`（表格无 loading prop）——请求失败静默、加载无反馈。
- **修复方案**：catch 改 message.error + overdueLoading state + Table loading prop
  （与 HC-F3 假空态同模式）。
- **验证等级**：A 级（tsc）
- **标注**：【事实】

---

## 三、引用卡

| 引用 | 主题 | 主卡位置 |
|---|---|---|
| P1-F5 | 还书双还覆盖 + overdue_mark 守卫 | `P1-立即修复-资金并发行锁.md` |
| P1-F8 | checkout/borrow 锁序死锁窗口 | 同上 |
| P1-F4 | 预约释放物理双借 | 同上 |
| WM6-F2 | 同孩子同书并发双预约 | `WM6-小程序阅读链-修复包.md` |
| HC-F4 | 时间显示统一（借阅台三处 slice 含 T） | `横切-前端质量-修复包.md` |

## 四、本包施工顺序与验证

**第一批（前端，A 级）**：WM5-F1（一行，最优先）+ WM5-F2 + WM5-F6（同文件一次改完）
**第二批（后端）**：P1-F5 引用卡完成后 → WM5-F4（B 级）；WM5-F3（C 级跨域）单独批
全部完成后：`cd admin-web && pnpm exec tsc --noEmit` + 后端相关域 pytest + 合批 C 级 gate。
