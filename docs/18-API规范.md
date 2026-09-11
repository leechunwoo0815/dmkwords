# docs/18 — API 答题规范（2026-09-03，T28/S2）

> **定位**：前后端对 API 的"答题规范"——状态码怎么映射、错误长什么样、字段怎么命名、
> 改接口走什么流程。全部内容从代码实测提取（源文件标注），禁止凭记忆写。
> 配套：T27 契约快照（docs/api/openapi.json + gate [8] diff 检查）。

---

## 一、状态码映射表（源：backend/common/exceptions.py 全 9 类，实测）

| 异常类 | HTTP | 语义 |
|---|---|---|
| `UnauthorizedError` | 401 | 未认证（token 缺失/无效/过期） |
| `ForbiddenError` | 403 | 权限不足（RBAC 拒绝；可带 error_code） |
| `OwnershipError` | 403 | 归属权校验失败（数据不属于当前家长，P0-F1 体系） |
| `NotFoundError` | 404 | 资源不存在 |
| `PaymentError` | 402 | 支付相关错误 |
| `BadRequestError` | 400 | 请求参数错误（通用） |
| `ConflictError` | 409 | 数据冲突（重复创建 / 状态机非法跳转 / 唯一约束） |
| `ValidationError` | 422 | **业务层**参数校验失败（区别于 Pydantic 校验，见 §二） |
| `RateLimitError` | 429 | 请求频率超限（T8 限流接线） |

**强制链路**（物理强制，不靠自觉）：
- Service 层只抛 `BusinessException` 体系异常，自带状态码语义；
- 全局处理器 `business_exception_handler` 于 `backend/main.py:42` 注册
  （`app.add_exception_handler(BusinessException, ...)`），统一转换 JSON 响应；
- **Router 层禁 try/except、禁 raise HTTPException、禁直接操作 ORM**
  ——`scripts/verify_architecture.py` 机械执法（T6 后含 miniapp_router）。

## 二、错误响应形状（显式声明——F-M2 `[object Object]` 事故的根因规范）

**形状 A：BusinessException（业务异常）**

```text
{"detail": "<中文消息>", "error_code"?: "<结构化码>"}
```

**形状 B：Pydantic 参数校验失败（FastAPI 框架默认，422）**

```text
{"detail": [ {"loc": ["body", "field"], "msg": "<英文消息>", "type": "<类型>"}, ... ]}
```

⚠️ **detail 有两种形状：字符串（形状 A）或数组（形状 B）——前端处理 422 必须兼容
两者**（数组取首条 `msg`）。F-M2 事故根因即形状 B 从未被声明，小程序 toast 渲染
`[object Object]`。双端规范：
- admin-web：`src/api/client.ts` 已修（FormData Content-Type + 422 detail 数组取首条）
- miniapp：`utils/request.js` 已修（同款，T26/F-M2）

**新增校验的原则**：能用业务语义表达的一律在 Service 层抛 `ValidationError`（形状 A，
中文消息家长/馆员可直接阅读）；形状 B 是 Pydantic 类型层的兜底，消息为英文，不面向用户展示。

## 三、error_code 结构化码机制

- 通道：`BusinessException(message, code, error_code=...)` → 响应多一个 `error_code` 字段，
  前端据此触发专用交互（区别于纯文案 toast）。
- **现状实测（2026-09-03）**：机制就绪、**全库零实际 raise 点**。注释中的
  `consent_required`（隐私协议未同意场景）为预定用例，尚未接线。
- **纪律：新增结构化码必须先在本节登记**（码名 + 触发场景 + 前端预期行为），
  禁止直接裸写——错误码是前后端契约，登记即生效。

## 四、命名与路径约定（实测提取）

- 字段命名：`snake_case`（Pydantic model → JSON 全链一致；schema.d.ts 生成同款）。
- 路径前缀：`/api/admin/*`（管理端，`require_perm`/`require_super_admin` 声明式鉴权）
  vs `/api/miniapp/*`（家长端，`get_current_parent` / query token 传图/音频组件）。
- 分页参数统一口径（实测：admin/billing、admin、catalog router 均为）：

```text
page: int = Query(1, ge=1); page_size: int = Query(20, ge=1, le=100)
响应：PaginatedResponse{items, total, page, page_size}
```

  ⚠️ miniapp 侧（reading/miniapp_router L57-58）为裸默认值（`page: int = 1`，无 Query 约束）
  ——不一致项已登记，归 T29 sweep 处置。
- 媒体流（音频/封面/报告图）：query token 传鉴权（`?token=`），组件无法带
  Authorization 头的历史约束；归属校验见 P0-F1/T25。
- **媒体消费点纪律（2026-09-09 增补，封面三断链教训）**：新增媒体字段（cover_url 类）
  的全部消费端（管理端 `<img>`/小程序 `<image>` 各页面）必须逐一接 token 拼 URL——
  miniapp 走 `media.fullUrl()`（utils/media.js），admin-web 走对应 `xxxUrl()` helper 拼
  query token。交付时附消费点清单（错误记忆库锚定）。
- **2026-09 增补端点族**（契约快照执法，改动必须走 contract-change 两步显形）：
  `GET /api/miniapp/books/{id}/audio-permission`（播放入口预检，与 audio 流共享 guards
  判定）；`GET /api/miniapp/quiz/status-batch`（书架角标批量，IN 查询禁 N+1）；
  `GET /api/miniapp/activities/carousel`（首页轮播，有封面优先）；
  admin 活动 CRUD 扩展（detail/update/cover 上传——activity_type 禁改、名额下限 422）。
- **2026-09-12 增补端点族（WM15-A/B + fix33，路径与参数均为代码实取）**：
  `GET /api/miniapp/circle/posts`（`child_id` 必传 → `liked_by_me` 随当前孩子；`likers[].level`；
  帖 1 的 `likers` 含**合成的馆长条目** `{child_id:0, level:"GM", is_admin:true}`）；
  `POST|DELETE /api/miniapp/circle/posts/{post_id}/like`（**破坏性变更**：`child_id` 必传，
  缺失 → 422「请先选择孩子」，Pydantic 层 `child_required`；唯一索引 `(post_id, child_id)`，兄弟可各赞各的）；
  `GET /api/miniapp/circle/children/{child_id}/profile`（孩子名片页，隐私红线：无中文名/家长名/手机号/生日，
  只回 `is_birthday` 布尔）；`GET /api/miniapp/circle/children/{child_id}/poster`（分享海报，
  **`image/jpeg`**，PNG 723KB → JPEG ~57KB，覆盖写并清旧 `.png`）；
  `GET /api/miniapp/circle/posts/{post_id}/thumb|image`（双规格媒体，走 `?token=` 拼装）；
  `PUT /api/miniapp/children/{child_id}/avatar`（内置头像白名单 `AVATAR_IDS`，空串=清空回默认；
  馆方专属 `gm_*` 资产**不在**白名单）；
  `GET /api/miniapp/notifications?scene=circle.liked`（场景过滤 + 深链数据面 `ref_type`/`ref_id`/`actor_name`；
  `ref_type=circle_admin` 标识馆长亲赞）。

## 五、契约工作流（改接口三步链，缺一步 gate 红或前端断）

1. **改后端**（schema/端点/响应模型）；
2. `cd admin-web && pnpm gen:api`（重生成 schema.d.ts，前端类型同步）+
   `python scripts/export_openapi.py`（更新契约快照，**单独 commit**，
   message 带 `contract-change:` 前缀 + 变更端点列表）；
3. 前端适配调用点 + tsc 绿。

漏第 2 步 → gate [8] diff 红（T27）；漏第 3 步 → tsc 红。破坏性变更（删字段/改类型/
删端点）在两步 commit 里显形，审查一眼可见。

---

*源实测文件：backend/common/exceptions.py、backend/main.py:42、backend/domain/{billing,admin,catalog}/router.py、
backend/domain/reading/miniapp_router.py；实测日期 2026-09-03。*
