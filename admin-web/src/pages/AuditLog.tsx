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

export default function AuditLogPage() {
  const { message } = AntdApp.useApp();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [action, setAction] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);

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
      <Typography.Title level={4} style={{ fontFamily: "Georgia, 'Songti SC', serif" }}>
        审计日志
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        所有敏感操作（登录 / 配置变更 / 资金操作 / 人工放行）的留痕记录；日志只读不可篡改。
      </Typography.Paragraph>
      <Space style={{ marginBottom: 16 }}>
        <Select
          placeholder="按动作筛选"
          allowClear
          style={{ width: 160 }}
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
          { title: "时间", dataIndex: "created_at", width: 180 },
          { title: "操作人", dataIndex: "actor_name", width: 120 },
          {
            title: "动作",
            dataIndex: "action",
            width: 110,
            render: (a: string) => (
              <Tag color={ACTION_COLOR[a] ?? "default"}>{ACTION_LABEL[a] ?? a}</Tag>
            ),
          },
          { title: "对象", key: "target", width: 220, render: (_, r) => `${r.target_type}:${r.target_id}` },
          { title: "详情", dataIndex: "detail", ellipsis: true },
          { title: "原因", dataIndex: "reason", ellipsis: true },
        ]}
      />
    </>
  );
}
