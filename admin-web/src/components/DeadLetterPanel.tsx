import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { Alert, App as AntdApp, Button, Card, Switch, Table, Tag } from "antd";
import { ReloadOutlined } from "@ant-design/icons";

import { apiDeadLetters, apiHandleDeadLetter, type DeadLetterItem } from "../api/admin";
import PaintEmpty from "./PaintEmpty";
import PaintPagination from "./PaintPagination";
import { usePaintPagination } from "../hooks/usePaintPagination";

const NUM: CSSProperties = { fontFamily: "var(--font-mono)" };
const MUTED: CSSProperties = { color: "rgba(0,0,0,0.45)" };

/**
 * 死信看板（P2-1，2026-10-18 第七十八批）。
 *
 * 为什么要有：事件处理器重试后仍失败 → 跨域副作用（积分/词数/通知/审计）**没发生**，
 * 而死信表此前**只写不读**（没有任何页面/接口）——运营看不到，等于静默数据缺失。
 * 本面板给出：待处理计数 + 最近 20 条 + 人工销项（幂等，留审计 dead_letter.handle）。
 */
export default function DeadLetterPanel() {
  const { message } = AntdApp.useApp();
  const [rows, setRows] = useState<DeadLetterItem[]>([]);
  const [pending, setPending] = useState(0);
  const [unresolvedOnly, setUnresolvedOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [handling, setHandling] = useState<number | null>(null);
  // P3 管理端族（2026-10-22）：原先固定只取 20 条且 `pagination={false}` → 死信一多就
  // 「看不到、也翻不到」（后端早已支持分页并返回 total）。现在走统一分页组件。
  const [total, setTotal] = useState(0);
  const { page, pageSize, setPage, onChange: onPageChange } = usePaintPagination();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiDeadLetters(unresolvedOnly, page, pageSize);
      setRows(r.items);
      setPending(r.pending);
      setTotal(r.total);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [unresolvedOnly, page, pageSize, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const handle = async (id: number) => {
    setHandling(id);
    try {
      const r = await apiHandleDeadLetter(id);
      message.success(r.already_handled ? "该条此前已销项（幂等）" : "已标记处理");
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setHandling(null);
    }
  };

  return (
    <Card
      title="事件死信"
      extra={
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={MUTED}>只看未处理</span>
          <Switch
            size="small"
            checked={unresolvedOnly}
            onChange={(v) => {
              setUnresolvedOnly(v);
              setPage(1); // 筛选变更回第 1 页（否则第 3 页切筛选会空列表）
            }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        </div>
      }
    >
      {pending > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`有 ${pending} 条死信待处理：事件处理器重试后仍失败 = 对应副作用（积分/词数/通知/审计）没有发生。`}
        />
      )}
      <Table<DeadLetterItem>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        locale={{ emptyText: <PaintEmpty message="暂无死信" /> }}
        pagination={false}
        columns={[
          { title: "事件", dataIndex: "event_type", width: 170 },
          { title: "处理器", dataIndex: "handler_name", width: 130 },
          {
            title: "失败原因",
            dataIndex: "error",
            render: (v: string) => (
              <span title={v} style={{ ...MUTED, fontSize: 12 }}>
                {v || "-"}
              </span>
            ),
          },
          {
            title: "重试",
            dataIndex: "retry_count",
            width: 60,
            render: (v: number) => <span style={NUM}>{v}</span>,
          },
          {
            title: "落库时间",
            dataIndex: "create_time",
            width: 160,
            render: (v: string | null) => <span style={NUM}>{v ? v.slice(0, 19).replace("T", " ") : "-"}</span>,
          },
          {
            title: "状态",
            dataIndex: "handled_at",
            width: 90,
            render: (v: string | null) =>
              v ? <Tag color="green">已处理</Tag> : <Tag color="red">待处理</Tag>,
          },
          {
            title: "操作",
            width: 110,
            render: (_: unknown, r: DeadLetterItem) =>
              r.handled_at ? (
                <span style={MUTED}>-</span>
              ) : (
                <Button
                  size="small"
                  loading={handling === r.id}
                  onClick={() => void handle(r.id)}
                >
                  标记已处理
                </Button>
              ),
          },
        ]}
      />
      <div style={{ marginTop: 12 }}>
        <PaintPagination
          current={page}
          pageSize={pageSize}
          total={total}
          onChange={onPageChange}
        />
      </div>
    </Card>
  );
}
