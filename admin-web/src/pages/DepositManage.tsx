import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp,
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";

import {
  apiCreateDepositOrder,
  apiCreateSupplementOrder,
  apiDeductDeposit,
  apiGetDepositLedgers,
  apiListDeposits,
  type Deposit,
  type DepositLedger,
} from "../api/deposits";
import { usePaintPagination } from "../hooks/usePaintPagination";

const STATUS_LABEL: Record<string, string> = {
  unpaid: "未缴纳", paid: "已缴纳",
  partially_deducted: "部分扣除", fully_deducted: "全部扣除",
  refunding: "退款中", refunded: "已退款",
};
const STATUS_COLOR: Record<string, string> = {
  unpaid: "default", paid: "green",
  partially_deducted: "orange", fully_deducted: "red",
  refunding: "blue", refunded: "default",
};
const ENTRY_LABEL: Record<string, string> = {
  pay: "缴纳", deduct: "赔偿扣除", supplement: "补缴", refund: "退款",
};

export default function DepositManage() {
  const { message } = AntdApp.useApp();
  const [deposits, setDeposits] = useState<Deposit[]>([]);
  const [total, setTotal] = useState(0);
  const { page, pageSize, setPage, onChange: onPageChange } = usePaintPagination();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [ledgerChild, setLedgerChild] = useState<Deposit | null>(null);
  const [ledgers, setLedgers] = useState<DepositLedger[]>([]);
  const [deductChild, setDeductChild] = useState<Deposit | null>(null);
  const [deductForm] = Form.useForm();

  const load = useCallback(
    (targetPage: number) => {
      setLoading(true);
      apiListDeposits({ page: targetPage, page_size: pageSize, status, keyword: keyword || undefined })
        .then((r) => { setDeposits(r.items ?? []); setTotal(r.total); })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [status, keyword, pageSize, message]
  );

  useEffect(() => { load(page); }, [load, page]);

  const openLedgers = async (dep: Deposit) => {
    setLedgerChild(dep);
    setLedgers(await apiGetDepositLedgers(dep.child_id));
  };

  return (
    <>
      <Typography.Title level={4} style={{ fontFamily: "var(--font-display)" }}>
        押金与赔偿
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        押金按孩子独立（1200 元/人）；遗失损坏按原价赔偿、优先扣本人押金；不足部分记「待结清」并持续提醒。
      </Typography.Paragraph>

      <Space style={{ marginBottom: 12 }}>
        <Input.Search
          placeholder="孩子姓名" allowClear style={{ width: 200 }}
          onSearch={(v) => { setKeyword(v); setPage(1); }}
        />
        <Select
          placeholder="押金状态" allowClear style={{ width: 130 }} value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          options={Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label }))}
        />
      </Space>

      <Table<Deposit> locale={{ emptyText: <PaintEmpty character="cat" /> }}
        rowKey="id" loading={loading} dataSource={deposits} size="middle"
        pagination={false}
        columns={[
          { title: "孩子", dataIndex: "child_name", width: 110 },
          { title: "押金状态", dataIndex: "status", width: 110, render: (s: string) => <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] ?? s}</Tag> },
          { title: "可用余额", dataIndex: "available_amount", width: 110, render: (v: string) => <Typography.Text strong>￥{Number(v).toLocaleString()}</Typography.Text> },
          { title: "标准额", dataIndex: "amount", width: 90, render: (v: string) => `￥${Number(v).toLocaleString()}` },
          { title: "累计扣除", dataIndex: "deducted_amount", width: 100, render: (v: string) => `￥${Number(v).toLocaleString()}` },
          { title: "累计补缴", dataIndex: "supplemented_total", width: 100, render: (v: string) => `￥${Number(v).toLocaleString()}` },
          {
            title: "待结清", dataIndex: "unpaid_balance", width: 100,
            render: (v: string) => (Number(v) > 0 ? <Tag color="red">￥{Number(v).toLocaleString()}</Tag> : "—"),
          },
          {
            title: "操作", key: "op", width: 230,
            render: (_, r) => (
              <Space>
                {r.status === "unpaid" && (
                  <Button type="link" size="small" onClick={async () => {
                    const order = await apiCreateDepositOrder(r.child_id);
                    message.success(`押金订单已创建（￥${Number(order.amount).toLocaleString()}），请到「会员管理 → 订单」确认收款`);
                  }}>缴押金</Button>
                )}
                {(r.status === "partially_deducted" || r.status === "fully_deducted") && (
                  <Button type="link" size="small" onClick={async () => {
                    const order = await apiCreateSupplementOrder(r.child_id);
                    message.success(`补缴订单已创建（￥${Number(order.amount).toLocaleString()}），确认收款后恢复全额`);
                  }}>补缴</Button>
                )}
                {(r.status === "paid" || r.status === "partially_deducted") && (
                  <Button type="link" size="small" onClick={() => { deductForm.resetFields(); setDeductChild(r); }}>
                    赔偿登记
                  </Button>
                )}
                <Button type="link" size="small" onClick={() => openLedgers(r)}>流水</Button>
              </Space>
            ),
          },
        ]}
      />
      <PaintPagination current={page} pageSize={pageSize} total={total} onChange={onPageChange} />

      <Drawer
        title={`押金流水 — ${ledgerChild?.child_name ?? ""}`}
        open={ledgerChild !== null} onClose={() => setLedgerChild(null)} width={480}
      >
        {ledgers.length === 0 ? (
          <Typography.Text type="secondary">暂无流水</Typography.Text>
        ) : (
          ledgers.map((l) => (
            <div key={l.id} style={{ padding: "10px 0", borderBottom: "1px dashed var(--paint-border)" }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <Tag color={l.entry_type === "deduct" ? "red" : l.entry_type === "pay" || l.entry_type === "supplement" ? "green" : "default"}>
                  {ENTRY_LABEL[l.entry_type] ?? l.entry_type}
                </Tag>
                <Typography.Text strong>￥{Number(l.amount).toLocaleString()}</Typography.Text>
              </div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {l.reason} · 余额 ￥{Number(l.balance_after).toLocaleString()} · {l.create_time?.slice(0, 16)}
              </Typography.Text>
            </div>
          ))
        )}
      </Drawer>

      <Modal
        title={`赔偿登记 — ${deductChild?.child_name ?? ""}（可用余额 ￥${deductChild ? Number(deductChild.available_amount).toLocaleString() : 0}）`}
        open={deductChild !== null} okText="登记赔偿" cancelText="取消" destroyOnClose
        onOk={async () => {
          if (!deductChild) return;
          const v = await deductForm.validateFields();
          try {
            const dep = await apiDeductDeposit(deductChild.child_id, { amount: String(v.amount), reason: v.reason });
            message.success(`已扣除 ￥${v.amount.toLocaleString()}${Number(dep.unpaid_balance) > 0 ? `，待结清 ￥${Number(dep.unpaid_balance).toLocaleString()}` : ""}`);
            setDeductChild(null);
            load(page);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "登记失败");
          }
        }}
        onCancel={() => setDeductChild(null)}
      >
        <Typography.Paragraph type="secondary">
          请先与家长线下协商确定赔偿；登记后从该孩子的押金可用余额中扣除。
        </Typography.Paragraph>
        <Form form={deductForm} layout="vertical">
          <Form.Item name="amount" label="赔偿金额（元）" rules={[{ required: true }]}>
            <InputNumber min={0.01} precision={2} style={{ width: 200 }} placeholder="按图书原价" />
          </Form.Item>
          <Form.Item name="reason" label="事由（留痕）" rules={[{ required: true }]}>
            <Input.TextArea rows={2} placeholder="如：遗失《Dog Man》按原价赔偿" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
