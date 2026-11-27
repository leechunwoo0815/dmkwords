// 路由级权限元数据（B5-8，2026-11-27）
//
// 为什么单独一张表：同一个"页面归哪个权限管"此前散落在三处——侧边栏 `pages/Layout.tsx` 的
// `perm`、路由守卫 `App.tsx` 的内联 `code`、页面内按钮的 `hasPermission(...)`；改一处漏两处
// 就是"能点不能用 / 直输 URL 一片 403"回归族（P2-58）。现在**路由侧只有这一张表**：
// `App.tsx` 的 `RequirePerm` 只认路由路径，权限码一律从这里查。
//
// 权限码的唯一权威来源是后端 `require_perm(...)` 声明，对账脚本 =
// `scripts/check_rbac_consistency.py`（宪法 §五.2）。本表的码**只能取它认可的既有声明**：
// 测试 `tests/unit/test_b5_8_frontend_perms_20261127.py` 逐条比对后端声明集合，任一条对不上即判红
// ——不允许在这里"猜"一个新码。现状：17 条页面路由全部有据可查（无"拿不准"遗留）；将来若出现
// 拿不准的路由，**不入表**（保持现状可见）并在该测试的 UNGATED 例外里留痕，而不是编号码顶上。
//
// 取码口径（每条都留证，见下方逐条注）：
//   ① 菜单同项 `perm`（既有前端判据，Layout.tsx）；② 该页**加载必需**的主接口后端 `require_perm`。
// 键 = 绝对路由路径（与 Layout 菜单 key 同名，便于逐项对照）。

/** 路由 → 权限码。用数组声明是为了让 `check_rbac_consistency.py` 的 `perm: "..."` 抽取口径
 *  也能看见路由级拦截（脚本只读 `.ts/.tsx`，跳过 schema.d.ts）。 */
export const ROUTE_META: { path: string; perm: string }[] = [
  // 仪表盘：GET /api/admin/dashboard、/dashboard/charts、/todo-counts 后端均 require_perm("dashboard.view")
  { path: "/", perm: "dashboard.view" },
  // 图书管理：catalog 路由全部 require_perm("book.manage")；详情页加载必调 GET /api/admin/books/{id}
  // （同页的题目/音频子面板更严：quiz.manage / audio.manage，属页内按钮级，不抬成路由级）
  { path: "/books", perm: "book.manage" },
  { path: "/books/:id", perm: "book.manage" },
  // 会员/孩子/家长：identity 路由 require_perm("member.manage")
  { path: "/members", perm: "member.manage" },
  // 押金与赔偿：billing 路由 require_perm("member.manage")
  { path: "/deposits", perm: "member.manage" },
  // 借阅操作台：circulation 路由全部 require_perm("borrow.operate")
  { path: "/circulation", perm: "borrow.operate" },
  // 预约管理：reading 路由的预约列表/核销 require_perm("borrow.operate")
  // （页内"孩子阅读档案"弹窗走 member.manage——既有部分权限页，路由门取加载必需的最低码）
  { path: "/reservations", perm: "borrow.operate" },
  // 成长与测验：growth 路由 require_perm("member.manage")
  { path: "/growth", perm: "member.manage" },
  // 线下活动：activity 路由 require_perm("member.manage")（签到端点单挂 borrow.operate，属页内动作）
  { path: "/activities", perm: "member.manage" },
  // 退款中心：后端是 require_super_admin()（没有权限码）；audit.view 是"仅超管"码
  // （SUPER_ADMIN_ONLY_PERMISSIONS），且菜单既有同款口径 → 语义等价于超管专属
  { path: "/refund-center", perm: "audit.view" },
  // 阅读圈：reading_circle 路由 require_perm("member.manage")
  { path: "/circle", perm: "member.manage" },
  // 通知中心：/notifications、/notifications/export 后端 require_perm("dashboard.view")
  { path: "/notifications", perm: "dashboard.view" },
  // 任务看板：/tasks、/tasks/runs、/tasks/{name}/run、/dead-letters 后端均 require_perm("dashboard.view")
  { path: "/tasks", perm: "dashboard.view" },
  // 员工管理：后端无权限码（超管专属）；audit.view = 仅超管码（沿用菜单既有判据，F-L9 已裁定）
  { path: "/staff", perm: "audit.view" },
  // 系统配置 / 服务器监控：/configs、/system/live、/system/history 后端 require_perm("config.view")
  { path: "/configs", perm: "config.view" },
  { path: "/system-monitor", perm: "config.view" },
  // 审计日志：/audit-logs、/audit-logs/export 后端 require_perm("audit.view")
  { path: "/audit-logs", perm: "audit.view" },
];

/** 路由 → 权限码查找表（由 ROUTE_META 派生，单一事实源仍是上面那张表）。 */
export const ROUTE_PERMS: Record<string, string> = Object.fromEntries(
  ROUTE_META.map((row) => [row.path, row.perm]),
);

/** 取某路由的权限码；未登记返回 undefined（= 该页不做路由级拦截）。 */
export function routePerm(path: string): string | undefined {
  return ROUTE_PERMS[path];
}
