import { BookFilled, LockOutlined, UserOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, Typography, App as AntdApp } from "antd";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../auth";
import { BRAND } from "../branding";
import { useAdminTheme } from "../theme/ThemeProvider";

export default function Login() {
  const { login } = useAuth();
  const { themeId } = useAdminTheme();
  const isPaint = themeId === "paint";
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm();

  const onFinish = async (values: { username: string; password: string }) => {
    try {
      await login(values.username, values.password);
      message.success("登录成功");
      navigate("/", { replace: true });
    } catch (e) {
      message.error(e instanceof Error ? e.message : "登录失败");
    }
  };

  // 企业风登录页（第一百六十四批）：中性底 + 单张白卡，去掉黄色书本大图标与手绘字体。
  // 颜色/圆角/阴影全部走主题变量，换主题时登录页自动跟随。
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--color-bg-layout)",
        padding: 16,
      }}
    >
      <Card
        style={{ width: 360, boxShadow: "var(--shadow-lg)" }}
        styles={{ body: { padding: 32 } }}
      >
        <div style={{ marginBottom: 24, textAlign: isPaint ? "center" : "left" }}>
          {isPaint && (
            // 绘本风的登录页：黄色书本大图标（旧版同款），标题用手绘体
            <div style={{ marginBottom: 8 }}>
              <BookFilled style={{ fontSize: 40, color: "var(--color-warning)" }} />
            </div>
          )}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              justifyContent: isPaint ? "center" : "flex-start",
            }}
          >
            {!isPaint && (
              <span
                aria-hidden
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 32,
                  height: 32,
                  borderRadius: "var(--radius-base)",
                  background: "var(--color-primary)",
                  color: "var(--color-on-primary)",
                  fontSize: 16,
                  fontWeight: 600,
                }}
              >
                {BRAND.mark}
              </span>
            )}
            <Typography.Title
              level={4}
              style={{
                margin: 0,
                fontSize: 20,
                fontFamily: isPaint ? "var(--font-heading)" : undefined,
              }}
            >
              {BRAND.title}
            </Typography.Title>
          </div>
          <Typography.Text type="secondary" style={{ display: "block", marginTop: 8, fontSize: 13 }}>
            {BRAND.loginSubtitle}
          </Typography.Text>
        </div>
        <Form form={form} layout="vertical" onFinish={onFinish} autoComplete="on" requiredMark={false}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input
              placeholder="请输入用户名"
              prefix={<UserOutlined style={{ color: "var(--color-text-quaternary)" }} />}
              autoComplete="username"
            />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password
              placeholder="请输入密码"
              prefix={<LockOutlined style={{ color: "var(--color-text-quaternary)" }} />}
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, marginTop: 4 }}>
            <Button type="primary" htmlType="submit" block>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
