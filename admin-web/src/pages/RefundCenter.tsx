import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
// 退款中心（WM10：订单/押金退款 + 退会 + 转让，超管逐单审核）
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  App as AntdApp, Button, Input, Modal, Radio, Space, Table, Tabs, Tag, Tooltip, Typography,
} from "antd";

import {
  apiListRefunds, apiListTransfers, apiListWithdrawals,
  apiReviewRefund, apiReviewTransfer, apiReviewWithdrawal, apiExecuteRefund,
  type RefundRequestItem, type TransferItem, type WithdrawalItem,
} from "../api/refunds";
import { usePaintPagination } from "../hooks/usePaintPagination";
import { TODO_REFRESH_EVENT } from "../hooks/useTodoCounts";
import { PaintHScrollbar } from "../components/PaintHScrollbar";

const KIND_LABEL: Record<string, string> = { order: "订单退款", deposit: "押金退款" };
const ORDER_TYPE_LABEL: Record<string, string> = {
  observation_fee: "观察期费", formal_fee: "年费",
  first_activity_fee: "首场活动", activity_fee: "活动费",
  deposit: "押金", deposit_supplement: "押金补缴",
};
const STATUS_LABEL: Record<string, string> = {
  // 退款 7 态（R-308）
  pending: "待审核", approved: "已通过", processing: "执行中",
  refunded: "已退款", failed: "退款失败", rejected: "已拒绝", cancelled: "已撤销",
  // 退会 6 态（R-311）
  applying: "已申请", pending_settle: "待结算", refunding: "退款中", completed: "已完成",
  // 转让
  expired: "已超时",
};
const STATUS_TOOLTIP: Record<string, string> = {
  pending_settle: "结算中：退款单已生成，请在「退款」tab 逐单执行",
  refunding: "执行中：等待关联退款单处理，进度见「退款」tab",
  completed: "退会已完成（所有退款单均已结清）",
};
const STATUS_COLOR: Record<string, string> = {
  pending: "orange", approved: "green", processing: "blue",
  refunded: "green", failed: "red", rejected: "red",
  cancelled: "default", expired: "default",
  applying: "orange", pending_settle: "gold", refunding: "blue", completed: "green",
};

export default function RefundCenter() {
  const { message } = AntdApp.useApp();
  // WM13 跳转最后一公里（只读解析）：?tab=refunds|withdrawals|transfers（pending 兼容映射 refunds）
  // + ?highlight={id} 行高亮 3 秒——从通知跳过来直接看到那一单
  const [searchParams] = useSearchParams();
  const urlTab = searchParams.get("tab");
  const [activeTab, setActiveTab] = useState<string>(
    urlTab === "withdrawals" ? "withdrawals" : urlTab === "transfers" ? "transfers" : "refunds"
  );
  const [highlightId, setHighlightId] = useState<number | null>(
    searchParams.get("highlight") ? Number(searchParams.get("highlight")) : null
  );
  useEffect(() => {
    if (highlightId === null) return;
    const t = setTimeout(() => setHighlightId(null), 3000);
    return () => clearTimeout(t);
  }, [highlightId]);
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
  const [execTarget, setExecTarget] = useState<{
    id: number;
    childName: string;
    amount: string;
    kind: string;
    retry: boolean;
  } | null>(null);
  const [execSuccess, setExecSuccess] = useState(true);
  const [execRemark, setExecRemark] = useState("");
  const refundPg = usePaintPagination();
  const withdrawalPg = usePaintPagination();
  const transferPg = usePaintPagination();

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
      // WM13 L3：审核完成主动刷新待办徽标/待办卡
      window.dispatchEvent(new Event(TODO_REFRESH_EVENT));
      setRemarkTarget(null);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const pendingRefunds = refunds.filter((r) => r.status === "pending");
  const pendingWithdrawals = withdrawals.filter((w) => w.status === "applying");
  const pendingTransfers = transfers.filter((t) => t.status === "pending");

  const askExecute = (r: RefundRequestItem) => {
    setExecSuccess(true);
    setExecRemark("");
    setExecTarget({
      id: r.id,
      childName: r.child_name,
      amount: r.amount,
      kind: r.kind,
      retry: r.status === "failed",
    });
  };

  const doExecute = async () => {
    if (!execTarget) return;
    if (!execSuccess && !execRemark.trim()) {
      message.warning("执行失败必须填写原因（留痕）");
      return;
    }
    try {
      await apiExecuteRefund(execTarget.id, execSuccess, execRemark);
      message.success(execSuccess ? "已登记退款完成" : "已登记退款失败");
      // WM13 L3：执行完成主动刷新待办徽标/待办卡
      window.dispatchEvent(new Event(TODO_REFRESH_EVENT));
      setExecTarget(null);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div>
      <Typography.Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
        资金安全关口：仅超级管理员可审；拒绝必填原因（家长端可见）；押金退款随退会/转让通过自动发起。
      </Typography.Text>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "refunds",
            label: `退款待审（${pendingRefunds.length}）`,
            children: (<>
              <Table<RefundRequestItem> locale={{ emptyText: <PaintEmpty character="cat" /> }}
                rowKey="id"
                rowClassName={(r) => (r.id === highlightId ? "wm13-highlight-row" : "")}
                dataSource={refunds.slice((refundPg.page - 1) * refundPg.pageSize, refundPg.page * refundPg.pageSize)} size="middle"
                pagination={false}
                columns={[
                  { title: "类型", dataIndex: "kind", width: 100, render: (k) => KIND_LABEL[k] ?? k },
                  { title: "孩子", dataIndex: "child_name", width: 90 },
                  {
                    title: "关联", key: "rel", width: 190, render: (_, r) => (
                      r.order_no ? (
                        <div>
                          <Typography.Text code style={{ fontSize: 12 }}>{r.order_no}</Typography.Text>
                          <div style={{ fontSize: 12, color: "var(--paint-ink-light)" }}>
                            {ORDER_TYPE_LABEL[r.order_type ?? ""] ?? r.order_type}
                            {r.pay_method ? ` · ${r.pay_method}` : ""}
                          </div>
                        </div>
                      ) : "押金账户"
                    ),
                  },
                  { title: "金额", dataIndex: "amount", width: 100, render: (v) => <Typography.Text strong>￥{Number(v).toLocaleString()}</Typography.Text> },
                  { title: "家长原因", dataIndex: "reason" },
                  { title: "状态", dataIndex: "status", width: 90, render: (s) => <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] ?? s}</Tag> },
                  { title: "审核备注", dataIndex: "review_remark", width: 150, render: (v) => v ?? "—" },
                  { title: "申请时间", dataIndex: "created_at", width: 165, render: (v) => v.replace("T", " ").slice(0, 19) },
                  {
                    title: "操作", key: "op", width: 190, render: (_, r) => (
                      r.status === "pending" ? (
                        <Space>
                          <Button type="primary" size="small" onClick={() => askReview("refund", r.id, true,
                            "通过退款",
                            `${r.child_name} · ￥${r.amount}（${KIND_LABEL[r.kind]}）`)}>通过</Button>
                          <Button size="small" danger onClick={() => askReview("refund", r.id, false,
                            "拒绝退款",
                            `${r.child_name} · ￥${r.amount}（${KIND_LABEL[r.kind]}）`)}>拒绝</Button>
                        </Space>
                      ) : (r.status === "approved" || r.status === "failed") ? (
                        <Button type="primary" size="small"
                          onClick={() => askExecute(r)}>
                          {r.status === "failed" ? "重试执行" : "执行退款"}
                        </Button>
                      ) : <span>—</span>
                    ),
                  },
                ]}
               scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
              <PaintPagination current={refundPg.page} pageSize={refundPg.pageSize} total={refunds.length} onChange={refundPg.onChange} />
            </>),

          },
          {
            key: "withdrawals",
            label: `退会待审（${pendingWithdrawals.length}）`,
            children: (<>
              <Table<WithdrawalItem> locale={{ emptyText: <PaintEmpty character="cat" /> }}
                rowKey="id"
                rowClassName={(r) => (r.id === highlightId ? "wm13-highlight-row" : "")}
                dataSource={withdrawals.slice((withdrawalPg.page - 1) * withdrawalPg.pageSize, withdrawalPg.page * withdrawalPg.pageSize)} size="middle"
                pagination={false}
                columns={[
                  { title: "孩子", dataIndex: "child_name", width: 100 },
                  { title: "当前状态", dataIndex: "member_status", width: 100 },
                  { title: "退会原因", dataIndex: "reason" },
                  { title: "状态", dataIndex: "status", width: 120, render: (s) => (
                    <Tooltip title={STATUS_TOOLTIP[s]}>
                      <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] ?? s}</Tag>
                    </Tooltip>
                  ) },
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
               scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
              <PaintPagination current={withdrawalPg.page} pageSize={withdrawalPg.pageSize} total={withdrawals.length} onChange={withdrawalPg.onChange} />
            </>),

          },
          {
            key: "transfers",
            label: `转让待审（${pendingTransfers.length}）`,
            children: (<>
              <Table<TransferItem> locale={{ emptyText: <PaintEmpty character="cat" /> }}
                rowKey="id"
                rowClassName={(r) => (r.id === highlightId ? "wm13-highlight-row" : "")}
                dataSource={transfers.slice((transferPg.page - 1) * transferPg.pageSize, transferPg.page * transferPg.pageSize)} size="middle"
                pagination={false}
                columns={[
                  { title: "转出方", dataIndex: "source_name", width: 100 },
                  { title: "受让方", dataIndex: "target_name", width: 100 },
                  { title: "状态", dataIndex: "status", width: 90, render: (s) => <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] ?? s}</Tag> },
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
               scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
              <PaintPagination current={transferPg.page} pageSize={transferPg.pageSize} total={transfers.length} onChange={transferPg.onChange} />
            </>),

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

      <Modal
        title={execTarget?.retry ? "重试执行退款" : "执行退款"}
        open={!!execTarget}
        okText="确认登记" cancelText="取消"
        onOk={doExecute} onCancel={() => setExecTarget(null)}
      >
        <Typography.Paragraph>
          {execTarget?.childName} · ￥{execTarget?.amount}（{execTarget ? KIND_LABEL[execTarget.kind] : ""}）
        </Typography.Paragraph>
        <Radio.Group value={execSuccess ? "ok" : "fail"} onChange={(e) => setExecSuccess(e.target.value === "ok")}>
          <Radio value="ok">退款成功</Radio>
          <Radio value="fail">退款失败</Radio>
        </Radio.Group>
        <Input.TextArea
          rows={2} value={execRemark} onChange={(e) => setExecRemark(e.target.value)}
          placeholder={execSuccess ? "凭证/备注（可选）" : "失败原因（必填，留痕）"}
          style={{ marginTop: 8 }}
        />
      </Modal>
    </div>
  );
}
