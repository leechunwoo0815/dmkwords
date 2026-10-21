import { Card, Typography } from "antd";
import { Navigate, Route, Routes } from "react-router-dom";

import { getToken } from "./api/client";
import { AuthProvider, hasPermission, useAuth } from "./auth";
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
import TaskBoard from "./pages/TaskBoard";

/**
 * 路由级权限门（P2-58，2026-10-20 第八轮）。
 *
 * 侧边栏按权限隐藏菜单，但**直输 URL 仍可达**——`/staff`、`/audit-logs` 这类页面进去就是
 * 一片 403 报错（"能点不能用"回归族）。这里与菜单**同一个判据**（`hasPermission`），
 * 无权限直接渲染说明页（不跳转，避免和"登录过期跳转"打架）。
 */
function RequirePerm({ code, children }: { code: string; children: React.ReactNode }) {
  const { permissions } = useAuth();
  if (!hasPermission(permissions, code)) {
    return (
      <Card>
        <Typography.Title level={5} style={{ fontFamily: "var(--font-display)" }}>
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
          <Route index element={<Dashboard />} />
          <Route
            path="configs"
            element={
              <RequirePerm code="config.view">
                <SystemConfig />
              </RequirePerm>
            }
          />
          <Route
            path="audit-logs"
            element={
              <RequirePerm code="audit.view">
                <AuditLog />
              </RequirePerm>
            }
          />
          <Route path="books" element={<BookManage />} />
          <Route path="circulation" element={<CirculationDesk />} />
          <Route path="books/:id" element={<BookDetail />} />
          <Route path="members" element={<MemberManage />} />
          <Route path="deposits" element={<DepositManage />} />
          <Route path="reservations" element={<Reservations />} />
          <Route path="growth" element={<GrowthManage />} />
          <Route path="activities" element={<ActivityManage />} />
          <Route path="circle" element={<CircleManage />} />
          <Route path="refund-center" element={<RefundCenter />} />
          <Route path="notifications" element={<Notifications />} />
          <Route path="tasks" element={<TaskBoard />} />
          <Route
            path="staff"
            element={
              <RequirePerm code="audit.view">
                <Staff />
              </RequirePerm>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
