import { useCallback, useEffect, useState } from "react";
import { App as AntdApp, Select, Space, Table, Tag, Typography } from "antd";

import { apiListAuditLogs, type AuditLog } from "../api/admin";

const ACTION_LABEL: Record<string, string> = {
  login: "登录",
  "config.update": "配置变更",
};

const ACTION_COLOR: Record<string, string> = {
  login: "default",
  "config.update": "blue",
};

const TARGET_LABEL: Record<string, string> = {
  admin_user: "后台账号",
  system_config: "系统配置",
};

/** detail JSON → 人话（配置变更渲染为「旧值 → 新值」） */
function renderDetail(log: AuditLog): string {
  if (log.action === "config.update") {
    try {
      const d = JSON.parse(log.detail) as { old?: string; new?: string };
      const oldV = d.old === "true" ? "开启" : d.old === "false" ? "关闭" : d.old;
      const newV = d.new === "true" ? "开启" : d.new === "false" ? "关闭" : d.new;
      return `${oldV ?? "?"} → ${newV ?? "?"}`;
    } catch {
      /* 解析失败回落原始文本 */
    }
  }
  if (log.action === "login") return "登录成功";
  return log.detail || "—";
}

/** target_type:target_id → 友好对象名（配置项显示中文，账号显示操作人自己） */
function renderTarget(log: AuditLog, configNames: Record<string, string>): string {
  if (log.target_type === "system_config") {
    return configNames[log.target_id] ?? log.target_id;
  }
  if (log.target_type === "admin_user") {
    return log.actor_name || "账号";
  }
  return log.target_id || "—";
}

export default function AuditLogPage() {
  const { message } = AntdApp.useApp();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [action, setAction] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [configNames, setConfigNames] = useState<Record<string, string>>({});

  useEffect(() => {
    import("../api/admin")
      .then(({ apiListConfigs }) => apiListConfigs())
      .then((configs) => {
        const map: Record<string, string> = {};
        for (const c of configs) map[c.config_key] = c.display_name || c.description || c.config_key;
        setConfigNames(map);
      })
      .catch(() => undefined);
  }, []);

  const load = useCallback(
    (targetPage: number) => {
      setLoading(true);
      apiListAuditLogs({ page: targetPage, page_size: 15, action })
        .then((result) => {
          setLogs(result.items ?? []);
          setTotal(result.total);
        })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [action, message]
  );

  useEffect(() => {
    load(page);
  }, [load, page]);

  return (
    <>
      <Typography.Title level={4} style={{ fontFamily: "'ZCOOL KuaiLe', 'Nunito', 'PingFang SC', sans-serif" }}>
        审计日志
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        敏感操作（登录 / 配置变更 / 资金操作 / 人工放行）的留痕记录；只读不可篡改。
      </Typography.Paragraph>
      <Space style={{ marginBottom: 16 }}>
        <Select
          placeholder="按动作筛选"
          allowClear
          style={{ width: 140 }}
          value={action}
          onChange={(v) => {
            setAction(v);
            setPage(1);
          }}
          options={Object.entries(ACTION_LABEL).map(([value, label]) => ({ value, label }))}
        />
      </Space>
      <Table<AuditLog>
        rowKey="id"
        loading={loading}
        dataSource={logs}
        size="middle"
        pagination={{
          current: page,
          pageSize: 15,
          total,
          showSizeChanger: false,
          onChange: setPage,
        }}
        columns={[
          { title: "时间", dataIndex: "created_at", width: 170 },
          { title: "操作人", dataIndex: "actor_name", width: 110 },
          {
            title: "动作",
            dataIndex: "action",
            width: 100,
            render: (a: string) => (
              <Tag color={ACTION_COLOR[a] ?? "default"}>{ACTION_LABEL[a] ?? a}</Tag>
            ),
          },
          {
            title: "对象",
            key: "target",
            width: 200,
            render: (_, r) => (
              <span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {TARGET_LABEL[r.target_type] ?? r.target_type}
                </Typography.Text>
                <br />
                {renderTarget(r, configNames)}
              </span>
            ),
          },
          {
            title: "内容",
            key: "detail",
            render: (_, r) => renderDetail(r),
          },
          { title: "原因", dataIndex: "reason", ellipsis: true, width: 180 },
        ]}
      />
    </>
  );
}
