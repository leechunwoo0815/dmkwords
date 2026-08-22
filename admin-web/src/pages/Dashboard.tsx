import { Card, Col, Row, Tag, Typography } from "antd";

import { useAuth } from "../auth";

const MODULES = [
  { name: "WM1 平台基座", status: "已交付", color: "green" },
  { name: "WM2 图书资产", status: "待开发", color: "default" },
  { name: "WM3 会员与订单", status: "待开发", color: "default" },
  { name: "WM4 押金与赔偿", status: "待开发", color: "default" },
  { name: "WM5 借阅操作台", status: "待开发", color: "default" },
  { name: "WM6 小程序与阅读链", status: "待开发", color: "default" },
  { name: "WM7 测验与成长", status: "待开发", color: "default" },
  { name: "WM8 榜单报告护照", status: "待开发", color: "default" },
  { name: "WM9 线下活动", status: "待开发", color: "default" },
  { name: "WM10 退款退会转让", status: "待开发", color: "default" },
  { name: "WM11 通知任务看板", status: "待开发", color: "default" },
  { name: "WM12 支付与收尾", status: "待开发", color: "default" },
];

export default function Dashboard() {
  const { user } = useAuth();

  return (
    <>
      <Typography.Title level={4} style={{ fontFamily: "Georgia, 'Songti SC', serif" }}>
        欢迎回来，{user?.display_name || user?.username}
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        各业务模块（图书 / 会员 / 借阅 / 活动…）将按交付顺序陆续出现在左侧菜单。
      </Typography.Paragraph>
      <Row gutter={[12, 12]}>
        {MODULES.map((m) => (
          <Col key={m.name} xs={24} sm={12} lg={6}>
            <Card size="small" styles={{ body: { padding: "12px 16px" } }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <Typography.Text>{m.name}</Typography.Text>
                <Tag color={m.color}>{m.status}</Tag>
              </div>
            </Card>
          </Col>
        ))}
      </Row>
    </>
  );
}
