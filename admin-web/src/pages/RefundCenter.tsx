// 退款中心（WM10：订单/押金退款 + 退会 + 转让，超管逐单审核）
import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp, Button, Input, Modal, Space, Table, Tabs, Tag, Typography,
} from "antd";

import {
  apiListRefunds, apiListTransfers, apiListWithdrawals,
  apiReviewRefund, apiReviewTransfer, apiReviewWithdrawal,
  type RefundRequestItem, type TransferItem, type WithdrawalItem,
} from "../api/refunds";

const KIND_LABEL: Record<string, string> = { order: "订单退款", deposit: "押金退款" };
const ORDER_TYPE_LABEL: Record<string, string> = {
  observation_fee: "观察期费", formal_fee: "年费",
  first_activity_fee: "首场活动", activity_fee: "活动费",
  deposit: "押金", deposit_supplement: "押金补缴",
};
const STATUS_COLOR: Record<string, string> = {
  pending: "orange", approved: "green", rejected: "red",
  cancelled: "default", expired: "default",
};

export default function RefundCenter() {
  const { message } = AntdApp.useApp();
  const [refunds, setRefunds] = useState<RefundRequestItem[]>([]);
  const [withdrawals, setWithdrawals] = useState<WithdrawalItem[]>([]);
  const [transfers, setTransfers] = useState<TransferItem[]>([]);
  const [remark, setRemark] = useState("");
  const [remarkTarget, setRemarkTarget] = useState<{
    kind: "refund" | "withdrawal" | "transfer";
    id: number;
    approve: boolean;
    title: string;
    content: string;
  } | null>(null);

  const load = useCallback(() => {
    apiListRefunds().then(setRefunds).catch((e: Error) => message.error(e.message));
    apiListWithdrawals().then(setWithdrawals).catch(() => setWithdrawals([]));
    apiListTransfers().then(setTransfers).catch(() => setTransfers([]));
  }, [message]);

  useEffect(() => { load(); }, [load]);

  const askReview = (
    kind: "refund" | "withdrawal" | "transfer", id: number, approve: boolean,
    title: string, content: string,
  ) => {
    setRemark("");
    setRemarkTarget({ kind, id, approve, title, content });
  };

  const doReview = async () => {
    if (!remarkTarget) return;
    const { kind, id, approve } = remarkTarget;
    if (!approve && !remark.trim()) {
      message.warning("拒绝必须填写原因（家长可见）");
      return;
    }
    try {
      if (kind === "refund") await apiReviewRefund(id, approve, remark);
      if (kind === "withdrawal") await apiReviewWithdrawal(id, approve, remark);
      if (kind === "transfer") await apiReviewTransfer(id, approve, remark);
      message.success("已处理");
      setRemarkTarget(null);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const pendingRefunds = refunds.filter((r) => r.status === "pending");
  const pendingWithdrawals = withdrawals.filter((w) => w.status === "pending");
  const pendingTransfers = transfers.filter((t) => t.status === "pending");

  return (
    <div>
      <Typography.Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
        资金安全关口：仅超级管理员可审；拒绝必填原因（家长端可见）；押金退款随退会/转让通过自动发起。
      </Typography.Text>

      <Tabs
        items={[
          {
            key: "refunds",
            label: `退款待审（${pendingRefunds.length}）`,
            children: (
              <Table<RefundRequestItem>
                rowKey="id" dataSource={refunds} size="middle"
                pagination={{ pageSize: 15, showSizeChanger: false }}
                columns={[
                  { title: "类型", dataIndex: "kind", width: 100, render: (k) => KIND_LABEL[k] ?? k },
                  { title: "孩子", dataIndex: "child_name", width: 90 },
                  {
                    title: "关联", key: "rel", width: 190, render: (_, r) => (
                      r.order_no ? (
                        <div>
                          <Typography.Text code style={{ fontSize: 12 }}>{r.order_no}</Typography.Text>
                          <div style={{ fontSize: 12, color: "#6f685a" }}>
                            {ORDER_TYPE_LABEL[r.order_type ?? ""] ?? r.order_type}
                            {r.pay_method ? ` · ${r.pay_method}` : ""}
                          </div>
                        </div>
                      ) : "押金账户"
                    ),
                  },
                  { title: "金额", dataIndex: "amount", width: 100, render: (v) => <Typography.Text strong>￥{Number(v).toLocaleString()}</Typography.Text> },
                  { title: "家长原因", dataIndex: "reason" },
                  { title: "状态", dataIndex: "status", width: 90, render: (s) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
                  { title: "审核备注", dataIndex: "review_remark", width: 150, render: (v) => v ?? "—" },
                  { title: "申请时间", dataIndex: "created_at", width: 165, render: (v) => v.replace("T", " ").slice(0, 19) },
                  {
                    title: "操作", key: "op", width: 140, render: (_, r) => (
                      r.status === "pending" ? (
                        <Space>
                          <Button type="primary" size="small" onClick={() => askReview("refund", r.id, true,
                            "通过退款",
                            `${r.child_name} · ￥${r.amount}（${KIND_LABEL[r.kind]}）`)}>通过</Button>
                          <Button size="small" danger onClick={() => askReview("refund", r.id, false,
                            "拒绝退款",
                            `${r.child_name} · ￥${r.amount}（${KIND_LABEL[r.kind]}）`)}>拒绝</Button>
                        </Space>
                      ) : <span>—</span>
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: "withdrawals",
            label: `退会待审（${pendingWithdrawals.length}）`,
            children: (
              <Table<WithdrawalItem>
                rowKey="id" dataSource={withdrawals} size="middle"
                pagination={{ pageSize: 15, showSizeChanger: false }}
                columns={[
                  { title: "孩子", dataIndex: "child_name", width: 100 },
                  { title: "当前状态", dataIndex: "member_status", width: 100 },
                  { title: "退会原因", dataIndex: "reason" },
                  { title: "状态", dataIndex: "status", width: 90, render: (s) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
                  { title: "审核备注", dataIndex: "review_remark", width: 150, render: (v) => v ?? "—" },
                  { title: "申请时间", dataIndex: "created_at", width: 165, render: (v) => v.replace("T", " ").slice(0, 19) },
                  {
                    title: "操作", key: "op", width: 260, render: (_, r) => (
                      r.status === "pending" ? (
                        <Space>
                          <Button type="primary" size="small" onClick={() => askReview("withdrawal", r.id, true,
                            "通过退会",
                            `${r.child_name}：退会后转为 withdrawn，自动发起押金退款（退可用余额）`)}>通过</Button>
                          <Button size="small" danger onClick={() => askReview("withdrawal", r.id, false,
                            "拒绝退会",
                            `${r.child_name}：拒绝后解锁，家长可再次申请`)}>拒绝</Button>
                        </Space>
                      ) : <span>—</span>
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: "transfers",
            label: `转让待审（${pendingTransfers.length}）`,
            children: (
              <Table<TransferItem>
                rowKey="id" dataSource={transfers} size="middle"
                pagination={{ pageSize: 15, showSizeChanger: false }}
                columns={[
                  { title: "转出方", dataIndex: "source_name", width: 100 },
                  { title: "受让方", dataIndex: "target_name", width: 100 },
                  { title: "状态", dataIndex: "status", width: 90, render: (s) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
                  { title: "审核截止", dataIndex: "expires_at", width: 165, render: (v) => v.replace("T", " ").slice(0, 19) },
                  { title: "审核备注", dataIndex: "review_remark", width: 140, render: (v) => v ?? "—" },
                  { title: "申请时间", dataIndex: "created_at", width: 165, render: (v) => v.replace("T", " ").slice(0, 19) },
                  {
                    title: "操作", key: "op", width: 260, render: (_, r) => (
                      r.status === "pending" ? (
                        <Space>
                          <Button type="primary" size="small" onClick={() => askReview("transfer", r.id, true,
                            "通过转让",
                            `${r.source_name} → ${r.target_name}：转出方退会（年费不退）+ 押金退款自动发起；受让方转正式会员并继承到期日；词数/等级/积分各是各的`)}>通过</Button>
                          <Button size="small" danger onClick={() => askReview("transfer", r.id, false,
                            "拒绝转让",
                            `${r.source_name} → ${r.target_name}：拒绝后双方解锁`)}>拒绝</Button>
                        </Space>
                      ) : <span>—</span>
                    ),
                  },
                ]}
              />
            ),
          },
        ]}
      />

      <Modal
        title={remarkTarget?.title} open={!!remarkTarget}
        okText={remarkTarget?.approve ? "确认通过" : "确认拒绝"}
        cancelText="取消" onOk={doReview} onCancel={() => setRemarkTarget(null)}
      >
        <Typography.Paragraph>{remarkTarget?.content}</Typography.Paragraph>
        <Input.TextArea
          rows={2} value={remark} onChange={(e) => setRemark(e.target.value)}
          placeholder={remarkTarget?.approve ? "备注（可选）" : "拒绝原因（必填，家长可见）"}
        />
      </Modal>
    </div>
  );
}
