import dayjs from "dayjs";
import { UploadOutlined } from "@ant-design/icons";
import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
// 活动管理（WM9：发布/取消/报名/签到/退款审核）
import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp, Button, DatePicker, Drawer, Form, Input, InputNumber,
  Modal, Select, Space, Switch, Table, Tabs, Tag, Typography, Upload,
} from "antd";

import {
  activityCoverUrl, apiCancelActivity, apiCreateActivity, apiGetActivityDetail,
  apiListActivities, apiListEnrollments, apiSignin, apiUpdateActivity, apiUploadActivityCover,
  type ActivityItem, type EnrollmentItem,
} from "../api/activities";
import { usePaintPagination } from "../hooks/usePaintPagination";
import { PaintHScrollbar } from "../components/PaintHScrollbar";

const TYPE_OPTIONS = [
  { value: "lecture", label: "宣讲会" },
  { value: "book_club", label: "读书会" },
  { value: "experience_sharing", label: "经验交流会" },
  { value: "award_ceremony", label: "颁奖盛典" },
  { value: "theme_reading", label: "主题阅读活动" },
  { value: "parent_child", label: "亲子活动" },
];

// R9（A4 第三犯修正）：筛选用途与表单用途分离——筛选头插「全部」空串选项
// （A4/B7 显式口径，替 allowClear）；表单创建必选真实类型不动
const TYPE_FILTER_OPTIONS = [{ value: "", label: "全部" }, ...TYPE_OPTIONS];
const STATUS_FILTER_OPTIONS = [
  { value: "", label: "全部" },
  { value: "published", label: "已发布" },
  { value: "cancelled", label: "已取消" },
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
  // T3：列表三件套——搜索 + 类型/状态筛选（用户泛化纪律：列表清单逻辑全项目一致）
  const [keyword, setKeyword] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const activityPg = usePaintPagination();
  const [createOpen, setCreateOpen] = useState(false);
  const [enrollActivity, setEnrollActivity] = useState<ActivityItem | null>(null);
  const [enrollments, setEnrollments] = useState<EnrollmentItem[]>([]);
  const [signinCode, setSigninCode] = useState("");
  const [form] = Form.useForm();

  const load = useCallback(() => {
    setLoading(true);
    apiListActivities({
      status: filterStatus || undefined,
      keyword: keyword.trim() || undefined,
      activity_type: filterType || undefined,
    })
      .then(setActivities)
      .catch((e: Error) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [message, keyword, filterType, filterStatus]);

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

  // T45（FEAT-082）：编辑（复用创建表单；activity_type 禁改 disabled——Q8 批复）
  const [editTarget, setEditTarget] = useState<ActivityItem | null>(null);
  const [editCover, setEditCover] = useState<string | null>(null);
  const openEdit = async (a: ActivityItem) => {
    try {
      const d = await apiGetActivityDetail(a.id);
      setEditTarget(d);
      setEditCover(d.cover_url ? activityCoverUrl(a.id) : null);
      form.setFieldsValue({
        title: d.title, start_at: d.start_at ? dayjs(d.start_at) : undefined,
        location: d.location, max_quota: d.max_quota, fee: Number(d.fee),
        description: d.description, member_only: d.member_only,
        enroll_deadline: d.enroll_deadline ? dayjs(d.enroll_deadline) : undefined,
      });
      setCreateOpen(true);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onCoverUpload = async (file: File) => {
    if (!editTarget) return false;
    try {
      await apiUploadActivityCover(editTarget.id, file);
      setEditCover(activityCoverUrl(editTarget.id) + `&t=${Date.now()}`);
      message.success("封面已上传");
    } catch (e) {
      message.error((e as Error).message);
    }
    return false;
  };

  const onEditSave = async () => {
    if (!editTarget) return;
    const v = await form.validateFields();
    try {
      await apiUpdateActivity(editTarget.id, {
        title: v.title, start_at: v.start_at.toISOString(), location: v.location,
        max_quota: v.max_quota, fee: v.fee ?? 0, description: v.description,
        member_only: v.member_only ?? false,
        enroll_deadline: v.enroll_deadline ? v.enroll_deadline.toISOString() : undefined,
      });
      message.success(editTarget.status === "draft" ? "活动已更新" : "活动已更新（member_only/fee 仅影响新报名）");
      setCreateOpen(false);
      setEditTarget(null);
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

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            // RA（插修 17）：编辑过一次后 editTarget/表单残留→弹窗还是编辑模式；
            // 置 null 回创建模式（title/onOk 三元切换）+表单回 initialValues
            form.resetFields();
            setEditTarget(null);
            setEditCover(null);
            setCreateOpen(true);
          }}
        >
          发布活动
        </Button>
        <Input.Search
          placeholder="输入入场券码签到" style={{ width: 260 }} value={signinCode}
          onChange={(e) => setSigninCode(e.target.value)} onSearch={onSignin}
          enterButton="签到"
        />
        {/* T3：活动列表三件套——搜索+类型/状态筛选（B7 预约先例同款） */}
        <Input.Search
          placeholder="按活动标题搜索" style={{ width: 220 }} allowClear
          onSearch={(v) => setKeyword(v)}
        />
        {/* R9：显式「全部」选项（A4/B7 口径），替 allowClear */}
        <Select
          style={{ width: 140 }} value={filterType}
          onChange={(v) => { setFilterType(v); activityPg.setPage(1); }} options={TYPE_FILTER_OPTIONS}
        />
        <Select
          style={{ width: 120 }} value={filterStatus}
          onChange={(v) => { setFilterStatus(v); activityPg.setPage(1); }} options={STATUS_FILTER_OPTIONS}
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
                          <Button type="link" size="small" onClick={() => openEdit(r)}>编辑</Button>
                          <Button type="link" size="small" onClick={() => openEnrollments(r)}>报名名单</Button>
                          {r.status === "published" && (
                            <Button type="link" size="small" danger onClick={() => onCancelActivity(r)}>取消活动</Button>
                          )}
                        </Space>
                      ),
                    },
                  ]}
                 scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
                <PaintPagination current={activityPg.page} pageSize={activityPg.pageSize} total={activities.length} onChange={activityPg.onChange} />
              </>
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
         scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
      </Drawer>

      <Modal
        title={editTarget ? "编辑活动" : "发布活动"} open={createOpen}
        okText={editTarget ? "保存" : "发布"} cancelText="取消" destroyOnClose
        onOk={editTarget ? onEditSave : onCreate}
        onCancel={() => { setCreateOpen(false); setEditTarget(null); }} width={560}
      >
        {editTarget && (
          <div style={{ marginBottom: 12 }}>
            {/* T45：封面上传/预览（Q8 批复编辑白名单内） */}
            {editCover && (
              <img src={editCover} alt="封面" style={{ width: 120, height: 68, objectFit: "cover", marginRight: 12, borderRadius: 4 }} />
            )}
            <Upload accept="image/*" showUploadList={false} beforeUpload={onCoverUpload}>
              <Button icon={<UploadOutlined />}>上传封面</Button>
            </Upload>
          </div>
        )}
        <Form form={form} layout="vertical" initialValues={{ activity_type: "book_club", fee: 0, member_only: false }}>
          <Form.Item name="title" label="活动名称" rules={[{ required: true }]}>
            <Input placeholder="如：周六英文绘本读书会" />
          </Form.Item>
          <Space size="middle">
            <Form.Item name="activity_type" label="类型" rules={[{ required: true }]}
              extra={editTarget ? "类型创建后不可修改（Q8 批复）" : undefined}>
              <Select options={TYPE_OPTIONS} style={{ width: 150 }} disabled={!!editTarget} />
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
