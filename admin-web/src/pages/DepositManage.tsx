import PaintEmpty from "../components/PaintEmpty";
import { cfgNum, money, useConfigs } from "../hooks/useConfigs";
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
import { PaintHScrollbar } from "../components/PaintHScrollbar";
import PageTitle from "../components/PageTitle";

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
  const { configs, failed: configsFailed } = useConfigs();
  const { message } = AntdApp.useApp();
  const [deposits, setDeposits] = useState<Deposit[]>([]);
  const [total, setTotal] = useState(0);
  const { page, pageSize, setPage, onChange: onPageChange } = usePaintPagination();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  // P2-62：列表取数失败标记（空表会骗人——运营以为"没有押金记录"）
  const [listError, setListError] = useState("");
  // B8（2026-10-28）：建单请求在途时禁用按钮——双击会各发一次（服务端在途闸会拦，但界面出错误 toast）
  const [creatingId, setCreatingId] = useState<number | null>(null);
  const [ledgerChild, setLedgerChild] = useState<Deposit | null>(null);
  const [ledgers, setLedgers] = useState<DepositLedger[]>([]);
  // P1-7（第九轮审查）：流水取数失败标记——原先失败时抽屉里留着**上一个孩子**的流水
  // （`await` 抛错后 setLedgers 没执行，旧数据原样在），且失败后显示「暂无流水」也是在说谎
  const [ledgerError, setLedgerError] = useState("");
  const [deductChild, setDeductChild] = useState<Deposit | null>(null);
  const [deductForm] = Form.useForm();

  const load = useCallback(
    (targetPage: number) => {
      setLoading(true);
      setListError("");
      apiListDeposits({ page: targetPage, page_size: pageSize, status, keyword: keyword || undefined })
        .then((r) => { setDeposits(r.items ?? []); setTotal(r.total); })
        .catch((e: Error) => { message.error(e.message); setDeposits([]); setListError(e.message); })
        .finally(() => setLoading(false));
    },
    [status, keyword, pageSize, message]
  );

  useEffect(() => { load(page); }, [load, page]);

  const openLedgers = async (dep: Deposit) => {
    setLedgerChild(dep);
    setLedgers([]); // P1-7：先清空——否则抽屉在取数期间（或失败后）显示的是**上一个孩子**的流水
    setLedgerError("");
    try {
      setLedgers(await apiGetDepositLedgers(dep.child_id));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "流水加载失败";
      setLedgerError(msg);
      message.error(msg);
    }
  };

  return (
    <>
      <PageTitle>押金与赔偿</PageTitle>
      <Typography.Paragraph type="secondary">
        押金按孩子独立（{money(cfgNum(configs, "deposit_amount", 1200))} 元/人）；遗失损坏按原价赔偿、优先扣本人押金；不足部分记「待结清」并持续提醒。
      </Typography.Paragraph>
      {/* P2-55：配置没取到时金额是**默认值**——如实提示，别让运营照默认值办事 */}
      {configsFailed && (
        <Typography.Text type="warning" style={{ display: "block", marginBottom: 12 }}>
          价格配置未取到：上方金额按默认值展示，可能与实际收费不一致——请刷新或到「系统配置」确认。
        </Typography.Text>
      )}

      <Space style={{ marginBottom: 12 }}>
        <Input.Search
          placeholder="孩子姓名" allowClear style={{ width: 200 }}
          onSearch={(v) => { setKeyword(v); setPage(1); }}
        />
        <Select
          placeholder="押金状态" style={{ width: 130 }} value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          // R9 sweep：押金状态筛选补显式「全部」（A4 口径，替 allowClear）
          options={[{ value: "", label: "全部" },
            ...Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label }))]}
        />
      </Space>

      {/* P2-62：取数失败**不许**渲染成空表（运营会以为"没有押金记录"） */}
      {listError && (
        <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
          {listError} · 列表未加载，不代表没有记录——请重试
          <Typography.Link onClick={() => load(page)} style={{ marginLeft: 8 }}>重试</Typography.Link>
        </Typography.Text>
      )}
      <Table<Deposit> locale={{ emptyText: listError ? null : <PaintEmpty character="cat" /> }}
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
                  <Button type="link" size="small" loading={creatingId === r.child_id}
                    disabled={creatingId !== null} onClick={async () => {
                    // P1-7：原先无 catch——接口挂掉时 promise 静默 reject，运营看到的是"点了没反应"
                    setCreatingId(r.child_id);
                    try {
                      const order = await apiCreateDepositOrder(r.child_id);
                      message.success(`押金订单已创建（￥${Number(order.amount).toLocaleString()}），请到「会员管理 → 订单」确认收款`);
                    } catch (e) {
                      message.error(e instanceof Error ? e.message : "创建押金订单失败");
                    } finally {
                      setCreatingId(null);
                    }
                  }}>缴押金</Button>
                )}
                {(r.status === "partially_deducted" || r.status === "fully_deducted") && (
                  <Button type="link" size="small" loading={creatingId === r.child_id}
                    disabled={creatingId !== null} onClick={async () => {
                    setCreatingId(r.child_id);
                    try {
                      const order = await apiCreateSupplementOrder(r.child_id);
                      message.success(`补缴订单已创建（￥${Number(order.amount).toLocaleString()}），确认收款后恢复全额`);
                    } catch (e) {
                      message.error(e instanceof Error ? e.message : "创建补缴订单失败");
                    } finally {
                      setCreatingId(null);
                    }
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
       scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
      <PaintPagination current={page} pageSize={pageSize} total={total} onChange={onPageChange} />

      <Drawer
        title={`押金流水 — ${ledgerChild?.child_name ?? ""}`}
        open={ledgerChild !== null} onClose={() => setLedgerChild(null)} width={480}
      >
        {ledgerError ? (
          // P1-7：失败要说"未加载"，不能说"暂无"（同列表侧的 listError 口径）
          <Typography.Text type="warning" style={{ display: "block" }}>
            {ledgerError} · 流水未加载，不代表没有记录——请重试
            <Typography.Link
              onClick={() => ledgerChild && openLedgers(ledgerChild)}
              style={{ marginLeft: 8 }}
            >
              重试
            </Typography.Link>
          </Typography.Text>
        ) : ledgers.length === 0 ? (
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
        open={deductChild !== null} okText="登记赔偿" cancelText="取消" destroyOnHidden
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
