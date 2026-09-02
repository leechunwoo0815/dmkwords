import PaintEmpty from "../components/PaintEmpty";
import { PaintHScrollbar } from "../components/PaintHScrollbar";
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
  Input,
  Row,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { ScanOutlined, SearchOutlined } from "@ant-design/icons";

import {
  apiBorrow,
  apiChildCard,
  apiListChildren,
  apiOverdueList,
  apiRenew,
  apiReturnBook,
  type BorrowRecordResponse,
  type ChildCard,
  type OverdueItem,
} from "../api/circulation";

const MEMBER_LABEL: Record<string, string> = {
  none: "未入会", observation: "观察期", pending_evaluation: "待评估",
  formal: "正式会员", expired: "已过期", withdrawn: "已退会",
};
const MEMBER_COLOR: Record<string, string> = {
  none: "default", observation: "blue", pending_evaluation: "orange",
  formal: "green", expired: "red", withdrawn: "default",
};

export default function CirculationDesk() {
  const { message, modal } = AntdApp.useApp();
  const [searchKeyword, setSearchKeyword] = useState("");
  const [searchResults, setSearchResults] = useState<{ id: number; name: string; parent_phone: string }[]>([]);
  const [card, setCard] = useState<ChildCard | null>(null);
  const [isbn, setIsbn] = useState("");
  const [busy, setBusy] = useState(false);
  const [overdue, setOverdue] = useState<OverdueItem[]>([]);

  const loadOverdue = useCallback(() => {
    apiOverdueList().then(setOverdue).catch(() => undefined);
  }, []);
  useEffect(loadOverdue, [loadOverdue]);

  const search = async () => {
    const r = await apiListChildren({ page: 1, page_size: 10, keyword: searchKeyword });
    setSearchResults((r.items ?? []).map((c) => ({ id: c.id, name: c.name, parent_phone: c.parent_phone })));
  };

  const openCard = async (childId: number) => {
    try {
      setCard(await apiChildCard(childId));
    } catch (e) {
      message.error(e instanceof Error ? e.message : "加载失败");
    }
  };

  const refresh = async () => {
    if (card) setCard(await apiChildCard(card.child_id));
    loadOverdue();
  };

  const doBorrow = async (override = false) => {
    if (!card || !isbn.trim()) return;
    setBusy(true);
    try {
      const rec = await apiBorrow({
        child_id: card.child_id,
        isbn: isbn.trim(),
        ...(override ? { override_reason: "" } : {}),
      });
      message.success("借书成功");
      // 软提示不拦截（C16 AR 超范围 / C15 未入会 72h 等）
      (rec.warnings ?? []).forEach((w) => message.warning(w, 4));
      setIsbn("");
      await refresh();
    } catch (e) {
      const errText = e instanceof Error ? e.message : "借书失败";
      // 异常借书 → 人工放行确认（填原因留痕）；「限 1 本」为硬限制不吃放行
      if (!override && /押金|上限|未入会|过期/.test(errText) && !/限 1 本/.test(errText)) {
        let reason = "";
        modal.confirm({
          title: "异常借书 — 人工放行",
          content: (
            <div>
              <Alert type="warning" message={errText} style={{ marginBottom: 12 }} />
              <Input.TextArea
                rows={2}
                placeholder="放行原因（必填，写入审计日志）"
                onChange={(e) => { reason = e.target.value; }}
              />
            </div>
          ),
          okText: "放行并借出",
          cancelText: "取消",
          onOk: async () => {
            if (!reason.trim()) { message.warning("必须填写放行原因"); return Promise.reject(); }
            try {
              const rec2 = await apiBorrow({ child_id: card.child_id, isbn: isbn.trim(), override_reason: reason.trim() });
              message.success("已放行借出（留痕）");
              (rec2.warnings ?? []).forEach((w) => message.warning(w, 4));
              setIsbn("");
              await refresh();
            } catch (e2) {
              message.error(e2 instanceof Error ? e2.message : "放行失败");
              return Promise.reject(e2);
            }
          },
        });
      } else {
        message.error(errText);
      }
    } finally {
      setBusy(false);
    }
  };

  const doReturn = (record: { copy_id: number; id: number }) => {
    modal.confirm({
      title: "归还确认",
      content: "请检查图书实物状态后选择：",
      okText: "正常归架",
      cancelText: "取消",
      onOk: async () => {
        await apiReturnBook(record.copy_id, "normal");
        message.success("归还成功");
        await refresh();
      },
      footer: (_, { OkBtn, CancelBtn }) => (
        <Space>
          <CancelBtn />
          <Button danger onClick={async () => {
            try {
              await apiReturnBook(record.copy_id, "lost");
              message.success("已标记遗失，请到「押金与赔偿」登记赔偿");
              await refresh();
            } catch (e) { message.error(e instanceof Error ? e.message : "操作失败"); }
          }}>标记遗失</Button>
          <Button onClick={async () => {
            try {
              await apiReturnBook(record.copy_id, "maintenance");
              message.success("已转维护");
              await refresh();
            } catch (e) { message.error(e instanceof Error ? e.message : "操作失败"); }
          }}>转维护</Button>
          <OkBtn />
        </Space>
      ),
    });
  };

  return (
    <>
      <Typography.Title level={4} style={{ fontFamily: "var(--font-display)" }}>
        借阅操作台
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        搜索孩子 → 出示卡片 → 扫 ISBN 借书；异常情况（押金未缴/超上限）可人工放行并留痕。
      </Typography.Paragraph>

      {/* 搜索孩子 */}
      <Space.Compact style={{ width: 420, marginBottom: 16 }}>
        <Input
          size="large" placeholder="孩子姓名 / 家长手机号" value={searchKeyword}
          onChange={(e) => setSearchKeyword(e.target.value)}
          onPressEnter={search} prefix={<SearchOutlined />}
        />
        <Button size="large" type="primary" onClick={search}>搜索</Button>
      </Space.Compact>

      {searchResults.length > 0 && !card && (
        <Card size="small" style={{ marginBottom: 16 }}>
          {searchResults.map((s) => (
            <Button key={s.id} type="link" onClick={() => openCard(s.id)}>
              {s.name}（{s.parent_phone}）
            </Button>
          ))}
        </Card>
      )}

      {card && (
        <>
          {/* 孩子卡片 */}
          <Card
            size="small" style={{ marginBottom: 16, border: card.overdue_count > 0 ? "1px solid var(--paint-danger)" : undefined }}
            title={
              <Space>
                <Typography.Text strong style={{ fontSize: 16 }}>{card.name}</Typography.Text>
                <Tag color={MEMBER_COLOR[card.member_status]}>{MEMBER_LABEL[card.member_status]}</Tag>
                <Typography.Text type="secondary">{card.parent_name} · {card.parent_phone}</Typography.Text>
              </Space>
            }
            extra={<Button size="small" onClick={() => setCard(null)}>关闭</Button>}
          >
            <Row gutter={16}>
              <Col span={4}><Card size="small"><Typography.Text type="secondary">在借</Typography.Text><div style={{ fontSize: 22, fontWeight: 700 }}>{card.active_borrows}</div></Card></Col>
              <Col span={4}><Card size="small"><Typography.Text type="secondary">逾期</Typography.Text><div style={{ fontSize: 22, fontWeight: 700, color: card.overdue_count > 0 ? "var(--paint-danger)" : undefined }}>{card.overdue_count}</div></Card></Col>
              <Col span={4}><Card size="small"><Typography.Text type="secondary">可借</Typography.Text><div style={{ fontSize: 22, fontWeight: 700, color: "var(--paint-secondary)" }}>{card.available_quota}</div></Card></Col>
              <Col span={4}><Card size="small"><Typography.Text type="secondary">押金</Typography.Text><div style={{ marginTop: 6 }}>{card.deposit_status === "paid" ? <Tag color="green">已缴 ￥{Number(card.deposit_available).toLocaleString()}</Tag> : <Tag color="red">{card.deposit_status === "unpaid" ? "未缴" : "异常"}</Tag>}</div></Card></Col>
            </Row>
          </Card>

          {/* 扫码借书 */}
          <Card size="small" style={{ marginBottom: 16 }}>
            <Space.Compact style={{ width: 380 }}>
              <Input
                size="large" placeholder="扫图书 ISBN 条码或输入 ISBN" value={isbn}
                onChange={(e) => setIsbn(e.target.value)} prefix={<ScanOutlined />}
                onPressEnter={() => doBorrow()} disabled={busy}
              />
              <Button size="large" type="primary" loading={busy} onClick={() => doBorrow()}>借出</Button>
            </Space.Compact>
          </Card>

          {/* 在借列表 */}
          <Table locale={{ emptyText: <PaintEmpty character="bear" /> }}
            rowKey="id" size="small" pagination={false} dataSource={card.records}
            columns={[
              { title: "借出时间", dataIndex: "borrowed_at", width: 170, render: (v: string) => v?.slice(0, 16) },
              { title: "到期日", dataIndex: "due_at", width: 170, render: (v: string) => v?.slice(0, 16) },
              { title: "状态", dataIndex: "status", width: 90, render: (s: string) => s === "overdue" ? <Tag color="red">逾期</Tag> : <Tag color="blue">借出中</Tag> },
              { title: "续借", dataIndex: "renew_used", width: 90, render: (v: number, r: BorrowRecordResponse) => v >= 1 ? <Tag>已用</Tag> : <Button type="link" size="small" onClick={async () => {
                try {
                  await apiRenew(r.id);  // WM5-F1：用被点击行的 r.id，原 find(...) 全局查找导致多本在借时永远续第 1 行
                  message.success("续借成功（+7 天）");
                  await refresh();
                } catch (e) { message.error(e instanceof Error ? e.message : "续借失败"); }
              }}>续借</Button> },
              { title: "操作", key: "op", width: 100, render: (_, r) => (
                <Button type="primary" size="small" onClick={() => doReturn(r)}>还书</Button>
              ) },
            ]}
           scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
        </>
      )}

      {/* 逾期名单 */}
      <Typography.Title level={5} style={{ fontFamily: "var(--font-display)", marginTop: 24 }}>
        逾期名单（{overdue.length}）
      </Typography.Title>
      <Table<OverdueItem> locale={{ emptyText: <PaintEmpty character="bear" /> }}
        rowKey="record_id" size="small" pagination={false} dataSource={overdue}
        columns={[
          { title: "孩子", dataIndex: "child_name", width: 100 },
          { title: "家长电话", dataIndex: "parent_phone", width: 130 },
          { title: "书名", dataIndex: "book_title" },
          { title: "到期日", dataIndex: "due_at", width: 170, render: (v: string) => v?.slice(0, 16) },
          { title: "逾期天数", dataIndex: "days_overdue", width: 100, render: (d: number) => <Tag color={d > 7 ? "red" : "orange"}>{d} 天</Tag> },
        ]}
       scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
    </>
  );
}
