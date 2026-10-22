import { useEffect, useState } from "react";
import {
  BookOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DownloadOutlined,
  ReloadOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import {
  App as AntdApp,
  Button,
  Card,
  Col,
  Row,
  Tag,
  Typography,
} from "antd";
import { useNavigate } from "react-router-dom";

import PaintLoading from "../components/PaintLoading";
import {
  apiDashboardOverview,
  apiExportAuditLogs,
  apiExportDashboard,
  TodoCounts,
} from "../api/admin";
import { hasPermission, useAuth } from "../auth";
import { useTodoCounts } from "../hooks/useTodoCounts";
import type { components } from "../api/schema";
import DashboardCharts from "../components/DashboardCharts";
import PageTitle from "../components/PageTitle";

type Overview = components["schemas"]["DashboardOverviewResponse"];

/** 经营看板格子（C21 点亮：只读统计；valueKey 对应 DashboardOverviewResponse 字段） */
const BUSINESS_CELLS: { label: string; unit: string; valueKey: string }[] = [
  { label: "总藏书量", unit: "本", valueKey: "copy_total" },
  { label: "在馆 / 借出", unit: "本", valueKey: "copy_available_borrowed" },
  { label: "维护 / 遗失", unit: "本", valueKey: "copy_maint_lost" },
  { label: "今日借出", unit: "本", valueKey: "today_borrowed" },
  { label: "今日归还", unit: "本", valueKey: "today_returned" },
  { label: "当前逾期", unit: "本", valueKey: "overdue_active" },
  { label: "会员总数", unit: "人", valueKey: "member_total" },
  { label: "待评估", unit: "人", valueKey: "pending_evaluation_count" },
  { label: "本周新增会员", unit: "人", valueKey: "member_new_week" },
  { label: "续借率", unit: "%", valueKey: "renew_rate" },
  { label: "退会率", unit: "%", valueKey: "withdrawal_rate" },
  { label: "测验通过率", unit: "%", valueKey: "quiz_pass_rate" },
  { label: "里程碑达成", unit: "人", valueKey: "milestone_count" },
  { label: "近期活动报名", unit: "人次", valueKey: "activity_enroll_recent" },
];

const cellValue = (o: Overview | null, key: string): number | string | null => {
  if (!o) return null;
  if (key === "copy_available_borrowed") return `${o.copy_available} / ${o.copy_borrowed}`;
  if (key === "copy_maint_lost") return `${o.copy_maintenance} / ${o.copy_lost}`;
  const raw = (o as unknown as Record<string, number | null | undefined>)[key] ?? null;
  return raw;
};

/** WM13 待办卡（提案 §6.3）：实时计数+跳转；失败禁假 0（U10）；零待办绿色态。
 *
 * P1 #10（2026-10-15 大审查）：本表**必须与后端 `admin_total` 的求和项逐个对齐**——
 * 原先少了「活动订单作废 / 报损赔偿 / 资金异常 / 待评估跟进 / 待结清跟进」5 项，
 * 于是只有这几类待办时卡片显示绿色「全部处理完毕」而侧边栏徽标 >0（同屏自相矛盾，
 * 且把资金异常这种最该被看见的假绿掉）。后端求和项见 `admin/todo_service.todo_counts`。
 */
const TODO_ROWS: { key: keyof TodoCounts; label: string; route: string }[] = [
  { key: "refund_pending", label: "待审退款", route: "/refund-center" },
  { key: "withdrawal_pending", label: "待审退会", route: "/refund-center" },
  { key: "transfer_pending", label: "待审转让", route: "/refund-center" },
  { key: "activity_batch_refund", label: "活动批量退款", route: "/activities" },
  { key: "transfer_expiring", label: "转让临近超时", route: "/refund-center" },
  // G5（2026-10-14）：预约期间副本转维护/遗失且无副本可换 → 预约转异常，需馆员跟进改约/补书
  { key: "reservation_exception", label: "预约异常待跟进", route: "/reservations" },
  // 2026-10-12 拍板三类 + 2026-10-14 G12/G1 两类跟进（均计入 admin_total，本卡不得漏列）
  {
    key: "activity_orders_voided",
    label: "活动订单作废",
    route: "/notifications?box=admin&scene=admin.activity_order_voided",
  },
  {
    key: "copy_damage_pending",
    label: "报损赔偿",
    route: "/notifications?box=admin&scene=admin.copy_damage_compensation",
  },
  {
    key: "payment_anomaly",
    label: "资金异常",
    route: "/notifications?box=admin&scene=admin.payment_anomaly",
  },
  // P2-28（2026-10-17 第六十九批·大审查）：审核超时提醒补齐到三条（此前只有转让的
  // 「转让临近超时」一行）；退款/退会两条同样计入 admin_total（超管视角），故必须在这里有行
  // ——`scripts/check_admin_todo_rows.py` 按视角断言，漏一行即门禁红。
  {
    key: "refund_review_timeout",
    label: "退款审核超时",
    route: "/notifications?box=admin&scene=admin.refund_review_timeout",
  },
  {
    key: "withdrawal_review_timeout",
    label: "退会审核超时",
    route: "/notifications?box=admin&scene=admin.withdrawal_review_timeout",
  },
];

/** member.manage 角色的待办行（同样必须与 admin_total 求和项对齐） */
const STAFF_TODO_ROWS: { key: keyof TodoCounts; label: string; route: string }[] = [
  { key: "order_pending_manual", label: "待确认收款", route: "/members?tab=orders" },
  // R-313：未入会临时借书 → 入会跟进（点进去到会员管理跟进家长入会）
  { key: "member_follow_up", label: "入会跟进", route: "/members" },
  // T6/R10：活动报名待确认——计入 admin_total（运营视角一个数），原先本卡漏列
  { key: "activity_enroll_pending", label: "活动报名待确认", route: "/activities" },
  // G12/G1（2026-10-14 第五十九批）：两类跟进待办（此前只在后端计数、不进本卡）
  {
    key: "pending_evaluation_followup",
    label: "待评估跟进",
    route: "/notifications?box=admin&scene=admin.pending_evaluation",
  },
  {
    key: "deposit_settle_followup",
    label: "待结清跟进",
    route: "/notifications?box=admin&scene=admin.deposit_settle",
  },
];

function TodoCard({
  counts,
  failed,
  reload,
  isSuper,
}: {
  counts: TodoCounts | null;
  failed: boolean;
  reload: () => void;
  isSuper: boolean;
}) {
  const navigate = useNavigate();
  const { permissions } = useAuth();
  // 行级权限粒度（Q9 裁定）：审计类仅超管可见；待确认收款跟 member.manage 走
  const rows = [
    ...(isSuper ? TODO_ROWS : []),
    ...(hasPermission(permissions, "member.manage") ? STAFF_TODO_ROWS : []),
  ];
  const total = counts ? rows.reduce((s, r) => s + (counts[r.key] || 0), 0) : 0;
  return (
    <Card
      size="small"
      title="待办事项"
      style={{ marginBottom: 12, borderColor: "var(--paint-ink)" }}
      styles={{ body: { padding: "10px 16px" } }}
    >
      {failed ? (
        <Button size="small" danger onClick={reload}>
          待办加载失败 · 点击重试
        </Button>
      ) : !counts ? (
        <Typography.Text type="secondary">待办加载中…</Typography.Text>
      ) : total === 0 ? (
        <Tag color="green" style={{ fontSize: 13, padding: "2px 10px" }}>
          全部处理完毕
        </Tag>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {rows.map((r) => {
            const n = counts[r.key] || 0;
            return (
              <Button
                key={r.key}
                type="link"
                size="small"
                style={{ justifyContent: "flex-start", paddingInline: 0, width: "fit-content" }}
                onClick={() => navigate(r.route)}
              >
                {n > 0 ? (
                  <span style={{ fontWeight: 700, color: "#cf1322" }}>
                    {r.label} {n} 笔 →
                  </span>
                ) : (
                  <span style={{ color: "rgba(0,0,0,0.4)" }}>{r.label} 0 笔</span>
                )}
              </Button>
            );
          })}
        </div>
      )}
    </Card>
  );
}

export default function Dashboard() {
  const { message } = AntdApp.useApp(); // F-L4/T34：App context 化（禁静态 message）
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const { counts, failed, reload } = useTodoCounts();
  const { permissions } = useAuth();
  const isSuper = hasPermission(permissions, "audit.view");

  const load = () => {
    setLoading(true);
    apiDashboardOverview()
      .then(setOverview)
      // F-M8 族/T26：仪表盘 fetch 失败必须报错——静默 = 管理员对着空看板误判经营数据
      .catch((e: Error) => message.error(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <PageTitle>今日概览</PageTitle>
        <div style={{ display: "flex", gap: 8 }}>
          <Button
            icon={<DownloadOutlined />}
            size="small"
            onClick={() => apiExportDashboard().catch((e) => message.error((e as Error).message))}
          >
            导出看板
          </Button>
          {/* P2-58（第八轮）：审计导出后端是 `require_perm("audit.view")`——专员点了**必 403**，
              属"能点不能用"回归族。按同一权限码隐藏（与退款中心/媒体体检同款判据）。 */}
          {isSuper && (
            <Button
              icon={<DownloadOutlined />}
              size="small"
              onClick={() => apiExportAuditLogs().catch((e) => message.error((e as Error).message))}
            >
              导出审计日志
            </Button>
          )}
          <Button icon={<ReloadOutlined />} size="small" type="text" onClick={load}>
            刷新
          </Button>
        </div>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 4 }}>
        门店运营实时数据；经营看板覆盖藏书/借阅/会员/测验/里程碑，支持 Excel 导出。
      </Typography.Paragraph>

      {/* 图形区（2026-09-21 用户需求：仪表盘要有"很酷炫的图"，且要投到店外电视上）。
          用既有 /api/admin/dashboard 的数据渲染，不改后端；数字卡片按用户裁定照旧留在下方。 */}
      {overview && <DashboardCharts data={overview} />}

      <TodoCard counts={counts} failed={failed} reload={reload} isSuper={isSuper} />

      {loading ? (
        <PaintLoading character="rainbow" />
      ) : (
        <>
          <Row gutter={[12, 12]}>
          <Col xs={24} sm={8}>
            <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <TeamOutlined style={{ fontSize: 22, color: "var(--paint-ink)" }} />
                <div>
                  <div style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                    {overview?.admin_count ?? "—"}
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    后台账号（个）
                  </Typography.Text>
                </div>
              </div>
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <ClockCircleOutlined style={{ fontSize: 22, color: "var(--paint-ink)" }} />
                <div>
                  <div style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                    {overview?.today_logins ?? "—"}
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    今日登录（次）
                  </Typography.Text>
                </div>
              </div>
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <CheckCircleOutlined style={{ fontSize: 22, color: "var(--paint-secondary)" }} />
                <div>
                  <div style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                    {overview?.config_count ?? "—"}
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    业务配置项（项）
                  </Typography.Text>
                </div>
              </div>
            </Card>
          </Col>
        </Row>

        <Typography.Title
          level={5}
          style={{ fontFamily: "var(--font-display)", marginTop: 20, marginBottom: 8 }}
        >
          最近配置变更
        </Typography.Title>
        <Card size="small" styles={{ body: { padding: "4px 16px" } }}>
          {(overview?.recent_config_changes ?? []).length === 0 ? (
            <Typography.Text type="secondary">暂无变更记录</Typography.Text>
          ) : (
            (overview?.recent_config_changes ?? []).map((c, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "8px 0",
                  borderBottom: i < (overview?.recent_config_changes.length ?? 0) - 1 ? "1px dashed var(--paint-border)" : "none",
                }}
              >
                <span>
                  <Typography.Text strong>{c.config_name}</Typography.Text>
                  <Typography.Text
                    code
                    style={{ marginLeft: 8 }}
                  >
                    {c.change}
                  </Typography.Text>
                </span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {c.actor_name} · {c.created_at}
                </Typography.Text>
              </div>
            ))
          )}
        </Card>

        <Typography.Title
          level={5}
          style={{ fontFamily: "var(--font-display)", marginTop: 20, marginBottom: 8 }}
        >
          经营看板
        </Typography.Title>
        <Row gutter={[12, 12]}>
          {BUSINESS_CELLS.map((cell) => (
            <Col key={cell.label} xs={12} sm={6}>
              <Card
                size="small"
                styles={{ body: { padding: "12px 16px" } }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <Typography.Text type="secondary">{cell.label}</Typography.Text>
                  <BookOutlined style={{ color: "var(--paint-ink-light)" }} />
                </div>
                <div style={{ marginTop: 6, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <Typography.Text style={{ fontSize: 20, fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                    {cellValue(overview, cell.valueKey) ?? "—"}
                  </Typography.Text>
                  <Tag color="green" style={{ fontSize: 11 }}>{cell.unit}</Tag>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
        </>
      )}
    </>
  );
}
