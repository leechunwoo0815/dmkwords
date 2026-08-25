import { BookFilled } from "@ant-design/icons";
import { Button, Card, Form, Input, Typography, App as AntdApp } from "antd";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../auth";

export default function Login() {
  const { login } = useAuth();
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

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--paint-canvas)", // 暖纸米白
      }}
    >
      <Card
        style={{ width: 380 }}
        styles={{ body: { padding: 32 } }}
      >
        <div style={{ textAlign: "center", marginBottom: 8 }}>
          <BookFilled style={{ fontSize: 40, color: "var(--paint-yellow)" }} />
        </div>
        <Typography.Title
          level={3}
          style={{ textAlign: "center", marginBottom: 4, fontFamily: "var(--font-display)" }}
        >
          DmkWords 管理后台
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: "center", marginBottom: 24 }}>
          少儿英语分级阅读系统
        </Typography.Paragraph>
        <Form form={form} layout="vertical" onFinish={onFinish} autoComplete="off">
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input placeholder="admin / staff01" size="large" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password placeholder="dmkwords123" size="large" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 8, marginTop: 8 }}>
            <Button type="primary" htmlType="submit" block size="large">
              登 录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
