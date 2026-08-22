import { Navigate, Route, Routes } from "react-router-dom";

import { getToken } from "./api/client";
import { AuthProvider, useAuth } from "./auth";
import AuditLog from "./pages/AuditLog";
import BookDetail from "./pages/BookDetail";
import BookManage from "./pages/BookManage";
import CirculationDesk from "./pages/CirculationDesk";
import DepositManage from "./pages/DepositManage";
import ActivityManage from "./pages/ActivityManage";
import GrowthManage from "./pages/GrowthManage";
import Reservations from "./pages/Reservations";
import MemberManage from "./pages/MemberManage";
import Dashboard from "./pages/Dashboard";
import Layout from "./pages/Layout";
import Login from "./pages/Login";
import SystemConfig from "./pages/SystemConfig";

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
          <Route path="configs" element={<SystemConfig />} />
          <Route path="audit-logs" element={<AuditLog />} />
          <Route path="books" element={<BookManage />} />
          <Route path="circulation" element={<CirculationDesk />} />
          <Route path="books/:id" element={<BookDetail />} />
          <Route path="members" element={<MemberManage />} />
          <Route path="deposits" element={<DepositManage />} />
          <Route path="reservations" element={<Reservations />} />
          <Route path="growth" element={<GrowthManage />} />
          <Route path="activities" element={<ActivityManage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
