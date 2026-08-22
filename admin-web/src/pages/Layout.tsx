import {
  AuditOutlined,
  BookOutlined,
  DashboardOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { Dropdown, Layout as AntLayout, Menu, Typography } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { hasPermission, useAuth } from "../auth";

const { Sider, Header, Content } = AntLayout;

export default function Layout() {
  const { user, permissions, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const items = [
    { key: "/", icon: <DashboardOutlined />, label: "仪表盘", perm: "dashboard.view" },
    { key: "/books", icon: <BookOutlined />, label: "图书管理", perm: "book.manage" },
    { key: "/configs", icon: <SettingOutlined />, label: "系统配置", perm: "config.view" },
    { key: "/audit-logs", icon: <AuditOutlined />, label: "审计日志", perm: "audit.view" },
  ].filter((item) => hasPermission(permissions, item.perm));

  const selected = location.pathname === "/" ? "/" : `/${location.pathname.split("/")[1] ?? ""}`;

  return (
    <AntLayout style={{ minHeight: "100vh" }}>
      <Sider theme="light" width={208} style={{ borderRight: "1px solid #e4dcc8" }}>
        <div
          style={{
            padding: "20px 16px 16px",
            fontFamily: "Georgia, 'Songti SC', serif",
            fontSize: 18,
            fontWeight: 700,
            color: "#2c4a6e",
          }}
        >
          DmkWords
          <div style={{ fontSize: 12, fontWeight: 400, color: "#6f685a", marginTop: 2 }}>
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
      <AntLayout>
        <Header
          style={{
            background: "#fffefa",
            borderBottom: "1px solid #e4dcc8",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            paddingInline: 24,
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
        <Content style={{ padding: 24 }}>
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  );
}
