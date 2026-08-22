import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";

import {
  apiCancelOrder,
  apiConfirmPayment,
  apiCreateChild,
  apiCreateOrder,
  apiCreateParent,
  apiListChildren,
  apiListOrders,
  type Child,
  type Order,
} from "../api/members";

const MEMBER_LABEL: Record<string, string> = {
  none: "未入会", observation: "观察期", pending_evaluation: "待评估",
  formal: "正式会员", expired: "已过期", withdrawn: "已退会",
};
const MEMBER_COLOR: Record<string, string> = {
  none: "default", observation: "blue", pending_evaluation: "orange",
  formal: "green", expired: "red", withdrawn: "default",
};
const ORDER_TYPE_LABEL: Record<string, string> = {
  first_activity_fee: "首场亲子活动", observation_fee: "观察期会员费",
  formal_fee: "正式年费", activity_fee: "活动费",
  deposit: "押金", deposit_supplement: "押金补缴",
};
const ORDER_STATUS_LABEL: Record<string, string> = {
  pending_payment: "待支付", pending_manual_confirm: "待人工确认",
  paid: "已支付", cancelled: "已取消",
};
const PAY_METHOD_OPTIONS = [
  { value: "scan", label: "微信扫码" },
  { value: "alipay", label: "支付宝" },
  { value: "transfer", label: "对公转账" },
  { value: "card", label: "刷卡" },
  { value: "cash", label: "现金" },
  { value: "wechat", label: "微信线上" },
];

export default function MemberManage() {
  const { message } = AntdApp.useApp();
  const [tab, setTab] = useState("children");
  // 孩子列表
  const [children, setChildren] = useState<Child[]>([]);
  const [childTotal, setChildTotal] = useState(0);
  const [childPage, setChildPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  // 订单列表
  const [orders, setOrders] = useState<Order[]>([]);
  const [orderTotal, setOrderTotal] = useState(0);
  const [orderPage, setOrderPage] = useState(1);
  const [orderStatus, setOrderStatus] = useState<string | undefined>();
  // 弹窗
  const [parentOpen, setParentOpen] = useState(false);
  const [childOpen, setChildOpen] = useState(false);
  const [orderOpen, setOrderOpen] = useState(false);
  const [confirmOrder, setConfirmOrder] = useState<Order | null>(null);
  const [parentForm] = Form.useForm();
  const [childForm] = Form.useForm();
  const [orderForm] = Form.useForm();
  const [confirmForm] = Form.useForm();

  const loadChildren = useCallback(
    (page: number) => {
      setLoading(true);
      apiListChildren({ page, page_size: 15, keyword: keyword || undefined, status: statusFilter })
        .then((r) => { setChildren(r.items ?? []); setChildTotal(r.total); })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [keyword, statusFilter, message]
  );

  const loadOrders = useCallback(
    (page: number) => {
      apiListOrders({ page, page_size: 15, status: orderStatus })
        .then((r) => { setOrders(r.items ?? []); setOrderTotal(r.total); })
        .catch((e: Error) => message.error(e.message));
    },
    [orderStatus, message]
  );

  useEffect(() => { if (tab === "children") loadChildren(childPage); }, [loadChildren, childPage, tab]);
  useEffect(() => { if (tab === "orders") loadOrders(orderPage); }, [loadOrders, orderPage, tab]);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Typography.Title level={4} style={{ fontFamily: "Georgia, 'Songti SC', serif", margin: 0 }}>
          会员管理
        </Typography.Title>
        <Space>
          <Button onClick={() => { childForm.resetFields(); setChildOpen(true); }}>为孩子建档</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { parentForm.resetFields(); setParentOpen(true); }}>
            新建家长账号
          </Button>
        </Space>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 4 }}>
        家长建档 → 添加孩子 → 创建订单 → 确认收款（人工收款路径；微信线上支付在后期模块接入）。
      </Typography.Paragraph>

      <Tabs activeKey={tab} onChange={setTab} items={[
        { key: "children", label: `孩子档案（${childTotal}）` },
        { key: "orders", label: `订单（${orderTotal}）` },
      ]} />

      {tab === "children" && (
        <>
          <Space style={{ marginBottom: 12 }}>
            <Input.Search
              placeholder="孩子姓名 / 英文名 / 家长手机号" allowClear style={{ width: 260 }}
              onSearch={(v) => { setKeyword(v); setChildPage(1); }}
            />
            <Select
              placeholder="会员状态" allowClear style={{ width: 130 }} value={statusFilter}
              onChange={(v) => { setStatusFilter(v); setChildPage(1); }}
              options={Object.entries(MEMBER_LABEL).map(([value, label]) => ({ value, label }))}
            />
          </Space>
          <Table<Child>
            rowKey="id" loading={loading} dataSource={children} size="middle"
            pagination={{ current: childPage, pageSize: 15, total: childTotal, showSizeChanger: false, onChange: setChildPage }}
            columns={[
              { title: "孩子", key: "child", width: 150, render: (_, r) => (
                <div>
                  <div>{r.name}{r.english_name ? `（${r.english_name}）` : ""}</div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.grade || "—"}</Typography.Text>
                </div>
              ) },
              { title: "家长", key: "parent", width: 160, render: (_, r) => (
                <div>
                  <div>{r.parent_name}</div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.parent_phone}</Typography.Text>
                </div>
              ) },
              { title: "会员状态", dataIndex: "member_status", width: 110, render: (s: string) => (
                <Tag color={MEMBER_COLOR[s]}>{MEMBER_LABEL[s] ?? s}</Tag>
              ) },
              { title: "会员到期", dataIndex: "member_expire", width: 120, render: (v) => v ?? "—" },
              { title: "会员开始", dataIndex: "member_start", width: 120, render: (v) => v ?? "—" },
              {
                title: "操作", key: "op", width: 150,
                render: (_, r) => (
                  <Space>
                    <Button type="link" size="small" onClick={() => {
                      orderForm.setFieldsValue({ child_id: r.id, order_type: r.member_status === "none" ? "observation_fee" : "formal_fee" });
                      setOrderOpen(true);
                    }}>创建订单</Button>
                  </Space>
                ),
              },
            ]}
          />
        </>
      )}

      {tab === "orders" && (
        <>
          <Space style={{ marginBottom: 12 }}>
            <Select
              placeholder="订单状态" allowClear style={{ width: 140 }} value={orderStatus}
              onChange={(v) => { setOrderStatus(v); setOrderPage(1); }}
              options={Object.entries(ORDER_STATUS_LABEL).map(([value, label]) => ({ value, label }))}
            />
          </Space>
          <Table<Order>
            rowKey="id" dataSource={orders} size="middle"
            pagination={{ current: orderPage, pageSize: 15, total: orderTotal, showSizeChanger: false, onChange: setOrderPage }}
            columns={[
              { title: "订单号", dataIndex: "order_no", width: 200, render: (v) => <Typography.Text code style={{ fontSize: 12 }}>{v}</Typography.Text> },
              { title: "类型", dataIndex: "order_type", width: 120, render: (t: string) => ORDER_TYPE_LABEL[t] ?? t },
              { title: "孩子", dataIndex: "child_name", width: 90, render: (v) => v ?? "—" },
              { title: "家长", dataIndex: "parent_name", width: 90 },
              { title: "金额", dataIndex: "amount", width: 100, render: (v: string) => <Typography.Text strong>￥{Number(v).toLocaleString()}</Typography.Text> },
              { title: "状态", dataIndex: "status", width: 110, render: (s: string) => (
                <Tag color={s === "paid" ? "green" : s === "cancelled" ? "default" : "orange"}>
                  {ORDER_STATUS_LABEL[s] ?? s}
                </Tag>
              ) },
              { title: "收款方式", dataIndex: "pay_method", width: 100, render: (v) => v ? PAY_METHOD_OPTIONS.find((o) => o.value === v)?.label ?? v : "—" },
              { title: "创建时间", dataIndex: "created_at", width: 170 },
              {
                title: "操作", key: "op", width: 150,
                render: (_, r) => (
                  <Space>
                    {r.status === "pending_manual_confirm" && (
                      <>
                        <Button type="link" size="small" onClick={() => { confirmForm.resetFields(); setConfirmOrder(r); }}>
                          确认收款
                        </Button>
                        <Popconfirm title="确认取消该订单？" onConfirm={async () => { await apiCancelOrder(r.id); message.success("已取消"); loadOrders(orderPage); }}>
                          <Button type="link" size="small" danger>取消</Button>
                        </Popconfirm>
                      </>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        </>
      )}

      {/* 新建家长 */}
      <Modal
        title="新建家长账号" open={parentOpen} okText="创建" cancelText="取消" destroyOnClose
        onOk={async () => {
          const v = await parentForm.validateFields();
          try {
            await apiCreateParent(v);
            message.success(`家长 ${v.name} 创建成功，可继续为孩子建档`);
            setParentOpen(false);
            loadChildren(1);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "创建失败");
          }
        }}
        onCancel={() => setParentOpen(false)}
      >
        <Form form={parentForm} layout="vertical">
          <Form.Item name="name" label="家长姓名" rules={[{ required: true }]}>
            <Input placeholder="如：张女士" />
          </Form.Item>
          <Form.Item name="phone" label="手机号" rules={[{ required: true }, { pattern: /^\d{11}$/, message: "请输入 11 位手机号" }]}>
            <Input placeholder="用于登录与联系" maxLength={11} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 为孩子建档 */}
      <Modal
        title="为孩子建档" open={childOpen} okText="创建" cancelText="取消" destroyOnClose
        onOk={async () => {
          const v = await childForm.validateFields();
          try {
            await apiCreateChild(v.parent_id, {
              name: v.name, english_name: v.english_name || undefined,
              gender: v.gender, grade: v.grade ?? "",
            });
            message.success(`孩子 ${v.name} 建档成功（会员状态：未入会）`);
            setChildOpen(false);
            loadChildren(1);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "创建失败");
          }
        }}
        onCancel={() => setChildOpen(false)}
      >
        <Form form={childForm} layout="vertical">
          <Form.Item name="parent_id" label="所属家长ID" rules={[{ required: true }]} extra="在上方孩子列表中可查看家长的建档信息，也可先在「孩子档案」页搜索家长手机号">
            <Input type="number" placeholder="家长账号 ID（数字）" />
          </Form.Item>
          <Form.Item name="name" label="孩子姓名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="english_name" label="英文名（榜单展示用）">
            <Input />
          </Form.Item>
          <Form.Item name="gender" label="性别">
            <Radio.Group options={[{ value: 1, label: "男" }, { value: 2, label: "女" }]} />
          </Form.Item>
          <Form.Item name="grade" label="年级">
            <Input placeholder="如：一年级" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 创建订单 */}
      <Modal
        title="创建订单" open={orderOpen} okText="创建" cancelText="取消" destroyOnClose
        onOk={async () => {
          const v = await orderForm.validateFields();
          try {
            const order = await apiCreateOrder({ child_id: v.child_id, order_type: v.order_type, remark: v.remark ?? "" });
            message.success(`订单已创建（金额 ￥${Number(order.amount).toLocaleString()}），请到「订单」页确认收款`);
            setOrderOpen(false);
            setTab("orders");
            loadOrders(1);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "创建失败");
          }
        }}
        onCancel={() => setOrderOpen(false)}
      >
        <Form form={orderForm} layout="vertical">
          <Form.Item name="child_id" label="孩子ID" rules={[{ required: true }]}>
            <Input type="number" disabled />
          </Form.Item>
          <Form.Item name="order_type" label="订单类型" rules={[{ required: true }]} extra="年费金额自动判定二孩折扣；观察期/活动费不打折">
            <Select options={[
              { value: "observation_fee", label: "观察期会员费（500 元/月）" },
              { value: "formal_fee", label: "正式年费（6000 元，二孩自动 5400）" },
              { value: "first_activity_fee", label: "首场亲子活动（99 元，每账号一次）" },
            ]} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 确认收款 */}
      <Modal
        title={`确认收款：${confirmOrder ? ORDER_TYPE_LABEL[confirmOrder.order_type] ?? confirmOrder.order_type : ""}`}
        open={confirmOrder !== null} okText="确认收款" cancelText="取消" destroyOnClose
        onOk={async () => {
          if (!confirmOrder) return;
          const v = await confirmForm.validateFields();
          try {
            await apiConfirmPayment(confirmOrder.id, { pay_method: v.pay_method, remark: v.remark ?? "" });
            message.success("收款确认成功，会员权益已开通");
            setConfirmOrder(null);
            loadOrders(orderPage);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "确认失败");
          }
        }}
        onCancel={() => setConfirmOrder(null)}
      >
        {confirmOrder && (
          <Typography.Paragraph>
            金额：<Typography.Text strong style={{ fontSize: 18 }}>￥{Number(confirmOrder.amount).toLocaleString()}</Typography.Text>
            <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
              {confirmOrder.child_name} · {confirmOrder.parent_name}
            </Typography.Text>
          </Typography.Paragraph>
        )}
        <Form form={confirmForm} layout="vertical">
          <Form.Item name="pay_method" label="收款方式" rules={[{ required: true }]}>
            <Select options={PAY_METHOD_OPTIONS} placeholder="选择实际收款方式" />
          </Form.Item>
          <Form.Item name="remark" label="凭证说明（留痕）">
            <Input.TextArea rows={2} placeholder="如：转账截图已存档 / 备注家长姓名" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
