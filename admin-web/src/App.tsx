import { Card, Typography } from "antd";
import { Navigate, Route, Routes } from "react-router-dom";

import { getToken } from "./api/client";
import { AuthProvider, hasPermission, useAuth } from "./auth";
import { routePerm } from "./permissions";
import AuditLog from "./pages/AuditLog";
import BookDetail from "./pages/BookDetail";
import BookManage from "./pages/BookManage";
import CirculationDesk from "./pages/CirculationDesk";
import DepositManage from "./pages/DepositManage";
import ActivityManage from "./pages/ActivityManage";
import GrowthManage from "./pages/GrowthManage";
import CircleManage from "./pages/CircleManage";
import RefundCenter from "./pages/RefundCenter";
import Reservations from "./pages/Reservations";
import MemberManage from "./pages/MemberManage";
import Dashboard from "./pages/Dashboard";
import Layout from "./pages/Layout";
import Login from "./pages/Login";
import Notifications from "./pages/Notifications";
import Staff from "./pages/Staff";
import SystemConfig from "./pages/SystemConfig";
import ServerMonitor from "./pages/ServerMonitor";
import TaskBoard from "./pages/TaskBoard";

/**
 * 路由级权限门（P2-58，2026-10-20 第八轮；B5-8，2026-11-27 收敛为查表）。
 *
 * 侧边栏按权限隐藏菜单，但**直输 URL 仍可达**——`/staff`、`/audit-logs` 这类页面进去就是
 * 一片 403 报错（"能点不能用"回归族）。这里与菜单**同一个判据**（`hasPermission`），
 * 无权限直接渲染说明页（不跳转，避免和"登录过期跳转"打架）。
 *
 * 权限码**只从 `permissions.ts` 的 ROUTE_META 查**（B5-8）——路由侧不再出现内联字面量，
 * 避免"路由/菜单/按钮三处各写一份码"漂移。未登记的路由 = 不做路由级拦截（保持现状可见）；
 * 登记完整性由 tests/unit/test_b5_8_frontend_perms_20261127.py 机械执法。
 */
function RequirePerm({ path, children }: { path: string; children: React.ReactNode }) {
  const { permissions } = useAuth();
  const code = routePerm(path);
  if (code !== undefined && !hasPermission(permissions, code)) {
    return (
      <Card>
        <Typography.Title level={5} style={{ fontFamily: "var(--font-heading)" }}>
          无访问权限
        </Typography.Title>
        <Typography.Text type="secondary">
          该页面需要「{code}」权限；如确需使用请联系超级管理员。
        </Typography.Text>
      </Card>
    );
  }
  return <>{children}</>;
}

/** 路由元素别名：`element={gate("/books", <BookManage />)}`——码在表里，调用处只写路径。 */
function gate(path: string, element: React.ReactNode) {
  return <RequirePerm path={path}>{element}</RequirePerm>;
}

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (!getToken()) return <Navigate to="/login" replace />;
  if (loading || user === null) return null; // token 校验中
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <Protected>
              <Layout />
            </Protected>
          }
        >
          <Route index element={gate("/", <Dashboard />)} />
          <Route path="configs" element={gate("/configs", <SystemConfig />)} />
          <Route path="system-monitor" element={gate("/system-monitor", <ServerMonitor />)} />
          <Route path="audit-logs" element={gate("/audit-logs", <AuditLog />)} />
          <Route path="books" element={gate("/books", <BookManage />)} />
          <Route path="circulation" element={gate("/circulation", <CirculationDesk />)} />
          <Route path="books/:id" element={gate("/books/:id", <BookDetail />)} />
          <Route path="members" element={gate("/members", <MemberManage />)} />
          <Route path="deposits" element={gate("/deposits", <DepositManage />)} />
          <Route path="reservations" element={gate("/reservations", <Reservations />)} />
          <Route path="growth" element={gate("/growth", <GrowthManage />)} />
          <Route path="activities" element={gate("/activities", <ActivityManage />)} />
          <Route path="circle" element={gate("/circle", <CircleManage />)} />
          <Route path="refund-center" element={gate("/refund-center", <RefundCenter />)} />
          <Route path="notifications" element={gate("/notifications", <Notifications />)} />
          <Route path="tasks" element={gate("/tasks", <TaskBoard />)} />
          <Route path="staff" element={gate("/staff", <Staff />)} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
