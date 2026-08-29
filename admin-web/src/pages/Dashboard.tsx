import { useEffect, useState } from "react";
import {
  BookOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DownloadOutlined,
  ReloadOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { Button, Card, Col, message, Row, Tag, Typography } from "antd";

import PaintLoading from "../components/PaintLoading";
import { apiDashboardOverview, apiExportAuditLogs, apiExportDashboard } from "../api/admin";
import type { components } from "../api/schema";

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
  { label: "续费率", unit: "%", valueKey: "renew_rate" },
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

export default function Dashboard() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    apiDashboardOverview()
      .then(setOverview)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  };

  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ fontFamily: "var(--font-display)", marginBottom: 0 }}>
          今日概览
        </Typography.Title>
        <div style={{ display: "flex", gap: 8 }}>
          <Button
            icon={<DownloadOutlined />}
            size="small"
            onClick={() => apiExportDashboard().catch((e) => message.error((e as Error).message))}
          >
            导出看板
          </Button>
          <Button
            icon={<DownloadOutlined />}
            size="small"
            onClick={() => apiExportAuditLogs().catch((e) => message.error((e as Error).message))}
          >
            导出审计日志
          </Button>
          <Button icon={<ReloadOutlined />} size="small" type="text" onClick={load}>
            刷新
          </Button>
        </div>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 4 }}>
        门店运营实时数据；经营看板覆盖藏书/借阅/会员/测验/里程碑，支持 Excel 导出。
      </Typography.Paragraph>

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
                  <div style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-display)" }}>
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
                  <div style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-display)" }}>
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
                  <div style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-display)" }}>
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
                <div style={{ marginTop: 6, display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <Typography.Text style={{ fontSize: 20, fontWeight: 700, fontFamily: "var(--font-display)" }}>
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
