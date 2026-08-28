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
} from "@ant-design/icons";
import { Dropdown, Layout as AntLayout, Menu, Typography } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { hasPermission, useAuth } from "../auth";

const { Sider, Header, Content } = AntLayout;

export default function Layout() {
  const { user, permissions, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const iconBgMap: Record<string, string> = {
    "/": "#FCD34D",
    "/books": "#FF6B35",
    "/members": "#60A5FA",
    "/deposits": "#A78BFA",
    "/circulation": "#4ADE80",
    "/reservations": "#F472B6",
    "/growth": "#FCD34D",
    "/activities": "#FF6B35",
    "/refund-center": "#EF4444",
    "/staff": "#60A5FA",
    "/configs": "#6B5B5B",
    "/audit-logs": "#A78BFA",
  };

  const iconColorMap: Record<string, string> = {
    "/": "#3B2F2F",
    "/books": "#FFFFFF",
    "/members": "#FFFFFF",
    "/deposits": "#FFFFFF",
    "/circulation": "#3B2F2F",
    "/reservations": "#FFFFFF",
    "/growth": "#3B2F2F",
    "/activities": "#FFFFFF",
    "/refund-center": "#FFFFFF",
    "/staff": "#FFFFFF",
    "/configs": "#FFFFFF",
    "/audit-logs": "#FFFFFF",
  };

  const items = [
    { key: "/", icon: <DashboardOutlined />, label: "仪表盘", perm: "dashboard.view" },
    { key: "/books", icon: <BookOutlined />, label: "图书管理", perm: "book.manage" },
    { key: "/members", icon: <TeamOutlined />, label: "会员管理", perm: "member.manage" },
    { key: "/deposits", icon: <WalletOutlined />, label: "押金与赔偿", perm: "member.manage" },
    { key: "/circulation", icon: <SwapOutlined />, label: "借阅操作台", perm: "borrow.operate" },
    { key: "/reservations", icon: <PushpinOutlined />, label: "预约管理", perm: "borrow.operate" },
    { key: "/growth", icon: <TrophyOutlined />, label: "成长与测验", perm: "member.manage" },
    { key: "/activities", icon: <CalendarOutlined />, label: "线下活动", perm: "member.manage" },
    { key: "/refund-center", icon: <SafetyCertificateOutlined />, label: "退款中心", perm: "audit.view" },
    { key: "/staff", icon: <UserOutlined />, label: "员工管理", perm: "staff.manage" },
    { key: "/configs", icon: <SettingOutlined />, label: "系统配置", perm: "config.view" },
    { key: "/audit-logs", icon: <AuditOutlined />, label: "审计日志", perm: "audit.view" },
  ]
    .filter((item) => hasPermission(permissions, item.perm))
    .map((item) => ({
      ...item,
      icon: (
        <span
          style={{ background: iconBgMap[item.key], color: iconColorMap[item.key] }}
        >
          {item.icon}
        </span>
      ),
    }));

  const selected = location.pathname === "/" ? "/" : `/${location.pathname.split("/")[1] ?? ""}`;

  return (
    <AntLayout style={{ height: "100vh", overflow: "hidden" }}>
      <Sider className="paint-texture" theme="light" width={240} style={{ borderRight: "2px solid var(--paint-ink)", height: "100vh", position: "sticky", top: 0 }}>
        <div
          style={{
            padding: "22px 18px 18px",
            fontFamily: "var(--font-display)",
            fontSize: 22,
            fontWeight: 800,
            color: "#FF6B35",
            letterSpacing: 1,
          }}
        >
          DmkWords
          <div style={{ fontSize: 13, fontWeight: 600, color: "#6B5B5B", marginTop: 4 }}>
            少儿英语分级阅读
          </div>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={items}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: "none" }}
        />
      </Sider>
      <AntLayout style={{ minWidth: 0, height: "100vh", overflow: "hidden" }}>
        <Header
          style={{
            background: "rgba(255, 253, 247, 0.96)",
            borderBottom: "2px solid #3B2F2F",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            paddingInline: 24,
            flexShrink: 0,
          }}
        >
          <Dropdown
            menu={{
              items: [{ key: "logout", label: "退出登录" }],
              onClick: ({ key }) => {
                if (key === "logout") logout();
              },
            }}
          >
            <Typography.Text style={{ cursor: "pointer" }}>
              {user?.display_name || user?.username}（{user?.role === "superadmin" ? "超级管理员" : "运营专员"}）
            </Typography.Text>
          </Dropdown>
        </Header>
        <Content style={{ padding: 24, minWidth: 0, flex: "1 1 auto", overflow: "auto" }}>
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  );
}
