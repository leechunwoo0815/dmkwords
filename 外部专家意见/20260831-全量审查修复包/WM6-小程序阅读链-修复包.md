# WM6 小程序阅读链与预约 · 修复包

> 投喂时机：验收 WM6 时。来源：20260831 全量深度审查。
> WM6 状态：MANUAL_TEST（miniapp 9 页 + 防刷 + 预约链已交付，等验收）。
> **这是全项目问题最重的包**：越权主卡（P0-F1 在此模块）+ 预约并发 + 音频门禁。
> P0-F1（越权 6 端点）与 P1-F4（预约释放双借）主卡在 P0/P1 包，本包不重复施工。

---

## 一、验收重点关注（验收 WM6 前必读）

### 必撞清单

1. **预约成功后收不到任何通知（WM6-F1）**——reservation.created 事件零发布，
   家长预约成功后消息中心无"预约成功"通知（而预约释放通知正常发）。已挂死订阅器
2. **非会员账号可听任意书目完整音频（WM6-F3）**——音频端点无会员门禁。
   若这是 V1 刻意设计请用户明示并记 ADR，否则按卡修复

### 测不出清单

- 双设备同时首播 → 重复进度行/重复打卡（WM6-F4 唯一约束缺失）
- 同孩子同书双击预约 → 双预约锁两副本（WM6-F2）
- 预约超时释放与核销并发 → 物理双借（P1-F4）
- 媒体 token 泄露面（HIGH-14，WM12 前置包）

### 验收可顺带复核（审查确认的正面项）

- 防刷按 R-151 墙上时钟（已重写过，coverage 有测试）
- 继续听/续播三态（v6 批次修复：开始听/继续听/再听一遍 + seek 续播）
- 查词闭环（WM11 遗留已关闭）

---

## 二、任务卡

### WM6-F1【高】reservation.created 事件有订阅无发布：家长预约成功通知永不触发

- **验收可见性**：**验收必撞**——预约一本书后翻消息中心，没有"预约成功"通知
- **证据**【事实，审查者亲验 grep 全库仅 4 处命中】：
  - 订阅器存在且文案完备：`backend/tasks/notify_handlers.py:206-226`——
    "《{title}》预约成功，请及时到馆取书（72 小时内核销）"
  - 注册于 `notify_handlers.py:284`
  - 事件定义：`backend/common/events.py:217`
  - **发布点：零**。预约创建 `backend/domain/reading/service.py:337-362` 只有
    `copy.status = BookCopy.STATUS_RESERVED` → `self.db.commit()`，无事件发布
- **现象与根因**：WM6 交付时只写了订阅器（按 WM11 通知清单）漏写发布点；
  宪法"事件发布/订阅双向对账入门禁"未实现（verify_architecture.py:4 自述
  "域就绪后逐步收紧"），无门禁兜底。同业务闭环里预约**释放**通知（reservation.expired）
  正常发送——一半有声一半无声。
- **附带问题**【事实】：订阅器复用 `SCENE_RESERVATION_EXPIRING` 场景常量发"预约成功"
  文案——场景语义错位（通知分类/筛选会归错类）。
- **修复方案**：

```text
# reading/service.py create() 预约成功 commit 前补发布（参照同文件 L404
# reservation.expired 的发布写法）：
    event_bus.publish(
        ReservationCreatedEvent(
            reservation_id=res.id,
            child_id=child.id,
            book_id=book_id,
            copy_id=copy.id,
        ),
        db=self.db,
    )
    self.db.commit()

# 场景语义修正：notify_handlers._on_reservation_created 改用独立场景常量
# SCENE_RESERVATION_CREATED（notifications 场景清单若需加值，同步 miniapp 消息页分类映射）
```

- **测试要求**：`tests/unit/test_wm6_reservation_notify.py`（红测试）：
  走 API 创建预约 → 查 notifications 表断言新增一条 parent 维度"预约成功"通知
  （**修复前零条，RED**）；既有 reservation.expired 测试回归。
- **验证等级**：B 级（reading + tasks 跨域）→ 建议 C 级（事件订阅注册链）
- **施工顺序**：**先做事件对账门禁的前置数据**——本卡修复后，横切-门禁包 HC-T6
  的"事件双向对账检查"才有基线可开
- **标注**：【事实】

### WM6-F2【中】Reservation 无唯一约束 + 查重在锁外：同孩子同书并发双预约，锁死两个副本

- **验收可见性**：验收测不出（需双击/双端同刻）；可被 P0-F1 越权端点放大（修复后消除）
- **证据**【事实】：`backend/domain/reading/models.py:50-65`（Reservation 无任何
  `__table_args__` 唯一索引，迁移 92e6434da8d1 同证仅普通索引）；
  `reading/service.py:300-311` 查重 count 在 L337 副本锁**之前**执行：

```text
# L300-311 同书进行中预约唯一（count 查重，无锁）
dup = (
    self.db.query(func.count(Reservation.id))
    .filter(Reservation.child_id == child.id, Reservation.book_id == book_id, ...)
    .scalar()
)
if dup:
    raise ConflictError("该书已有进行中的预约")
...
# L337-348 这一步才锁副本
copy = (self.db.query(BookCopy).filter(...).order_by(BookCopy.id).with_for_update().first())
```

- **并发推演**【推断】：双击/双端并发：A、B 都通过 dup=0；A 锁 copy1 置 reserved、
  commit；B 的副本查询发现 copy1 已非 available → 锁 copy2 → 第二条 active 预约建立。
  同书两个预约占两份额度、锁两个副本 72h。
- **修复方案**（与 borrow 同款：锁主体行 Child 串行化同孩子操作）：

```text
# create() 入口先锁 Child（与 CirculationService.borrow L102 同模板）
    child = (
        self.db.query(Child)
        .filter(Child.id == child.id, Child.is_deleted == 0)
        .with_for_update()
        .first()
    )
# 之后的 dup 查重与副本锁定都在 Child 行锁内——同孩子并发预约串行化，
# B 拿锁后 dup 查到 A 已提交的预约 → 拒绝
```

  备选（DB 兜底）：加冗余唯一列（MySQL 无 partial index）——复杂度更高，不推荐首选。
- **测试要求**：并发用例（复用 P1 包 session_pair 基建）：同 child 同 book 并发双
  `ReservationService.create` → 断言最终 active 预约数 ==1（**修复前 2，RED**）；
  既有预约测试回归。
- **验证等级**：B 级（reading 域）
- **施工顺序**：P1-F4 同批（同文件同函数族）
- **标注**：【事实】（结构）；推演【推断】

### WM6-F3【高·需产品裁决】小程序音频端点无会员门禁：付费音频任意家长可拉全量

- **验收可见性**：**验收可撞**——用未入会测试账号进详情页，若直接点播放能听到
  完整音频（当前能，因为门禁只在进度上报层）→ 即此卡
- **证据**：`backend/domain/reading/miniapp_router.py:253-270`：

```text
@router.get("/books/{book_id}/audio")
def book_audio(book_id: int, token: str = "", db: Session = Depends(get_db)):
    _parent_from_token(token, db)          # 只验家长身份
    book = db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
    if not book or not book.audio_path:
        raise NotFoundError("音频不存在")
    ...return FileResponse(full, media_type="audio/mpeg")   # ← 无会员/在借校验
```

会员门禁只落在 `report_progress`（`reading/service.py:97-115`，FEAT-038：
非会员/过期未持有 → 422）。音频 URL 规律为 `/api/miniapp/books/{id}/audio` 可遍历。
- **现象与根因**：鉴权点选在进度上报而非资源下发——听书是核心付费权益（FEAT-038），
  字节流层完全开放。**若属 V1 刻意简化**（上线前再收紧），需在代码注释/PRD 明示
  并记入 WM12 前置清单——当前无任何说明。
- **修复方案**（对齐 report_progress 的 FEAT-038 判定）：

```text
# book_audio 内 _parent_from_token 后补（复用 reading/service.py:97-115 的判定逻辑，
# 建议抽成 ReadingService.ensure_listening_allowed(child, book) 公共方法两处调用）：
    child = _child_from_parent(db, parent)   # 家长的当前孩子
    ReadingService(db).ensure_listening_allowed(child, book)   # 非会员/过期未持有 → 422
```

  语义：有效会员全馆可听；过期会员仅手中在借/历史进度书可听；未入会拒绝（与
  report_progress 一致）。**多孩家庭的当前孩子选择**：按 miniapp 会话 currentChild
  传 child_id 参数（与 progress 端点同款）。
- **测试要求**：`tests/unit/test_wm6_audio_gate.py`：
  1. `member_status=none` 家长 token 拉 `/api/miniapp/books/1/audio` 断言 403/422
     （**修复前 200，RED**）
  2. observation 会员 200；formal 会员 200
  3. 过期会员 + 在借该书 200；过期会员 + 未借该书 422
- **验证等级**：B 级（reading 域）；**先请用户裁决口径再施工**（是否 V1 刻意设计）
- **标注**：【事实】（端点无校验亲验）；设计意图【推断】

### WM6-F4【中】reading_progress / checkins 注释声明唯一但库里无唯一约束：双设备并发产生重复行

- **验收可见性**：验收测不出（双设备同刻首播）；长期脏数据会导致打卡日历/streak 分叉
- **证据**【事实】：`backend/domain/reading/models.py:16-19`：

```text
__tablename__ = "reading_progress"
__table_args__ = (  # 学生×书目唯一（进程行）     ← 注释声明唯一
    Column("child_id", Integer, nullable=False, index=True),   # 仅单列索引
)
```

`models.py:40-43`（checkins 同样无 `(child_id, checkin_date)` 唯一约束、无 checkin_date
索引）。写入均为 query-then-insert：`reading/service.py:120-132`（progress）、
`:196-209`（checkin exists 查重后 add）。对照组：vocabularies/favorites 都建了唯一索引
（`reading/models.py:83,95`）。
- **现象与根因**：注释承诺的约束没落成 UniqueConstraint；并发防线只有应用层查重。
  双设备同时首播 → 两条进度行（`.first()` 只更新其一，另一条孤儿）；同日并发完播打卡
  → 重复 CheckInEvent（重复积分）+ streak 分叉。
- **修复方案**（迁移 + 防御写入）：

```text
# ① 新 alembic 迁移：
op.create_index("uq_progress_child_book", "reading_progress",
                ["child_id", "book_id", "is_deleted"], unique=True)
op.create_index("uq_checkin_child_date", "checkins",
                ["child_id", "checkin_date", "is_deleted"], unique=True)
op.create_index("ix_checkins_child_date", "checkins", ["child_id", "checkin_date"])

# ② 写入路径防撞（参照 E-20260829-08 复活语义 / INSERT IGNORE 既有模式）：
# checkin 查重命中软删行 → 置 is_deleted=0 复活；progress 首报捕获 IntegrityError
# 幂等返回既有行
```

  迁移前先查库确认无存量重复（有则先清洗再建唯一索引——E-20260829-08 教训）。
- **测试要求**：并发用例：双 session 同 child 同 book 首报 progress → 断言仅一行；
  同日双打卡 → 一条记录一次积分事件；删除→再打卡复活语义回归（若有既有测试）。
- **验证等级**：C 级（models + 迁移）→ `bash scripts/gate.sh full`（含 alembic check）
- **施工顺序**：独立批（迁移单独 commit）
- **标注**：【事实】

### WM6-F5【中】miniapp /books 分页参数无约束：page_size 可传 100000 全表拉取

- **证据**【事实】：`backend/domain/reading/miniapp_router.py:118-119`：
  `page: int = 1, page_size: int = 20` 无 Query(ge/le) 约束；管理端同参数有
  `Query(1, ge=1)` + `le=100` 规范（billing/router.py:52-53 示范）。
- **修复方案**：

```text
def list_books(
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ...
```

- **测试要求**：`page_size=100000` 断言 422；正常 20 条 200
- **验证等级**：B 级（reading 域）
- **标注**：【事实】

### WM6-F6【低】家长撤销退款/退会端点后端已存在，小程序侧零调用（撤销路径用户不可达）

- **证据**【事实】：后端有 `POST /api/miniapp/refund-requests/{id}/cancel`
  （`identity/miniapp_router.py:192`）、`POST /api/miniapp/withdrawals/{id}/cancel`
  （`:222`），但 `miniapp/utils/api.js` 无对应封装、pages 全局 grep 无调用
  （仅活动/预约/转让的 cancel 有调用）。WM13 的"已失效·家长已撤销"显示态依赖
  cancelled 状态——当前撤销路径只在后端测试里可达。
- **修复方案**（二选一，请用户裁决）：
  - A：补 api 封装 + 我的退款/退会列表页加"撤销申请"按钮（带确认弹窗）
  - B：V1 不开放家长撤销 → PRD/docs/03 标注"撤销仅后端通道"，WM13 显示态保留
    （留给超管驳回路径）
- **测试要求**：选 A 则端到端：家长申请退款 → 小程序撤销 → WM13 待办变"已失效·家长已撤销"
- **验证等级**：B 级（miniapp 前端 + 已有后端）
- **标注**：【事实】

### WM6-F7【低】媒体端点 startswith(root) 未加 os.sep（理论性路径前缀绕过）

- **证据**【事实】：`reading/miniapp_router.py:268,285,314`、`growth/miniapp_router.py:123`、
  `catalog/router.py:69`、`file_storage.py:28` 用 `full.startswith(root)`——
  `/app/uploads` 会放行 `/app/uploads-evil/...`（需先存在同名前缀目录，理论性）。
  正确写法已有：`file_utils.py:35` 的 `startswith(str(uploads_root) + os.sep)`。
- **修复方案**：统一改 `startswith(str(uploads_root) + os.sep)`（6 处，
  复用 file_utils 的写法或抽公共 guard 函数）。
- **测试要求**：构造 `../uploads-evil` 路径请求断言 404（现有路径穿越测试回归）
- **验证等级**：B 级（跨三域 router 小改）
- **标注**：【事实】

---

## 三、引用卡

| 引用 | 主题 | 主卡位置 |
|---|---|---|
| P0-F1 | 越权 6 端点（主责模块在此，卡片在 P0 包） | `P0-立即修复-安全漏洞五项.md` |
| P1-F4 | 预约超时释放与核销并发物理双借 | `P1-立即修复-资金并发行锁.md` |
| P1-F8 | checkout/borrow 锁序死锁 | 同上 |
| WM12-P3 | token-in-query 媒体 token 改造 | `WM12-支付前置-安全清单.md` |
| HC-A6 | LIKE 未转义（books keyword 5 处之一） | `横切-架构债-修复包.md` |
| WM5-F3 | 可借上限公式两套（预约侧） | `WM5-借阅操作台-修复包.md` |

## 四、本包施工顺序与验证

1. **P0-F1 先行**（越权，已在 P0 包排首位）
2. WM6-F1 预约通知（半天，独立）+ WM6-F5 分页约束（10 分钟）
3. WM6-F2 + P1-F4 同批（预约并发族，含并发测试）
4. WM6-F4 唯一约束迁移（独立批 C 级）
5. WM6-F3 音频门禁（**用户裁决口径后**施工）
6. WM6-F6 撤销入口（用户裁决 A/B 后）、WM6-F7 随手批

完成后 C 级 gate 一次 + 小程序模拟器回归动线（登录→图书馆→详情→预约→听书→消息中心
看"预约成功"通知）。
