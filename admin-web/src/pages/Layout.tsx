import {
  AuditOutlined,
  BookOutlined,
  TeamOutlined,
  SwapOutlined,
  WalletOutlined,
  PushpinOutlined,
  TrophyOutlined,
  CalendarOutlined,
  SafetyCertificateOutlined,
  DashboardOutlined,
  SettingOutlined,
  UserOutlined,
  NotificationOutlined,
  ScheduleOutlined,
  StarOutlined,
  DownOutlined,
  BgColorsOutlined,
} from "@ant-design/icons";
import { useEffect } from "react";
import { Badge, Breadcrumb, Dropdown, Layout as AntLayout, Menu } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { hasPermission, useAuth } from "../auth";
import { useTodoCounts } from "../hooks/useTodoCounts";
import { BRAND } from "../branding";
import TechIcon from "../components/TechIcon";
import type { IconName } from "../components/TechIcon";
import { PAGE_TITLES, pageTitleFor } from "../pageTitles";
import { THEME_LIST, getTheme } from "../theme";
import { useAdminTheme } from "../theme/ThemeProvider";
import { paintIllustration as P } from "../theme/paint";

const { Sider, Header, Content } = AntLayout;

export default function Layout() {
  const { user, permissions, logout } = useAuth();
  const { themeId, setThemeId } = useAdminTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const { counts, failed } = useTodoCounts();

  const isPaint = themeId === "paint";
  // 绘本风的彩色图标圆圈（旧版按路由给每个图标配底色/字色）；企业风不渲染这层
  const iconBgMap: Record<string, string> = {
    "/": P.yellow,
    "/books": P.orange,
    "/members": P.blue,
    "/deposits": P.purple,
    "/circulation": P.green,
    "/reservations": P.pink,
    "/growth": P.yellow,
    "/activities": P.orange,
    "/refund-center": P.red,
    "/staff": P.blue,
    "/configs": P.textSecondary,
    "/audit-logs": P.purple,
    "/notifications": P.pink,
    "/tasks": P.green,
    "/circle": P.yellow,
  };
  const iconColorMap: Record<string, string> = {
    "/": P.ink,
    "/books": P.white,
    "/members": P.white,
    "/deposits": P.white,
    "/circulation": P.ink,
    "/reservations": P.white,
    "/growth": P.ink,
    "/activities": P.white,
    "/refund-center": P.white,
    "/staff": P.white,
    "/configs": P.white,
    "/audit-logs": P.white,
    "/notifications": P.white,
    "/tasks": P.ink,
    "/circle": P.ink,
  };

  // L3 双保险（Q10 裁定）：路由变化也触发待办刷新（审核完成主动 dispatch 之外的兜底）
  useEffect(() => {
    window.dispatchEvent(new Event("wm13-todo-refresh"));
  }, [location.pathname]);

  // WCAG 2.4.2（页面有标题）：此前所有路由共用「DmkWords 管理后台」一个 document.title，
  // 读屏器/浏览器历史/收藏都无法区分页面（2026-10-24 无障碍轴实测）。
  useEffect(() => {
    const name = pageTitleFor(location.pathname);
    document.title = name ? `${name} · ${BRAND.documentSuffix}` : BRAND.documentSuffix;
  }, [location.pathname]);

  // 徽标计数 = 管理待办全部待处理（与通知中心管理待办 tab 同口径）；
  // 拉取失败或未拉到 → 不渲染徽标（U10 禁假 0），count=0 时 Badge 自动隐藏
  // S1 分工表（计数同源第 5 案）：侧边栏"通知中心"徽标=管理待办数（要干活的活），
  // 家长通知未读在通知中心家长 tab 胶囊展示——两数语义各归各，本徽标不含未读
  const badgeCount = failed || !counts ? 0 : counts.admin_total;
  const memberBadge = failed || !counts ? 0 : (counts.order_pending_manual ?? 0); // T20e #3
  // R6（#4）：退款中心徽标=退款待审+转让待审（退会不计——用户口径 20260907）
  const refundCenterBadge =
    failed || !counts ? 0 : (counts.refund_pending ?? 0) + (counts.transfer_pending ?? 0);
  // T6：线下活动侧边栏徽标（活动报名待确认，单独口径不进 admin_total）
  const activityBadge = failed || !counts ? 0 : (counts.activity_enroll_pending ?? 0);
  // WM14-A：阅读圈徽标（今日新帖未馆长赞数——冷启动巡场动线；样式同先例）
  const circleBadge = failed || !counts ? 0 : (counts.circle_unliked ?? 0);

  const rawItems = [
    { key: "/", icon: <DashboardOutlined />, label: "仪表盘", perm: "dashboard.view" },
    { key: "/books", icon: <BookOutlined />, label: "图书管理", perm: "book.manage" },
    {
      key: "/members",
      icon: <TeamOutlined />,
      label:
        failed || memberBadge > 0 ? (
          <Badge count={failed ? "?" : memberBadge} title={failed ? "待审核收款计数加载失败" : undefined} size="small" offset={[10, 0]}>
            会员管理
          </Badge>
        ) : (
          "会员管理"
        ),
      perm: "member.manage",
    },
    { key: "/deposits", icon: <WalletOutlined />, label: "押金与赔偿", perm: "member.manage" },
    { key: "/circulation", icon: <SwapOutlined />, label: "借阅操作台", perm: "borrow.operate" },
    { key: "/reservations", icon: <PushpinOutlined />, label: "预约管理", perm: "borrow.operate" },
    { key: "/growth", icon: <TrophyOutlined />, label: "成长与测验", perm: "member.manage" },
    {
      key: "/activities",
      icon: <CalendarOutlined />,
      perm: "member.manage",
      label:
        failed || activityBadge > 0 ? (
          <Badge count={failed ? "?" : activityBadge} title={failed ? "活动报名待确认计数加载失败" : undefined} size="small" offset={[10, 0]}>
            线下活动
          </Badge>
        ) : (
          "线下活动"
        ),
    },
    {
      key: "/refund-center",
      icon: <SafetyCertificateOutlined />,
      label:
        failed || refundCenterBadge > 0 ? (
          <Badge count={failed ? "?" : refundCenterBadge} title={failed ? "退款/转让待审计数加载失败" : undefined} size="small" offset={[10, 0]}>
            退款中心
          </Badge>
        ) : (
          "退款中心"
        ),
      perm: "audit.view",
    },
    {
      key: "/circle",
      icon: <StarOutlined />,
      perm: "member.manage",
      label:
        failed || circleBadge > 0 ? (
          <Badge count={failed ? "?" : circleBadge} title={failed ? "阅读圈待赞计数加载失败" : undefined} size="small" offset={[10, 0]}>
            阅读圈
          </Badge>
        ) : (
          "阅读圈"
        ),
    },
    {
      key: "/notifications",
      icon: <NotificationOutlined />,
      label:
        failed || badgeCount > 0 ? (
          <Badge count={failed ? "?" : badgeCount} title={failed ? "待办计数加载失败，点击通知中心重试" : undefined} size="small" offset={[10, 0]}>
            通知中心
          </Badge>
        ) : (
          "通知中心"
        ),
      perm: "dashboard.view",
    },
    { key: "/tasks", icon: <ScheduleOutlined />, label: "任务看板", perm: "dashboard.view" },
    // F-L9：staff.manage 是后端权限目录不存在的幽灵码——员工管理=超管专属，
    // 换既有超管专属码 audit.view（STAFF_PERMISSIONS 无此码=staff 永远隐藏，语义不变）
    { key: "/staff", icon: <UserOutlined />, label: "员工管理", perm: "audit.view" },
    { key: "/configs", icon: <SettingOutlined />, label: "系统配置", perm: "config.view" },
    { key: "/audit-logs", icon: <AuditOutlined />, label: "审计日志", perm: "audit.view" },
  ];

  // 菜单分组：15 项平铺像"毛坯"，分组后才有信息架构（Feishu/DingTalk 同款做法）
  const GROUPS: { key: string; label: string; keys: string[]; icon: IconName }[] = [
    { key: "overview", label: "概览", icon: "dashboard", keys: ["/"] },
    { key: "library", label: "馆藏与借阅", icon: "book", keys: ["/books", "/circulation", "/reservations"] },
    { key: "reader", label: "读者与运营", icon: "member", keys: ["/members", "/growth", "/activities", "/circle", "/notifications"] },
    { key: "money", label: "资金与合规", icon: "refund", keys: ["/deposits", "/refund-center", "/audit-logs"] },
    { key: "system", label: "系统", icon: "config", keys: ["/tasks", "/staff", "/configs"] },
  ];

  const allowed = rawItems
    .filter((item) => hasPermission(permissions, item.perm))
    .map((item) =>
      isPaint
        ? {
            ...item,
            icon: (
              <span
                className="paint-menu-ico"
                style={{ background: iconBgMap[item.key], color: iconColorMap[item.key] }}
              >
                {item.icon}
              </span>
            ),
          }
        : item,
    );

  const items = GROUPS.map((g) => ({
    type: "group" as const,
    key: g.key,
    label: (
      <span className="menu-group-label">
        <TechIcon name={g.icon} size={12} />
        {g.label}
      </span>
    ),
    children: allowed.filter((item) => g.keys.includes(item.key)),
  })).filter((g) => g.children.length > 0);

  const selected = location.pathname === "/" ? "/" : `/${location.pathname.split("/")[1] ?? ""}`;
  const siderWidth = getTheme().density.siderWidth;
  const roleLabel = user?.role === "superadmin" ? "超级管理员" : "运营专员";

  // 面包屑：首页 / （所属模块 /）当前页
  const isBookDetail = location.pathname.startsWith("/books/");
  const crumb: string[] = [];
  if (location.pathname !== "/") {
    if (isBookDetail) {
      crumb.push("图书管理", "图书详情");
    } else {
      const name = PAGE_TITLES[location.pathname];
      if (name) crumb.push(name);
    }
  }

  return (
    <AntLayout style={{ height: "100vh", overflow: "hidden" }}>
      <Sider
        className={`paint-sider${isPaint ? " paint-texture" : ""}`}
        theme="light"
        width={siderWidth}
        style={{ height: "100vh", position: "sticky", top: 0, overflow: "hidden" }}
      >
        <div className="paint-brand">
          <div className="paint-brand-name">
            <span className="paint-brand-mark" aria-hidden>
              {BRAND.mark}
            </span>
            {BRAND.name}
          </div>
          <div className="paint-brand-sub">{BRAND.subtitle}</div>
        </div>
        {/* fix29-R1：菜单项随模块增长会超出视口——菜单区独立滚动，否则外层
            overflow:hidden 会把底部菜单（员工管理/系统配置/审计日志）裁掉且无从滚动 */}
        <div className="paint-sider-scroll" style={{ flex: "1 1 auto", minHeight: 0, overflowY: "auto", paddingBottom: 12 }}>
          <Menu
            mode="inline"
            selectedKeys={[selected]}
            items={items}
            onClick={({ key }) => navigate(key)}
            style={{ borderInlineEnd: "none", background: "transparent" }}
          />
        </div>
      </Sider>
      <AntLayout style={{ minWidth: 0, height: "100vh", overflow: "hidden" }}>
        <Header
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 16,
            paddingInline: 20,
            lineHeight: "normal",
            flexShrink: 0,
            borderBottom: "var(--border-width) solid var(--color-border)",
          }}
        >
          {/* 顶栏左侧原来全空（"毛坯"感主要来源之一）：补面包屑，任何页面都知道自己在哪 */}
          <Breadcrumb
            className="paint-breadcrumb"
            items={[{ title: <span onClick={() => navigate("/")}>首页</span> }, ...crumb.map((c) => ({ title: c }))]}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            {/* 主题切换：显式入口（用户反馈只在二级菜单里找不到） */}
            <Dropdown
              trigger={["click"]}
              menu={{
                selectable: true,
                selectedKeys: [`theme:${themeId}`],
                items: THEME_LIST.map((t) => ({ key: `theme:${t.id}`, label: t.name })),
                onClick: ({ key }) => {
                  if (key.startsWith("theme:")) setThemeId(key.slice("theme:".length));
                },
              }}
            >
              <span className="paint-header-user paint-header-theme" title="切换界面主题">
                <BgColorsOutlined style={{ color: "var(--color-text-tertiary)" }} />
                <span>{getTheme(themeId).name}</span>
                <DownOutlined style={{ fontSize: 10, color: "var(--color-text-tertiary)" }} />
              </span>
            </Dropdown>
            <Dropdown
              // 点击展开（antd 默认 hover）：鼠标移开就消失的菜单在触屏/远程桌面上很难点中
              trigger={["click"]}
              menu={{
                selectable: true,
                selectedKeys: [`theme:${themeId}`],
                items: [
                  {
                    key: "theme",
                    label: "界面主题",
                    children: THEME_LIST.map((t) => ({ key: `theme:${t.id}`, label: t.name })),
                  },
                  { type: "divider" as const },
                  { key: "logout", label: "退出登录" },
                ],
                onClick: ({ key }) => {
                  if (key === "logout") {
                    logout();
                    return;
                  }
                  if (key.startsWith("theme:")) setThemeId(key.slice("theme:".length));
                },
              }}
            >
              <span className="paint-header-user">
                <UserOutlined style={{ color: "var(--color-text-tertiary)" }} />
                <span>{user?.display_name || user?.username}</span>
                {/* 显示名与角色名相同时不重复（历史数据里超管的 display_name 就叫「超级管理员」） */}
                {roleLabel !== (user?.display_name || user?.username) && (
                  <span className="paint-header-user-role">{roleLabel}</span>
                )}
                <DownOutlined style={{ fontSize: 10, color: "var(--color-text-tertiary)" }} />
              </span>
            </Dropdown>
          </div>
        </Header>
        <Content
          style={{
            padding: 12,
            minWidth: 0,
            flex: "1 1 auto",
            overflow: "auto",
          }}
        >
          {/* B8（2026-10-28）：无障碍——每页一个语义化 h1（视觉隐藏，不改变布局）。
              此前全仓管理端页面无 h1–h6，读屏软件无法定位页面主题。 */}
          <h1 className="sr-only">{pageTitleFor(location.pathname) ?? BRAND.documentSuffix}</h1>
          {/* 内容面（surface）：页面内容装进一张白纸，而不是直接浮在灰底上——
              这是"成品感"与"毛坯感"最大的差别；卡片再画在这张白纸上。 */}
          <div className="page-surface">
            <Outlet />
          </div>
        </Content>
      </AntLayout>
    </AntLayout>
  );
}
