import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
// 活动管理（WM9：发布/取消/报名/签到/退款审核）
import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp, Button, DatePicker, Drawer, Form, Input, InputNumber,
  Modal, Select, Space, Switch, Table, Tabs, Tag, Typography,
} from "antd";

import {
  apiCancelActivity, apiCreateActivity, apiListActivities, apiListActivityRefunds,
  apiListEnrollments, apiReviewActivityRefund, apiSignin,
  type ActivityItem, type EnrollmentItem,
} from "../api/activities";
import { usePaintPagination } from "../hooks/usePaintPagination";
import { TODO_REFRESH_EVENT } from "../hooks/useTodoCounts";

const TYPE_OPTIONS = [
  { value: "lecture", label: "宣讲会" },
  { value: "book_club", label: "读书会" },
  { value: "experience_sharing", label: "经验交流会" },
  { value: "award_ceremony", label: "颁奖盛典" },
  { value: "theme_reading", label: "主题阅读活动" },
  { value: "parent_child", label: "亲子活动" },
];

const STATUS_LABEL: Record<string, string> = {
  pending_payment: "待收款", enrolled: "已报名", checked_in: "已签到",
  cancelled: "已取消", refund_pending: "退款待审", refunded: "已退款",
};
const STATUS_COLOR: Record<string, string> = {
  pending_payment: "orange", enrolled: "blue", checked_in: "green",
  cancelled: "default", refund_pending: "red", refunded: "default",
};

export default function ActivityManage() {
  const { message, modal } = AntdApp.useApp();
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const activityPg = usePaintPagination();
  const [createOpen, setCreateOpen] = useState(false);
  const [enrollActivity, setEnrollActivity] = useState<ActivityItem | null>(null);
  const [enrollments, setEnrollments] = useState<EnrollmentItem[]>([]);
  const [refunds, setRefunds] = useState<EnrollmentItem[]>([]);
  const [signinCode, setSigninCode] = useState("");
  const [form] = Form.useForm();

  const load = useCallback(() => {
    setLoading(true);
    apiListActivities()
      .then(setActivities)
      .catch((e: Error) => message.error(e.message))
      .finally(() => setLoading(false));
    apiListActivityRefunds().then(setRefunds).catch(() => setRefunds([]));
  }, [message]);

  useEffect(() => { load(); }, [load]);

  const onCreate = async () => {
    const v = await form.validateFields();
    try {
      await apiCreateActivity({
        title: v.title, activity_type: v.activity_type,
        start_at: v.start_at.toISOString(), location: v.location,
        max_quota: v.max_quota, fee: v.fee ?? 0,
        description: v.description, member_only: v.member_only ?? false,
        enroll_deadline: v.enroll_deadline ? v.enroll_deadline.toISOString() : undefined,
      });
      message.success("活动已发布");
      setCreateOpen(false);
      form.resetFields();
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onCancelActivity = (a: ActivityItem) => {
    modal.confirm({
      title: "取消整场活动",
      content: "已付款且未签到的家庭将转为「退款待审」，由管理员逐单审核。",
      okText: "确认取消活动", okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const r = await apiCancelActivity(a.id);
          message.success(`已取消：${r.refund_pending} 笔转退款待审，${r.cancelled} 笔待支付作废`);
          load();
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  const openEnrollments = (a: ActivityItem) => {
    setEnrollActivity(a);
    setEnrollments([]);
    apiListEnrollments(a.id)
      .then(setEnrollments)
      .catch((e: Error) => message.error(e.message));
  };

  const onSignin = async () => {
    const code = signinCode.trim().toUpperCase();
    if (!code) return;
    try {
      const r = await apiSignin(code);
      message.success(`签到成功（${r.checked_in_at.replace("T", " ").slice(0, 19)}）`);
      setSigninCode("");
      if (enrollActivity) openEnrollments(enrollActivity);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onReview = (r: EnrollmentItem, approve: boolean) => {
    modal.confirm({
      title: approve ? "通过退款" : "拒绝退款",
      content: `${r.child_name} · ${r.activity_title} · ￥${r.amount}${approve ? "（名额将释放，订单转已退款）" : "（报名恢复为已报名）"}`,
      okText: approve ? "通过" : "拒绝",
      onOk: async () => {
        try {
          await apiReviewActivityRefund(r.enrollment_id ?? r.id ?? 0, approve, "");
          message.success("已处理");
          // WM13 L3：审核完成主动刷新待办徽标/待办卡
          window.dispatchEvent(new Event(TODO_REFRESH_EVENT));
          load();
          if (enrollActivity) openEnrollments(enrollActivity);
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" onClick={() => setCreateOpen(true)}>发布活动</Button>
        <Input.Search
          placeholder="输入入场券码签到" style={{ width: 260 }} value={signinCode}
          onChange={(e) => setSigninCode(e.target.value)} onSearch={onSignin}
          enterButton="签到"
        />
      </Space>

      <Tabs
        items={[
          {
            key: "activities", label: `活动列表（${activities.length}）`,
            children: (
              <>
                <Table<ActivityItem> locale={{ emptyText: <PaintEmpty character="star" /> }}
                  rowKey="id" loading={loading} dataSource={activities.slice((activityPg.page - 1) * activityPg.pageSize, activityPg.page * activityPg.pageSize)} size="middle"
                  pagination={false}
                  columns={[
                    { title: "活动", dataIndex: "title", width: 200 },
                    { title: "类型", dataIndex: "activity_type", width: 110, render: (t) => TYPE_OPTIONS.find((o) => o.value === t)?.label ?? t },
                    { title: "开始时间", dataIndex: "start_at", width: 170, render: (v) => v.replace("T", " ").slice(0, 16) },
                    { title: "地点", dataIndex: "location", width: 130 },
                    { title: "费用", dataIndex: "fee_display", width: 90 },
                    {
                      title: "名额", key: "quota", width: 110, render: (_, r) => (
                        <span>
                          {r.quota_used}/{r.max_quota}
                          {r.full ? <Tag color="red" style={{ marginLeft: 6 }}>满</Tag> : null}
                        </span>
                      ),
                    },
                    {
                      title: "状态", dataIndex: "status", width: 90, render: (s) => (
                        <Tag color={s === "published" ? "green" : s === "cancelled" ? "default" : "blue"}>
                          {s === "published" ? "报名中" : s === "cancelled" ? "已取消" : "已结束"}
                        </Tag>
                      ),
                    },
                    {
                      title: "操作", key: "op", width: 180, render: (_, r) => (
                        <Space>
                          <Button type="link" size="small" onClick={() => openEnrollments(r)}>报名名单</Button>
                          {r.status === "published" && (
                            <Button type="link" size="small" danger onClick={() => onCancelActivity(r)}>取消活动</Button>
                          )}
                        </Space>
                      ),
                    },
                  ]}
                />
                <PaintPagination current={activityPg.page} pageSize={activityPg.pageSize} total={activities.length} onChange={activityPg.onChange} />
              </>
            ),
          },
          {
            key: "refunds", label: `退款待审（${refunds.length}）`,
            children: (
              <Table<EnrollmentItem> locale={{ emptyText: <PaintEmpty character="star" /> }}
                rowKey={(r) => String(r.enrollment_id ?? r.id)} dataSource={refunds} size="middle"
                pagination={false}
                columns={[
                  { title: "活动", dataIndex: "activity_title", width: 180 },
                  { title: "孩子", dataIndex: "child_name", width: 90 },
                  { title: "金额", dataIndex: "amount", width: 90, render: (v) => `￥${v}` },
                  { title: "原因", dataIndex: "reason" },
                  { title: "申请时间", dataIndex: "created_at", width: 170, render: (v) => v.replace("T", " ").slice(0, 19) },
                  {
                    title: "操作", key: "op", width: 150, render: (_, r) => (
                      <Space>
                        <Button type="primary" size="small" onClick={() => onReview(r, true)}>通过</Button>
                        <Button size="small" onClick={() => onReview(r, false)}>拒绝</Button>
                      </Space>
                    ),
                  },
                ]}
              />
            ),
          },
        ]}
      />

      <Drawer
        title={enrollActivity ? `《${enrollActivity.title}》报名名单` : ""}
        width={640} open={!!enrollActivity}
        onClose={() => setEnrollActivity(null)}
      >
        <Table<EnrollmentItem> locale={{ emptyText: <PaintEmpty character="star" /> }}
          rowKey="id" dataSource={enrollments} size="small" pagination={false}
          columns={[
            { title: "孩子", dataIndex: "child_name", width: 90 },
            { title: "券码", dataIndex: "ticket_code", width: 160, render: (v) => <Typography.Text code style={{ fontSize: 12 }}>{v}</Typography.Text> },
            { title: "状态", dataIndex: "status", width: 100, render: (s) => <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] ?? s}</Tag> },
            { title: "签到时间", dataIndex: "checked_in_at", width: 160, render: (v) => v ? v.replace("T", " ").slice(0, 19) : "—" },
            { title: "报名时间", dataIndex: "created_at", render: (v) => v.replace("T", " ").slice(0, 19) },
          ]}
        />
      </Drawer>

      <Modal
        title="发布活动" open={createOpen} okText="发布" cancelText="取消" destroyOnClose
        onOk={onCreate} onCancel={() => setCreateOpen(false)} width={560}
      >
        <Form form={form} layout="vertical" initialValues={{ activity_type: "book_club", fee: 0, member_only: false }}>
          <Form.Item name="title" label="活动名称" rules={[{ required: true }]}>
            <Input placeholder="如：周六英文绘本读书会" />
          </Form.Item>
          <Space size="middle">
            <Form.Item name="activity_type" label="类型" rules={[{ required: true }]}>
              <Select options={TYPE_OPTIONS} style={{ width: 150 }} />
            </Form.Item>
            <Form.Item name="start_at" label="开始时间" rules={[{ required: true }]}>
              <DatePicker showTime style={{ width: 200 }} />
            </Form.Item>
          </Space>
          <Form.Item name="location" label="地点">
            <Input placeholder="馆内一层阅读区" />
          </Form.Item>
          <Space size="middle">
            <Form.Item name="max_quota" label="最大报名人数" rules={[{ required: true }]}>
              <InputNumber min={1} style={{ width: 130 }} />
            </Form.Item>
            <Form.Item name="fee" label="费用（0=免费）">
              <InputNumber min={0} style={{ width: 130 }} />
            </Form.Item>
            <Form.Item name="member_only" label="仅限会员" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item name="enroll_deadline" label="报名截止（可选）">
            <DatePicker showTime style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="description" label="活动介绍">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
