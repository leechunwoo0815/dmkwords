// 预约管理（WM6：列表 / 状态筛选 / 核销转借阅）
import { useCallback, useEffect, useState } from "react";
import { App as AntdApp, Button, Select, Space, Table, Tag, Typography } from "antd";
import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";

import {
  apiCheckoutReservation,
  apiListReservations,
  type ReservationItem,
} from "../api/reservations";
import { usePaintPagination } from "../hooks/usePaintPagination";

const STATUS_LABEL: Record<string, string> = {
  active: "锁定中", expired: "已超时", cancelled: "已取消",
  checked_out: "已借出", exception: "副本异常",
};
const STATUS_COLOR: Record<string, string> = {
  active: "blue", expired: "red", cancelled: "default",
  checked_out: "green", exception: "orange",
};

export default function Reservations() {
  const { message, modal } = AntdApp.useApp();
  const [items, setItems] = useState<ReservationItem[]>([]);
  const [status, setStatus] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);
  const { page, pageSize, setPage, onChange: onPageChange } = usePaintPagination();

  const load = useCallback(() => {
    setLoading(true);
    apiListReservations(status)
      .then((r) => setItems(r ?? []))
      .catch((e: Error) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [status, message]);

  useEffect(() => { load(); }, [load]);

  const onCheckout = (r: ReservationItem) => {
    modal.confirm({
      title: "核销预约转借阅",
      content: `${r.child_name} 预约的《${r.book_title}》：核销后立即登记借出（走标准借书校验：额度/押金/同书未还）。`,
      okText: "核销借出",
      cancelText: "取消",
      onOk: async () => {
        try {
          const res = await apiCheckoutReservation(r.id);
          message.success(`已借出，应还日期 ${res.due_at.slice(0, 10)}`);
          load();
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Select
          placeholder="预约状态" allowClear style={{ width: 140 }} value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          options={Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label }))}
        />
        <Typography.Text type="secondary">
          家长在小程序发起预约后，副本锁定 72 小时；到店取书时在此核销转借阅。
        </Typography.Text>
      </Space>
      <Table<ReservationItem> locale={{ emptyText: <PaintEmpty character="bear" /> }}
        rowKey="id" loading={loading} dataSource={items.slice((page - 1) * pageSize, page * pageSize)} size="middle"
        pagination={false}
        columns={[
          { title: "孩子", dataIndex: "child_name", width: 100 },
          {
            title: "家长", key: "parent", width: 160, render: (_, r) => (
              <div>
                <div>{r.parent_name}</div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.parent_phone}</Typography.Text>
              </div>
            ),
          },
          { title: "书名", dataIndex: "book_title", width: 200 },
          { title: "副本ID", dataIndex: "copy_id", width: 80 },
          {
            title: "状态", key: "status", width: 110, render: (_, r) => (
              <Tag color={STATUS_COLOR[r.status]}>
                {r.expired && r.status === "active" ? "已超时（待释放）" : STATUS_LABEL[r.status] ?? r.status}
              </Tag>
            ),
          },
          { title: "预约时间", dataIndex: "created_at", width: 170 },
          { title: "锁定截止", dataIndex: "expires_at", width: 170 },
          {
            title: "操作", key: "op", width: 120, render: (_, r) => (
              r.status === "active" && !r.expired ? (
                <Button type="link" size="small" onClick={() => onCheckout(r)}>核销借出</Button>
              ) : <span>—</span>
            ),
          },
        ]}
      />
      <PaintPagination current={page} pageSize={pageSize} total={items.length} onChange={onPageChange} />
    </div>
  );
}
