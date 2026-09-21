// 借还记录面板（2026-09-21 任务包 D 批）：操作台内的第二个 tab。
// 口径（用户拍板）：查询要能按 孩子/书/时间段/状态 筛，要看得见**归还操作人**，要能导出 Excel。
// 范式对齐既有页面：筛选 + PaintPagination + PaintHScrollbar（审计日志/订单列表同款）。
import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp,
  Button,
  Card,
  DatePicker,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { DownloadOutlined, ReloadOutlined } from "@ant-design/icons";
import type { Dayjs } from "dayjs";

import PaintEmpty from "./PaintEmpty";
import { PaintHScrollbar } from "./PaintHScrollbar";
import PaintPagination from "./PaintPagination";
import { usePaintPagination } from "../hooks/usePaintPagination";
import {
  apiBorrowRecords,
  apiExportBorrowRecords,
  type BorrowRecordItem,
} from "../api/circulation";

const STATUS_LABEL: Record<string, string> = {
  active: "借出中",
  overdue: "逾期",
  returned: "已还",
  lost: "遗失",
};
const STATUS_COLOR: Record<string, string> = {
  active: "blue",
  overdue: "red",
  returned: "green",
  lost: "orange",
};
const CONDITION_LABEL: Record<string, string> = {
  normal: "正常归架",
  maintenance: "转维护",
  lost: "标记遗失",
};

function fmt(v?: string | null): string {
  return v ? v.replace("T", " ").slice(0, 16) : "—";
}

export default function BorrowRecordPanel({ active = true }: { active?: boolean }) {
  const { message } = AntdApp.useApp();
  const { page, pageSize, setPage, onChange: onPageChange } = usePaintPagination();
  const [keyword, setKeyword] = useState("");
  const [pendingKeyword, setPendingKeyword] = useState("");
  const [status, setStatus] = useState<string>("");
  const [dateField, setDateField] = useState<"borrowed" | "returned">("borrowed");
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [rows, setRows] = useState<BorrowRecordItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const params = useCallback(
    (targetPage = page) => ({
      page: targetPage,
      page_size: pageSize,
      keyword: keyword || undefined,
      status: status || undefined,
      date_field: dateField,
      date_from: range?.[0]?.startOf("day").format("YYYY-MM-DDTHH:mm:ss"),
      date_to: range?.[1]?.endOf("day").format("YYYY-MM-DDTHH:mm:ss"),
    }),
    [keyword, status, dateField, range, page, pageSize],
  );

  const load = useCallback(
    (targetPage = page) => {
      setLoading(true);
      apiBorrowRecords(params(targetPage))
        .then((r) => {
          setRows(r.items ?? []);
          setTotal(r.total ?? 0);
        })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [params, page, message],
  );

  useEffect(() => {
    load(page);
  }, [load, page]);

  // 切到本 tab 时重新拉一次（2026-09-21 用户反馈"直接点借还记录要刷新页面才显示新数据"：
  // Tabs 会把 pane 留在 DOM 里，挂载时那一次 useEffect 不会因为切回来再跑）
  useEffect(() => {
    if (active) load(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const doExport = async () => {
    try {
      await apiExportBorrowRecords(params());
      message.success("已导出当前筛选结果");
    } catch (e) {
      message.error(e instanceof Error ? e.message : "导出失败");
    }
  };

  return (
    <Card size="small" styles={{ body: { padding: 12 } }}>
      <Space wrap style={{ marginBottom: 12 }}>
        <Input
          placeholder="孩子名 / 家长手机号 / 书名 / ISBN / 副本码"
          value={pendingKeyword}
          onChange={(e) => setPendingKeyword(e.target.value)}
          onPressEnter={() => {
            setKeyword(pendingKeyword.trim());
            setPage(1);
          }}
          style={{ width: 300 }}
          allowClear
        />
        <Select
          value={status}
          onChange={(v) => {
            setStatus(v);
            setPage(1);
          }}
          style={{ width: 130 }}
          options={[
            { value: "", label: "全部状态" },
            ...Object.entries(STATUS_LABEL).map(([v, l]) => ({ value: v, label: l })),
          ]}
        />
        <Select
          value={dateField}
          onChange={(v) => {
            setDateField(v);
            setPage(1);
          }}
          style={{ width: 130 }}
          options={[
            { value: "borrowed", label: "按借出时间" },
            { value: "returned", label: "按归还时间" },
          ]}
        />
        <DatePicker.RangePicker
          value={range}
          onChange={(v) => {
            setRange(v as [Dayjs, Dayjs] | null);
            setPage(1);
          }}
          showTime={false}
        />
        <Button icon={<ReloadOutlined />} onClick={() => load(page)}>
          刷新
        </Button>
        <Button
          type="primary"
          icon={<DownloadOutlined />}
          onClick={() => void doExport()}
        >
          导出 Excel
        </Button>
      </Space>

      <Table<BorrowRecordItem>
        rowKey="record_id"
        size="small"
        loading={loading}
        dataSource={rows}
        locale={{ emptyText: <PaintEmpty character="bear" /> }}
        pagination={false}
        scroll={{ x: "max-content" }}
        columns={[
          { title: "孩子", dataIndex: "child_name", width: 100 },
          { title: "家长电话", dataIndex: "parent_phone", width: 120 },
          { title: "书名", dataIndex: "book_title" },
          { title: "副本码", dataIndex: "copy_code", width: 150 },
          {
            title: "状态",
            dataIndex: "status",
            width: 90,
            render: (s: string) => <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] ?? s}</Tag>,
          },
          { title: "借出时间", dataIndex: "borrowed_at", width: 150, render: fmt },
          { title: "应还时间", dataIndex: "due_at", width: 150, render: fmt },
          { title: "归还时间", dataIndex: "returned_at", width: 150, render: fmt },
          {
            title: "归还状态",
            dataIndex: "returned_condition",
            width: 100,
            render: (v: string | null) => (v ? CONDITION_LABEL[v] ?? v : "—"),
          },
          {
            title: "逾期天数",
            dataIndex: "days_overdue",
            width: 90,
            render: (d: number) => (d > 0 ? <Tag color={d > 7 ? "red" : "orange"}>{d} 天</Tag> : "—"),
          },
          { title: "借出操作人", dataIndex: "borrowed_by_name", width: 110 },
          {
            title: "归还操作人",
            dataIndex: "returned_by_name",
            width: 110,
            render: (v: string) =>
              v === "未记录" ? <Typography.Text type="secondary">未记录</Typography.Text> : v,
          },
        ]}
      />
      <PaintHScrollbar auto />
      <div style={{ marginTop: 12, display: "flex", justifyContent: "flex-end" }}>
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
