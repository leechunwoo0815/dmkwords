import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
// 退款中心（WM10：订单/押金退款 + 退会 + 转让，超管逐单审核）
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  App as AntdApp,
  Button,
  Card,
  Input,
  Modal,
  Radio,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Alert,
} from "antd";

import {
  apiListRefunds, apiListTransfers, apiListWithdrawals, apiRefundCenterCounts,
  type RefundCenterCounts,
  apiReviewRefund, apiReviewTransfer, apiReviewWithdrawal, apiExecuteRefund,
  apiQueryRefundGateway,
  apiSettlePreview, type SettlePreview,
  type RefundRequestItem, type TransferItem, type WithdrawalItem,
} from "../api/refunds";
import { hasPermission, useAuth } from "../auth";
import { payMethodLabel } from "../constants/pay";
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
  // R3（#5）403 风暴根治：退款中心=资金审核域（audit.view 超管专属）——
  // 无权限直接渲染提示页且不发任何请求（从源头杜绝直输 URL 的 6 连 toast）
  const { permissions } = useAuth();
  const isSuper = hasPermission(permissions, "audit.view");
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

  const [refunds, setRefunds] = useState<RefundRequestItem[]>([]);
  const [withdrawals, setWithdrawals] = useState<WithdrawalItem[]>([]);
  const [transfers, setTransfers] = useState<TransferItem[]>([]);
  // P2-44：三个台账的后端硬上限（200）——已达上限时列表底部提示"可能还有更多"，
  // 否则 200 条看起来就是全部，**待审单会被漏看**（资金域，最高危）。
  const [refundLimit, setRefundLimit] = useState<number | null>(null);
  const [withdrawalLimit, setWithdrawalLimit] = useState<number | null>(null);
  const [transferLimit, setTransferLimit] = useState<number | null>(null);
  // P1-7：服务端分页——总数来自 `X-Total-Count`（当前筛选下的**全部**行数，
  // 不再是"本页行数"）；徽标来自后端 counts 端点（分页后不能再用本页行数算，那是"假 0"）。
  const [refundTotal, setRefundTotal] = useState<number | null>(null);
  const [withdrawalTotal, setWithdrawalTotal] = useState<number | null>(null);
  const [transferTotal, setTransferTotal] = useState<number | null>(null);
  const [counts, setCounts] = useState<RefundCenterCounts | null>(null);
  // P2-57（第八轮）：**列表取数失败不许渲染成空表**——徽标说"待审 N"、表格却空着，
  // 运营会以为"没单可审"（假 0 变体）。三个台账各自记失败原因，表格区如实提示 + 重试。
  const [listErrors, setListErrors] = useState<{ refund: string; withdrawal: string; transfer: string }>(
    { refund: "", withdrawal: "", transfer: "" },
  );
  // R5（#3）时序加固：3 秒清除窗口从列表数据到达起算——原实现挂载即起算，
  // 接口返回前窗口已烧掉，行渲染时 highlightId 已被清（高亮不出现真凶嫌疑）
  const [hlArmed, setHlArmed] = useState(false);
  useEffect(() => {
    if (!hlArmed && (refunds.length || withdrawals.length || transfers.length)) setHlArmed(true);
  }, [refunds, withdrawals, transfers, hlArmed]);
  useEffect(() => {
    if (highlightId === null || !hlArmed) return;
    const t = setTimeout(() => setHighlightId(null), 3000);
    return () => clearTimeout(t);
  }, [highlightId, hlArmed]);
  const [remark, setRemark] = useState("");
  const [remarkTarget, setRemarkTarget] = useState<{
    kind: "refund" | "withdrawal" | "transfer";
    id: number;
    approve: boolean;
    title: string;
    content: string;
  } | null>(null);
  const [settlePreview, setSettlePreview] = useState<SettlePreview | null>(null);
  const [settlePreviewError, setSettlePreviewError] = useState(false);
  // R4（#2）：联动退会单超管"查看详情"——settle-preview 只读复用（X2 数据源）
  const [linkTarget, setLinkTarget] = useState<WithdrawalItem | null>(null);
  const [linkSettle, setLinkSettle] = useState<SettlePreview | null>(null);
  const [linkSettleErr, setLinkSettleErr] = useState(false);
  const [execTarget, setExecTarget] = useState<{
    id: number;
    childName: string;
    amount: string;
    kind: string;
    retry: boolean;
    refundChannel?: string; // WM12-B：wechat=自动原路退回，执行弹窗据此提示
    unknown?: boolean; // WM12-C：网关结果未知（禁止跳过查单直接重试）
  } | null>(null);
  const [queryingId, setQueryingId] = useState<number | null>(null);
  const [execSuccess, setExecSuccess] = useState(true);
  const [execRemark, setExecRemark] = useState("");
  const refundPg = usePaintPagination();
  const withdrawalPg = usePaintPagination();
  const transferPg = usePaintPagination();

  const load = useCallback(() => {
    // P1-7：三个台账都走**服务端分页**（页码/页大小来自各自的 PaintPagination 状态），
    // 总数取响应头 X-Total-Count；P2-44 的上限元信息（X-List-Limit）一并存下来给提示条。
    apiListRefunds(undefined, refundPg.page, refundPg.pageSize)
      .then((r) => {
        setRefunds(r.items); setRefundLimit(r.limit); setRefundTotal(r.total);
        setListErrors((s) => ({ ...s, refund: "" }));
      })
      .catch((e: Error) => {
        message.error(e.message);
        setRefunds([]);
        setListErrors((s) => ({ ...s, refund: e.message }));
      });
    // F-H4/T26：资金域列表 fetch 失败必须报错——静默置空=管理员误判无待审（最高危）
    apiListWithdrawals(undefined, withdrawalPg.page, withdrawalPg.pageSize)
      .then((r) => {
        setWithdrawals(r.items); setWithdrawalLimit(r.limit); setWithdrawalTotal(r.total);
        setListErrors((s) => ({ ...s, withdrawal: "" }));
      })
      .catch((e: Error) => {
        message.error(e.message);
        setWithdrawals([]);
        setListErrors((s) => ({ ...s, withdrawal: e.message }));
      });
    apiListTransfers(undefined, transferPg.page, transferPg.pageSize)
      .then((r) => {
        setTransfers(r.items); setTransferLimit(r.limit); setTransferTotal(r.total);
        setListErrors((s) => ({ ...s, transfer: "" }));
      })
      .catch((e: Error) => {
        message.error(e.message);
        setTransfers([]);
        setListErrors((s) => ({ ...s, transfer: e.message }));
      });
    // P1-7：徽标计数来自后端（分页后本页行数算不出"待审 N"，那会变成"假 0"）
    apiRefundCenterCounts()
      .then(setCounts)
      // counts=null = 未知（界面显示「—」），不是假 0；失败留一条 warn 便于排查
      .catch((e) => {
        console.warn("退款中心计数加载失败", e);
        setCounts(null);
      });
  }, [message, refundPg.page, refundPg.pageSize, withdrawalPg.page, withdrawalPg.pageSize, transferPg.page, transferPg.pageSize]);

  useEffect(() => {
    if (!isSuper) return; // R3：无权限不发请求
    load();
  }, [load, isSuper]);

  const askReview = (
    kind: "refund" | "withdrawal" | "transfer", id: number, approve: boolean,
    title: string, content: string,
  ) => {
    setRemark("");
    setRemarkTarget({ kind, id, approve, title, content });
    // X2：退会审核明批——弹窗打开即拉预估结算（失败降级不阻断审核）
    if (kind === "withdrawal") {
      setSettlePreview(null);
      setSettlePreviewError(false);
      apiSettlePreview(id)
        .then(setSettlePreview)
        .catch(() => setSettlePreviewError(true));
    }
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

  // P1-7：**徽标全部来自后端计数**（服务端分页后只数本页 = "侧边栏说有待审、页面显示 0"）。
  // 后端不可用时退回本页统计（如实标"本页"），不假装是全量。
  const refundCounts = counts?.refund ?? null;
  const pendingRefunds = {
    length: refundCounts ? (refundCounts.pending ?? 0) : refunds.filter((r) => r.status === "pending").length,
  };
  const pendingWithdrawals = {
    length: counts?.withdrawal
      ? (counts.withdrawal.applying ?? 0)
      : withdrawals.filter((w) => w.status === "applying").length,
  };
  const pendingTransfers = {
    length: counts?.transfer
      ? (counts.transfer.pending ?? 0)
      : transfers.filter((t) => t.status === "pending").length,
  };
  // W1 tab 标签双计数：表格显示全量记录（含 approved 待执行等），单数 pending 会
  // 造成「待审 0 却有活干」观感矛盾——待执行/退款中单列（M>0 红字）
  const refundExecutable = refundCounts
    ? (refundCounts.approved ?? 0) + (refundCounts.failed ?? 0)
    : refunds.filter((r) => r.status === "approved" || r.status === "failed").length;
  const withdrawalRefunding = counts?.withdrawal
    ? (counts.withdrawal.refunding ?? 0)
    : withdrawals.filter((w) => w.status === "refunding").length;

  const askExecute = (r: RefundRequestItem) => {
    setExecSuccess(true);
    setExecRemark("");
    setExecTarget({
      id: r.id,
      childName: r.child_name,
      amount: r.amount,
      kind: r.kind,
      retry: r.status === "failed",
      refundChannel: r.refund_channel,
      unknown: !!r.gateway_unknown_at,
    });
  };

  // WM12-C（审查 P0-1）：退款查单——未知态（网关超时）下唯一合法的下一步。
  // 不查单就重试会被服务端拒绝（换新商户退款单号可能二次出款）。
  const doQueryGateway = async (id: number) => {
    setQueryingId(id);
    try {
      const res = await apiQueryRefundGateway(id);
      message.success(res.note || "查单完成");
      load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setQueryingId(null);
    }
  };

  const doExecute = async () => {
    if (!execTarget) return;
    if (!execSuccess && !execRemark.trim()) {
      message.warning("执行失败必须填写原因（留痕）");
      return;
    }
    try {
      await apiExecuteRefund(execTarget.id, execSuccess, execRemark);
      // X3：反馈带金额+类型，执行者不用回头查列
      const amountTxt = `￥${Number(execTarget.amount).toLocaleString()}（${KIND_LABEL[execTarget.kind] ?? execTarget.kind}）`;
      message.success(execSuccess ? `已登记退款完成：${amountTxt}` : `已登记退款失败：${amountTxt}`);
      // WM13 L3：执行完成主动刷新待办徽标/待办卡
      window.dispatchEvent(new Event(TODO_REFRESH_EVENT));
      setExecTarget(null);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  if (!isSuper) {
    return (
      <Card>
        <PaintEmpty message="仅超级管理员可访问退款中心（资金审核域）" />
      </Card>
    );
  }

  return (
    <div>
      <Typography.Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
        资金安全关口：仅超级管理员可审；拒绝必填原因（家长端可见）；押金退款随退会/转让通过自动发起。
      </Typography.Text>
      {/* P1-7：后端计数不可用时如实降级——徽标数字将只反映**本页**，不能假装是全量 */}
      {counts === null && (
        <Typography.Text type="warning" style={{ display: "block", marginBottom: 12 }}>
          后端计数暂不可用：下方徽标数字为本页统计（非全量），请以侧边栏待办徽标为准。
        </Typography.Text>
      )}

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "refunds",
            label: (
              <>
                退款（待审{" "}
                {pendingRefunds.length > 0 ? (
                  <span style={{ background: "#ff4d4f", color: "#fff", borderRadius: 10, padding: "0 6px" }}>
                    {pendingRefunds.length}
                  </span>
                ) : (
                  pendingRefunds.length
                )}
                {refundExecutable > 0 && (
                  <span style={{ color: "#cf1322", fontWeight: 700 }}>
                    {" · 待执行 "}
                    {refundExecutable}
                  </span>
                )}
                ）
              </>
            ),
            children: (<>
              {listErrors.refund && (
                <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
                  {listErrors.refund} · 列表未加载，不代表没有待办——请重试
                  <Typography.Link onClick={load} style={{ marginLeft: 8 }}>重试</Typography.Link>
                </Typography.Text>
              )}
              <Table<RefundRequestItem> locale={{ emptyText: listErrors.refund ? null : <PaintEmpty character="cat" /> }}
                rowKey="id"
                rowClassName={(r) => (r.id === highlightId ? "wm13-highlight-row" : "")}
                dataSource={refunds} size="middle"
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
                            {r.pay_method ? ` · ${payMethodLabel(r.pay_method)}` : ""}
                          </div>
                        </div>
                      ) : "押金账户"
                    ),
                  },
                  { title: "金额", dataIndex: "amount", width: 100, render: (v) => <Typography.Text strong>￥{Number(v).toLocaleString()}</Typography.Text> },
                  { title: "家长原因", dataIndex: "reason" },
                  {
                    title: "状态", dataIndex: "status", width: 140, render: (s, r) => (
                      <>
                        <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] ?? s}</Tag>
                        {r.gateway_unknown_at ? <Tag color="warning">结果未知</Tag> : null}
                      </>
                    ),
                  },
                  {
                    title: "退款渠道", dataIndex: "refund_channel", width: 110,
                    // WM12-B：线上微信支付的单 → 执行时自动原路退回；线下收款的 → 人工打款登记
                    render: (v: string) => (v === "wechat"
                      ? <Tag color="processing">微信原路</Tag>
                      : <Tag>线下打款</Tag>),
                  },
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
                      ) : (r.status === "processing" && r.refund_channel === "wechat") ? (
                        // WM12-C：执行中的线上退款 → 可查单确认微信侧结果（未知态必须查单才能重试）
                        <Button size="small" loading={queryingId === r.id}
                          onClick={() => void doQueryGateway(r.id)}>查单</Button>
                      ) : <span>—</span>
                    ),
                  },
                ]}
               scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
              <PaintPagination current={refundPg.page} pageSize={refundPg.pageSize} total={refundTotal ?? refunds.length} onChange={refundPg.onChange} listLimit={refundLimit} />
            </>),

          },
          {
            key: "withdrawals",
            label: (
              <>
                退会（待审 {pendingWithdrawals.length}
                {withdrawalRefunding > 0 && (
                  <span style={{ color: "#cf1322", fontWeight: 700 }}>
                    {" · 退款中 "}
                    {withdrawalRefunding}
                  </span>
                )}
                ）
              </>
            ),
            children: (<>
              {listErrors.withdrawal && (
                <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
                  {listErrors.withdrawal} · 列表未加载，不代表没有待办——请重试
                  <Typography.Link onClick={load} style={{ marginLeft: 8 }}>重试</Typography.Link>
                </Typography.Text>
              )}
              <Table<WithdrawalItem> locale={{ emptyText: listErrors.withdrawal ? null : <PaintEmpty character="cat" /> }}
                rowKey="id"
                rowClassName={(r) => (r.id === highlightId ? "wm13-highlight-row" : "")}
                dataSource={withdrawals} size="middle"
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
                    title: "操作", key: "op", width: 260, render: (_, r) => {
                      if (r.status !== "applying") return <span>—</span>;
                      // R4：联动退会单（退款/转让驱动）灰态语义——超管仅"查看详情"
                      //（settle-preview 只读，无通过/拒绝）；直接退会单保持审核按钮
                      if (r.source && r.source !== "normal") {
                        return (
                          <Button
                            size="small"
                            onClick={() => {
                              setLinkTarget(r);
                              setLinkSettle(null);
                              setLinkSettleErr(false);
                              apiSettlePreview(r.id)
                                .then(setLinkSettle)
                                .catch(() => setLinkSettleErr(true));
                            }}
                          >
                            查看详情
                          </Button>
                        );
                      }
                      return (
                        <Space>
                          <Button type="primary" size="small" onClick={() => askReview("withdrawal", r.id, true,
                            "通过退会",
                            `${r.child_name}：退会后转为 withdrawn，自动发起押金退款（退可用余额）`)}>通过</Button>
                          <Button size="small" danger onClick={() => askReview("withdrawal", r.id, false,
                            "拒绝退会",
                            `${r.child_name}：拒绝后解锁，家长可再次申请`)}>拒绝</Button>
                        </Space>
                      );
                    },
                  },
                ]}
               scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
              <PaintPagination current={withdrawalPg.page} pageSize={withdrawalPg.pageSize} total={withdrawalTotal ?? withdrawals.length} onChange={withdrawalPg.onChange} listLimit={withdrawalLimit} />
            </>),

          },
          {
            key: "transfers",
            label: (
              <>
                转让（待审{" "}
                {pendingTransfers.length > 0 ? (
              <span style={{ background: "#ff4d4f", color: "#fff", borderRadius: 10, padding: "0 6px" }}>
                {pendingTransfers.length}
              </span>
            ) : (
              pendingTransfers.length
            )}
                ）
              </>
            ),
            children: (<>
              {listErrors.transfer && (
                <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
                  {listErrors.transfer} · 列表未加载，不代表没有待办——请重试
                  <Typography.Link onClick={load} style={{ marginLeft: 8 }}>重试</Typography.Link>
                </Typography.Text>
              )}
              <Table<TransferItem> locale={{ emptyText: listErrors.transfer ? null : <PaintEmpty character="cat" /> }}
                rowKey="id"
                rowClassName={(r) => (r.id === highlightId ? "wm13-highlight-row" : "")}
                dataSource={transfers} size="middle"
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
              <PaintPagination current={transferPg.page} pageSize={transferPg.pageSize} total={transferTotal ?? transfers.length} onChange={transferPg.onChange} listLimit={transferLimit} />
            </>),

          },
        ]}
      />

      <Modal
        title={remarkTarget?.title} open={!!remarkTarget}
        okText={remarkTarget?.approve ? "确认通过" : "确认拒绝"}
        cancelText="取消" onOk={doReview} onCancel={() => { setRemarkTarget(null); setSettlePreview(null); setSettlePreviewError(false); }}
      >
        <Typography.Paragraph>{remarkTarget?.content}</Typography.Paragraph>
        {remarkTarget?.kind === "withdrawal" && remarkTarget.approve && (
          settlePreviewError ? (
            <Typography.Text type="warning">预估获取失败，可继续审核（以结算结果为准）</Typography.Text>
          ) : !settlePreview ? (
            <Typography.Text type="secondary">预估计算中…</Typography.Text>
          ) : (
            <div style={{ marginBottom: 12 }}>
              {settlePreview.items.map((it, i) => (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                  <span>{it.kind === "deposit" ? "押金退款" : `订单 ${it.order_no ?? ""}`}</span>
                  <span style={{ color: "#888" }}>{it.rule}</span>
                  <Typography.Text strong>￥{Number(it.amount).toLocaleString()}</Typography.Text>
                </div>
              ))}
              <div style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid #eee", marginTop: 6, paddingTop: 6 }}>
                <Typography.Text strong>预估退款合计</Typography.Text>
                <Typography.Text strong style={{ color: "#cf1322", fontSize: 16 }}>
                  ￥{Number(settlePreview.total).toLocaleString()}
                </Typography.Text>
              </div>
            </div>
          )
        )}
        <Input.TextArea
          rows={2} value={remark} onChange={(e) => setRemark(e.target.value)}
          placeholder={remarkTarget?.approve ? "备注（可选）" : "拒绝原因（必填，家长可见）"}
        />
      </Modal>

      <Modal
        title={linkTarget ? `${linkTarget.child_name}·联动退会（随退款自动推进）` : ""}
        open={!!linkTarget}
        footer={null}
        onCancel={() => setLinkTarget(null)}
      >
        <Typography.Paragraph type="secondary">
          该单由退款/转让流程驱动创建，随退款自动推进（无需审核操作）。
        </Typography.Paragraph>
        {linkSettleErr ? (
          <Typography.Text type="warning">结算明细获取失败（该单可能无可退项）</Typography.Text>
        ) : !linkSettle ? (
          <Typography.Text type="secondary">结算明细计算中…</Typography.Text>
        ) : (
          <div>
            {linkSettle.items.map((it, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                <span>{it.kind === "deposit" ? "押金退款" : `订单 ${it.order_no ?? ""}`}</span>
                <span style={{ color: "#888" }}>{it.rule}</span>
                <Typography.Text strong>￥{Number(it.amount).toLocaleString()}</Typography.Text>
              </div>
            ))}
            <div style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid #eee", marginTop: 6, paddingTop: 6 }}>
              <Typography.Text strong>预估退款合计</Typography.Text>
              <Typography.Text strong style={{ color: "#cf1322", fontSize: 16 }}>
                ￥{Number(linkSettle.total).toLocaleString()}
              </Typography.Text>
            </div>
          </div>
        )}
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
        {execTarget?.unknown && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 10 }}
            message="上一笔网关结果未知（微信可能已受理）"
            description="请先「查单」确认微信侧结果：查得未受理才会允许重试（重试会换新的商户退款单号）。直接重试会被服务端拒绝——那是防二次出款。"
            action={
              <Button size="small" loading={queryingId === execTarget.id}
                onClick={() => void doQueryGateway(execTarget.id)}>查单</Button>
            }
          />
        )}
        {execTarget?.refundChannel === "wechat" && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 10 }}
            message="该单为微信线上支付 → 点「确认登记」后自动原路退回"
            description="退款结果由微信返回（受理后可能先显示「退款执行中」，到账后自动转「已退款」）。下方成功/失败只用于无法原路退回时的兜底登记。"
          />
        )}
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
