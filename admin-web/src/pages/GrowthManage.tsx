import PaintEmpty from "../components/PaintEmpty";
// 成长与测验管理（WM7：词数流水/积分明细/测验重置/积分调整/等级重算）
import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp, Button, Descriptions, Drawer, Form, Image, Input, InputNumber,
  Modal, Space, Table, Tabs, Tag, Typography,
} from "antd";

import {
  apiAdjustPoints, apiCheckMilestones, apiGenerateReport, apiGetChildGrowth,
  apiRecalcLevels, apiResetQuizAttempts, type ChildGrowth, type ReportData,
} from "../api/growth";
import { getToken } from "../api/client";
import { apiListChildren, type Child } from "../api/members";

const REASON_LABEL: Record<string, string> = {
  words_convert: "词数折算", quiz_first_pass: "测验首过",
  quiz_full_marks: "测验满分", checkin_7: "连续打卡7天",
  checkin_30: "连续打卡30天", manual_adjust: "人工调整",
};

export default function GrowthManage() {
  const { message, modal } = AntdApp.useApp();
  const [children, setChildren] = useState<Child[]>([]);
  const [keyword, setKeyword] = useState("");
  const [growth, setGrowth] = useState<ChildGrowth | null>(null);
  const [growChild, setGrowChild] = useState<Child | null>(null);
  const [loading, setLoading] = useState(false);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [resetTarget, setResetTarget] = useState<{ book_id: number; title: string } | null>(null);
  const [adjustForm] = Form.useForm();
  const [resetForm] = Form.useForm();
  const [report, setReport] = useState<{ url: string; data: ReportData } | null>(null);
  const [reportUrl, setReportUrl] = useState<string>("");

  const load = useCallback(() => {
    apiListChildren({ page: 1, page_size: 50, keyword: keyword || undefined })
      .then((r) => setChildren(r.items ?? []))
      .catch((e: Error) => message.error(e.message));
  }, [keyword, message]);

  useEffect(() => { load(); }, [load]);

  const openGrowth = (c: Child) => {
    setGrowChild(c);
    setGrowth(null);
    setLoading(true);
    apiGetChildGrowth(c.id)
      .then(setGrowth)
      .catch((e: Error) => message.error(e.message))
      .finally(() => setLoading(false));
  };

  const onAdjust = async () => {
    const v = await adjustForm.validateFields();
    try {
      const r = await apiAdjustPoints(growChild!.id, { points: v.points, reason: v.reason });
      message.success(`已调整，当前积分 ${r.points_total}`);
      setAdjustOpen(false);
      adjustForm.resetFields();
      openGrowth(growChild!);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onReset = async () => {
    const v = await resetForm.validateFields();
    try {
      const r = await apiResetQuizAttempts({
        child_id: growChild!.id, book_id: resetTarget!.book_id, reason: v.reason,
      });
      message.success(`已重置（清除 ${r.cleared} 次记录，恢复 ${r.attempts_left} 次机会）`);
      setResetTarget(null);
      resetForm.resetFields();
      openGrowth(growChild!);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onGenerateReport = async (kind: "weekly" | "monthly") => {
    try {
      const r = await apiGenerateReport(growChild!.id, kind);
      // uploads 需鉴权头，img 标签带不了 → blob + objectURL
      const token = getToken();
      const res = await fetch(r.url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!res.ok) throw new Error("报告图片加载失败");
      const blob = await res.blob();
      if (reportUrl) URL.revokeObjectURL(reportUrl);
      const url = URL.createObjectURL(blob);
      setReportUrl(url);
      setReport({ url, data: r.data });
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onRecalc = () => {
    modal.confirm({
      title: "等级阈值变更重算",
      content: "按当前 level_up_books 配置全量重算等级与里程碑（只升不降，幂等）。",
      okText: "开始重算",
      onOk: async () => {
        try {
          const r = await apiRecalcLevels();
          message.success(`重算完成：${r.states} 个孩子，升级 ${r.level_changed} 人，新发里程碑 ${r.milestone_new} 个`);
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  const onCheckMilestones = () => {
    modal.confirm({
      title: "里程碑补发核对",
      content: `按当前节点配置为 ${growChild?.name} 核对补发（已达成未发放的会立即补发）。`,
      okText: "开始核对",
      onOk: async () => {
        try {
          const r = await apiCheckMilestones(growChild!.id);
          message.success(r.new_nodes.length ? `补发节点：${r.new_nodes.join(", ")}` : "无需补发");
          openGrowth(growChild!);
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Input.Search
          placeholder="孩子姓名 / 家长手机号" allowClear style={{ width: 260 }}
          onSearch={(v) => { setKeyword(v); }}
        />
        <Button onClick={onRecalc}>等级阈值重算</Button>
        <Typography.Text type="secondary">
          测验次数重置仅超管可用；所有调整必填原因留痕。
        </Typography.Text>
      </Space>

      <Table<Child> locale={{ emptyText: <PaintEmpty character="rabbit" /> }}
        rowKey="id" dataSource={children} size="middle"
        pagination={{ pageSize: 15, showSizeChanger: false }}
        columns={[
          { title: "孩子", dataIndex: "name", width: 110, render: (_, r) => `${r.name}${r.english_name ? `（${r.english_name}）` : ""}` },
          { title: "家长", key: "p", width: 160, render: (_, r) => `${r.parent_name} ${r.parent_phone}` },
          { title: "会员状态", dataIndex: "member_status", width: 100 },
          {
            title: "操作", key: "op", width: 120, render: (_, r) => (
              <Button type="link" size="small" onClick={() => openGrowth(r)}>成长档案</Button>
            ),
          },
        ]}
      />

      <Drawer
        title={growChild ? `${growChild.name} 的成长档案` : "成长档案"}
        width={640} open={!!growChild}
        onClose={() => { setGrowChild(null); setGrowth(null); }}
      >
        {loading && <Typography.Text type="secondary">加载中…</Typography.Text>}
        {growth && (
          <>
            <Descriptions column={3} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="等级">{growth.summary.level} 级</Descriptions.Item>
              <Descriptions.Item label="已读完">{growth.summary.books_total} 本</Descriptions.Item>
              <Descriptions.Item label="本级进度">{growth.summary.progress_in_level}/{growth.summary.level_books_threshold}</Descriptions.Item>
              <Descriptions.Item label="有效词数">{growth.summary.words_total.toLocaleString()}</Descriptions.Item>
              <Descriptions.Item label="积分">{growth.summary.points_total}</Descriptions.Item>
              <Descriptions.Item label="零头池">{growth.summary.words_remainder} 词</Descriptions.Item>
              <Descriptions.Item label="里程碑" span={3}>
                {growth.summary.milestones_awarded.length
                  ? growth.summary.milestones_awarded.map((n) => (
                    <Tag key={n} color="gold">{n.toLocaleString()} 词</Tag>
                  ))
                  : "暂无"}
              </Descriptions.Item>
            </Descriptions>
            <Space style={{ marginBottom: 16 }} wrap>
              <Button size="small" onClick={() => setAdjustOpen(true)}>积分人工调整</Button>
              <Button size="small" onClick={onCheckMilestones}>里程碑核对补发</Button>
              <Button size="small" onClick={() => onGenerateReport("weekly")}>生成周报图片</Button>
              <Button size="small" onClick={() => onGenerateReport("monthly")}>生成月报图片</Button>
            </Space>

            <Tabs
              items={[
                {
                  key: "words", label: `词数流水（${growth.words_ledger.length}）`,
                  children: (
                    <Table locale={{ emptyText: <PaintEmpty character="rabbit" /> }}
                      rowKey="id" size="small" dataSource={growth.words_ledger}
                      pagination={false}
                      columns={[
                        { title: "书名", dataIndex: "title" },
                        { title: "词数", dataIndex: "word_count", width: 100, render: (v) => <Typography.Text strong>+{v.toLocaleString()}</Typography.Text> },
                        { title: "入账时间", dataIndex: "created_at", width: 170 },
                      ]}
                    />
                  ),
                },
                {
                  key: "points", label: `积分明细（${growth.points_ledger.length}）`,
                  children: (
                    <Table locale={{ emptyText: <PaintEmpty character="rabbit" /> }}
                      rowKey="id" size="small" dataSource={growth.points_ledger}
                      pagination={false}
                      columns={[
                        { title: "类型", dataIndex: "reason_type", width: 110, render: (t) => REASON_LABEL[t] ?? t },
                        { title: "说明", dataIndex: "detail" },
                        { title: "积分", dataIndex: "points", width: 80, render: (v) => <Typography.Text strong style={{ color: "var(--paint-secondary)" }}>+{v}</Typography.Text> },
                        { title: "时间", dataIndex: "created_at", width: 170 },
                      ]}
                    />
                  ),
                },
                {
                  key: "quiz", label: `测验记录（${growth.quiz_overview.length}）`,
                  children: (
                    <Table locale={{ emptyText: <PaintEmpty character="rabbit" /> }}
                      rowKey="book_id" size="small" dataSource={growth.quiz_overview}
                      pagination={false}
                      columns={[
                        { title: "书名", dataIndex: "title" },
                        { title: "最高分", dataIndex: "best_score", width: 90, render: (_, r) => `${r.best_score}/${r.max_attempts > 0 ? 5 : "?"}` },
                        { title: "已用次数", dataIndex: "attempts_used", width: 90, render: (_, r) => `${r.attempts_used}/${r.max_attempts}` },
                        { title: "状态", key: "s", width: 90, render: (_, r) => r.passed ? <Tag color="green">已通过</Tag> : r.attempts_used >= r.max_attempts ? <Tag color="red">已用完</Tag> : <Tag>进行中</Tag> },
                        {
                          title: "操作", key: "op", width: 110, render: (_, r) => (
                            <Button type="link" size="small" onClick={() => {
                              setResetTarget({ book_id: r.book_id, title: r.title });
                              resetForm.resetFields();
                            }}>重置次数</Button>
                          ),
                        },
                      ]}
                    />
                  ),
                },
              ]}
            />
          </>
        )}
      </Drawer>

      <Modal
        title={report ? `${report.data.child_name} 的${report.data.kind === "weekly" ? "周报" : "月报"}` : "报告"}
        open={!!report} footer={null} width={480}
        onCancel={() => setReport(null)}
      >
        {report && (
          <>
            <Typography.Paragraph type="secondary">
              {report.data.period_label} · 读 {report.data.books} 本 · {report.data.words} 词 · 打卡 {report.data.checkin_days} 天
            </Typography.Paragraph>
            <Image src={report.url} width="100%" />
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              家长端小程序「报告」入口也可查看与保存。
            </Typography.Paragraph>
          </>
        )}
      </Modal>

      <Modal
        title="积分人工调整" open={adjustOpen} okText="确认调整" cancelText="取消" destroyOnClose
        onOk={onAdjust} onCancel={() => setAdjustOpen(false)}
      >
        <Form form={adjustForm} layout="vertical">
          <Form.Item name="points" label="调整积分（正数=加）" rules={[{ required: true }]}>
            <InputNumber style={{ width: "100%" }} precision={0} />
          </Form.Item>
          <Form.Item name="reason" label="原因（必填，留痕）" rules={[{ required: true }]}>
            <Input.TextArea rows={2} placeholder="如：线下活动奖励" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={resetTarget ? `重置《${resetTarget.title}》测验次数` : ""}
        open={!!resetTarget} okText="确认重置" cancelText="取消" destroyOnClose
        onOk={onReset} onCancel={() => setResetTarget(null)}
      >
        <Typography.Paragraph type="secondary">
          将清除该书历史提交记录并恢复 3 次机会（成绩不可代标，只能孩子重测）；词数与已发积分不受影响。
        </Typography.Paragraph>
        <Form form={resetForm} layout="vertical">
          <Form.Item name="reason" label="重置原因（必填，留痕）" rules={[{ required: true }]}>
            <Input.TextArea rows={2} placeholder="如：孩子当时生病状态差" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
