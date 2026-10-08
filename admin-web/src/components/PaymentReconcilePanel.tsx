// admin-web/src/components/PaymentReconcilePanel.tsx — 支付对账卡片（WM12-B，docs/09 WM12-B）
// 为什么放在任务看板：对账是"每天该有人看一眼"的东西——绿色就滑过去，有差异才要点开。
// 口径：对账只报不改（绝不自作主张改单），所以这里只有"跑一轮"和"看差异"，没有"修复"按钮。
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { App as AntdApp, Alert, Button, Card, Collapse, Empty, Input, Space, Table, Tag } from "antd";
import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";

import {
  apiPaymentReconciliations,
  apiRunReconcile,
  type PaymentReconcileDiff,
  type PaymentReconcileReport,
} from "../api/admin";
import { hasPermission, useAuth } from "../auth";

const NUM: CSSProperties = { fontFamily: "var(--font-mono)" };
const MUTED: CSSProperties = { color: "rgba(0,0,0,0.45)" };

const SOURCE_LABEL: Record<string, string> = { local: "本地审计", wechat: "微信账单" };
const STATUS_TAG: Record<string, { color: string; text: string }> = {
  ok: { color: "green", text: "无差异" },
  diff: { color: "red", text: "有差异" },
  skipped: { color: "default", text: "已跳过" },
  failed: { color: "orange", text: "对账失败" },
};

function fmtTime(value: string): string {
  return (value || "").replace("T", " ").slice(0, 16);
}

export default function PaymentReconcilePanel() {
  const { message } = AntdApp.useApp();
  const { permissions } = useAuth();
  // WM12-C（审查 P2-11，用户裁定"都可读"）：**读**与后端同码——后端 GET /payments/reconciliations
  // 挂 dashboard.view（专员也有），前端原先用 audit.view 判超管 → 专员看不到卡片而后端放行。
  // **跑一轮**仍是超管专属（后端 POST /payments/reconcile 是 require_super_admin）。
  const canRead = hasPermission(permissions, "dashboard.view");
  const canRun = hasPermission(permissions, "audit.view");
  const [items, setItems] = useState<PaymentReconcileReport[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [billDate, setBillDate] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiPaymentReconciliations(10);
      setItems(res.items || []);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (canRead) void load(); // 无读权限（dashboard.view）就不发请求
  }, [canRead, load]);

  const runOnce = async () => {
    setRunning(true);
    try {
      const res = await apiRunReconcile(billDate.trim());
      const diffs = res.diff_total || 0;
      message.success(diffs ? `对账完成：发现 ${diffs} 条差异` : "对账完成：无差异");
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  if (!canRead) return null; // 读权限都没有 → 卡片不渲染（与后端 GET 同码）
  const latest = items[0];
  const summary = latest
    ? `${SOURCE_LABEL[latest.source] || latest.source} · ${latest.bill_date} · ${
        STATUS_TAG[latest.status]?.text || latest.status
      }${latest.diff_count ? `（${latest.diff_count} 条差异）` : ""}`
    : "还没有对账记录";

  return (
    <Card
      title="支付对账"
      extra={
        <Space>
          {canRun && (
            <>
              <Input
                size="small"
                placeholder="账单日 YYYY-MM-DD（默认今天）"
                value={billDate}
                onChange={(e) => setBillDate(e.target.value)}
                style={{ width: 200 }}
              />
              <Button
                size="small"
                type="primary"
                icon={<SyncOutlined />}
                loading={running}
                onClick={() => void runOnce()}
              >
                跑一轮
              </Button>
            </>
          )}
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        </Space>
      }
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <span style={MUTED}>{summary}</span>
        {latest && (
          <Tag color={STATUS_TAG[latest.status]?.color}>{STATUS_TAG[latest.status]?.text}</Tag>
        )}
      </div>
      {latest?.note && <Alert type="info" showIcon message={latest.note} style={{ marginBottom: 8 }} />}

      <Collapse
        ghost
        items={[
          {
            key: "reports",
            label: "对账明细与历史",
            children: items.length ? (
              <Table<PaymentReconcileReport>
                size="small"
                rowKey="id"
                loading={loading}
                dataSource={items}
                pagination={false}
                expandable={{
                  expandedRowRender: (row) =>
                    row.detail?.length ? (
                      <Table<PaymentReconcileDiff>
                        size="small"
                        rowKey={(d) => `${row.id}-${d.kind}-${d.ref}`}
                        dataSource={row.detail}
                        pagination={false}
                        columns={[
                          { title: "类型", dataIndex: "kind", width: 220 },
                          { title: "定位", dataIndex: "ref", width: 200, render: (v) => <span style={NUM}>{v}</span> },
                          { title: "说明", dataIndex: "message" },
                        ]}
                      />
                    ) : (
                      <span style={MUTED}>{row.note || "无差异"}</span>
                    ),
                }}
                columns={[
                  { title: "账单日", dataIndex: "bill_date", width: 110 },
                  {
                    title: "来源",
                    dataIndex: "source",
                    width: 100,
                    render: (v: string) => SOURCE_LABEL[v] || v,
                  },
                  {
                    title: "结果",
                    dataIndex: "status",
                    width: 100,
                    render: (v: string) => (
                      <Tag color={STATUS_TAG[v]?.color}>{STATUS_TAG[v]?.text || v}</Tag>
                    ),
                  },
                  { title: "核对笔数", dataIndex: "checked_count", width: 100, render: (v) => <span style={NUM}>{v}</span> },
                  {
                    title: "差异",
                    dataIndex: "diff_count",
                    width: 80,
                    render: (v: number) => (
                      <span style={{ ...NUM, color: v ? "#cf1322" : undefined }}>{v}</span>
                    ),
                  },
                  { title: "触发", dataIndex: "trigger", width: 90, render: (v: string) => (v === "scheduled" ? "定时" : "手动") },
                  { title: "完成时间", dataIndex: "finished_at", width: 150, render: fmtTime },
                ]}
              />
            ) : (
              <Empty description="还没有对账记录（超管可点「跑一轮」）" />
            ),
          },
        ]}
      />
    </Card>
  );
}
