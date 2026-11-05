// admin-web/src/pageTitles.ts — 路由 → 页面名（面包屑 / document.title / h1 同源）
//
// 170 批从 Layout.tsx 抽出：动态路由（/books/:id）必须显式映射，
// 否则 h1 会回退成品牌名。纯函数便于单测（tests/pageTitle.test.ts）。

export const PAGE_TITLES: Record<string, string> = {
  "/": "仪表盘",
  "/books": "图书管理",
  "/members": "会员管理",
  "/deposits": "押金与赔偿",
  "/circulation": "借阅操作台",
  "/reservations": "预约管理",
  "/growth": "成长与测验",
  "/activities": "线下活动",
  "/refund-center": "退款中心",
  "/circle": "阅读圈",
  "/notifications": "通知中心",
  "/tasks": "任务看板",
  "/staff": "员工管理",
  "/configs": "系统配置",
  "/audit-logs": "审计日志",
};

/** 当前路径的页面名；动态路由（/books/:id）在这里显式映射。 */
export function pageTitleFor(pathname: string): string | null {
  return pathname.startsWith("/books/") ? "图书详情" : PAGE_TITLES[pathname] ?? null;
}
