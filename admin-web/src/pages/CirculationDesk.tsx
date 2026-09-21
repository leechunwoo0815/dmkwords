import PaintEmpty from "../components/PaintEmpty";
import { PaintHScrollbar } from "../components/PaintHScrollbar";
import PageTitle from "../components/PageTitle";
import ScanInput, { type ScanHandle } from "../components/ScanInput";
import BorrowRecordPanel from "../components/BorrowRecordPanel";
import { useCallback, useEffect, useRef, useState } from "react";
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
  Tabs,
  Tag,
  Typography,
} from "antd";
import { SearchOutlined } from "@ant-design/icons";

import {
  apiBorrow,
  apiChildCard,
  apiChildCardByCode,
  apiListChildren,
  apiOverdueList,
  apiRenew,
  apiReturnBook,
  apiScan,
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

const ACTION_TEXT: Record<string, string> = {
  borrow: "借出", return: "归还", checkout: "预约核销借出", member: "切换读者",
};

interface RecentRow {
  seq: number;
  code: string;
  text: string;
  ok: boolean;
  time: string;
}
const RECENT_MAX = 20;

// 表格里的时间统一成"月-日 时:分"（接口给的是 ISO，直接切片会把 T 留在屏幕上看不清）
function fmtShort(v?: string | null): string {
  if (!v) return "—";
  const d = v.replace("T", " ").slice(5, 16); // YYYY-MM-DDTHH:MM → MM-DD HH:MM
  return d;
}

export default function CirculationDesk() {
  const { message, modal } = AntdApp.useApp();
  const [searchKeyword, setSearchKeyword] = useState("");
  const [searchResults, setSearchResults] = useState<{ id: number; name: string; parent_phone: string }[]>([]);
  const [card, setCard] = useState<ChildCard | null>(null);
  const [overdue, setOverdue] = useState<OverdueItem[]>([]);
  const [recent, setRecent] = useState<RecentRow[]>([]);
  const [tab, setTab] = useState("desk");
  const cardRef = useRef<ChildCard | null>(null);
  const seqRef = useRef(0);
  const memberRef = useRef<ScanHandle>(null);
  const bookRef = useRef<ScanHandle>(null);

  const setCurrentCard = useCallback((c: ChildCard | null) => {
    cardRef.current = c;
    setCard(c);
  }, []);

  const pushRecent = useCallback((code: string, text: string, ok: boolean) => {
    seqRef.current += 1;
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, "0");
    const time = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    setRecent((prev) => [{ seq: seqRef.current, code, text, ok, time }, ...prev].slice(0, RECENT_MAX));
  }, []);

  const loadOverdue = useCallback(() => {
    // F-M8/T26：逾期名单 fetch 失败必须报错——馆员误判无逾期（催还漏操作）
    apiOverdueList().then(setOverdue).catch((e: Error) => { message.error(e.message); setOverdue([]); });
  }, []);
  useEffect(loadOverdue, [loadOverdue]);

  const search = async () => {
    const r = await apiListChildren({ page: 1, page_size: 10, keyword: searchKeyword });
    setSearchResults((r.items ?? []).map((c) => ({ id: c.id, name: c.name, parent_phone: c.parent_phone })));
  };

  const openCard = async (childId: number) => {
    try {
      setCurrentCard(await apiChildCard(childId));
      setSearchResults([]);
      setTimeout(() => bookRef.current?.focus(), 0);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "加载失败");
    }
  };

  const refresh = async () => {
    const cur = cardRef.current;
    if (cur) setCurrentCard(await apiChildCard(cur.child_id));
    loadOverdue();
  };

  // ---------- 扫会员码（第一个框）----------
  // 命中即出卡片并把焦点交棒给书码框——这就是"扫完孩子自动跳到扫书"的焦点链。
  const scanMember = useCallback(
    async (code: string): Promise<boolean> => {
      try {
        const c = await apiChildCardByCode(code);
        setCurrentCard(c);
        setSearchResults([]);
        message.success(`已识别：${c.name}（${MEMBER_LABEL[c.member_status] ?? c.member_status}）`);
        pushRecent(code, `识别 ${c.name}`, true);
        setTimeout(() => bookRef.current?.focus(), 0); // 焦点交棒到书码框
        return true;
      } catch (e) {
        const msg = e instanceof Error ? e.message : "会员码识别失败";
        message.error(msg);
        pushRecent(code, msg, false);
        memberRef.current?.focus(); // 识别失败：焦点留在本框，就地重扫
        return false;
      }
    },
    [message, pushRecent, setCurrentCard],
  );

  // ---------- 扫书码（第二个框）：服务端统一判定借/还/核销 ----------
  const scanBook = useCallback(
    async (code: string): Promise<boolean> => {
      const cur = cardRef.current;
      if (!cur) {
        message.warning("请先扫会员码（或搜索选择孩子）");
        pushRecent(code, "未选读者", false);
        memberRef.current?.focus(); // 没读者就把他引回第一个框
        return false;
      }
      try {
        const r = await apiScan(cur.child_id, code);
        if (r.action === "member") {
          // 扫错框也能就地纠正：书码框读到会员码 = 换人
          if (r.card) setCurrentCard(r.card);
          message.success(`已切换到读者：${r.card?.name ?? ""}`);
          pushRecent(code, `切换读者 ${r.card?.name ?? ""}`, true);
          setTimeout(() => bookRef.current?.focus(), 0);
          return true;
        }
        const label = ACTION_TEXT[r.action] ?? r.action;
        message.success(`${label}：《${r.book_title ?? code}》`);
        (r.warnings ?? []).forEach((w) => message.warning(w, 4));
        pushRecent(code, `${label}《${r.book_title ?? ""}》`, true);
        await refresh();
        return true;
      } catch (e) {
        const errText = e instanceof Error ? e.message : "扫码失败";
        pushRecent(code, errText, false);
        // 异常借书 → 人工放行确认（填原因留痕）；「限 1 本」是硬限制不吃放行
        if (/押金|上限|未入会|过期/.test(errText) && !/限 1 本/.test(errText)) {
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
                const rec = await apiBorrow({ child_id: cur.child_id, isbn: code, override_reason: reason.trim() });
                message.success("已放行借出（留痕）");
                (rec.warnings ?? []).forEach((w) => message.warning(w, 4));
                pushRecent(code, "放行借出", true);
                await refresh();
              } catch (e2) {
                message.error(e2 instanceof Error ? e2.message : "放行失败");
                return Promise.reject(e2);
              }
            },
          });
          return false;
        }
        message.error(errText);
        return false;
      }
    },
    [message, modal, pushRecent, refresh, setCurrentCard],
  );

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
      {/* 标题：科幻风样稿（2026-09-21 用户要求"改字体+底色框，先改一页看看"）。
          留白口径同会员管理：Title margin:0（AntD 默认 margin-top 1.2em 是顶上一大片白的真凶）。 */}
      <PageTitle>借阅操作台</PageTitle>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        size="small"
        tabBarStyle={{ marginBottom: 8 }}
        items={[
          {
            key: "desk",
            label: "借还办理",
            children: (
              <>
                <Typography.Paragraph type="secondary" style={{ marginBottom: 8, fontSize: 13 }}>
                  ① 扫会员码 → ② 连扫图书 ISBN，系统自动判断借出 / 归还 / 预约核销；异常可人工放行留痕。
                </Typography.Paragraph>

      {/* 扫码区：进页面焦点自动落在第一个框，识别成功后自动交棒给书码框（2026-09-21 C 批） */}
      <Card
        size="small"
        style={{ marginBottom: 16, borderColor: card ? "var(--paint-secondary)" : undefined }}
      >
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Space wrap>
            <Typography.Text className="scan-step scan-step-member">① 扫会员码</Typography.Text>
            {card ? (
              <Tag color="green">当前读者：{card.name}</Tag>
            ) : (
              <Tag color="orange">等待扫会员码…</Tag>
            )}
            {card && (
              <Button
                size="small"
                type="link"
                onClick={() => {
                  setCurrentCard(null);
                  memberRef.current?.focus(); // 换人：焦点回第一个框
                }}
              >
                结束本次（换人）
              </Button>
            )}
          </Space>
          <div className="scan-block scan-block-member">
          <ScanInput
            ref={memberRef}
            onScan={scanMember}
            // 切回「借还办理」tab 时重新聚焦第一个框（Tabs 会把 pane 留在 DOM 里，焦点不会自己回来）
            refocusKey={tab}
            // 关键：本框提交后**不许自己回焦**，否则会把交棒给书码框的焦点抢回来
            refocusAfterSubmit={false}
            placeholder="扫码枪扫小程序的「会员码」，或手工输入后回车"
            actionLabel="识别"
            hint="孩子到店：小程序首页 →「会员码」→ 出示给扫码枪"
          />
          </div>

          <Space wrap>
            <Typography.Text className="scan-step scan-step-book">② 扫图书 ISBN</Typography.Text>
            <Tag color={card ? "green" : "default"}>
              {card ? "扫哪本办哪本：自动判借 / 还" : "先扫会员码（或搜索选择孩子）"}
            </Tag>
          </Space>
          <div className="scan-block scan-block-book">
          <ScanInput
            ref={bookRef}
            onScan={scanBook}
            // 不自动抢焦点：进页面焦点必须留在会员码框，识别成功后才由焦点链交棒过来
            focusDelays={[]}
            placeholder="扫码枪扫图书条码，或手工输入 ISBN 后回车"
            actionLabel="提交"
            hint="已借出去的书再扫一次＝归还；自己预约的书扫了自动核销借出"
          />
          </div>

          {recent.length > 0 && (
            <div>
              <Space style={{ marginBottom: 4 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  本次操作（{recent.length}）
                </Typography.Text>
                <Button type="link" size="small" onClick={() => setRecent([])}>
                  清空
                </Button>
              </Space>
              <div style={{ maxHeight: 150, overflowY: "auto" }}>
                {recent.map((r) => (
                  <Space key={r.seq} size={8} style={{ display: "flex", fontSize: 12, lineHeight: "20px" }}>
                    <Typography.Text type="secondary">{r.time}</Typography.Text>
                    <Typography.Text code style={{ fontSize: 12 }}>
                      {r.code}
                    </Typography.Text>
                    <span>{r.text}</span>
                    <Tag color={r.ok ? "green" : "red"} style={{ marginRight: 0 }}>
                      {r.ok ? "成功" : "失败"}
                    </Tag>
                  </Space>
                ))}
              </div>
            </div>
          )}
        </Space>
      </Card>

      {/* 手工兜底：会员码扫不出来（没带手机/码模糊）时按姓名或手机号找孩子 */}
      <Space.Compact style={{ width: 420, marginBottom: 16 }}>
        <Input
          size="large" placeholder="孩子姓名 / 家长手机号（会员码扫不出时用）" value={searchKeyword}
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
            extra={<Button size="small" onClick={() => setCurrentCard(null)}>关闭</Button>}
          >
            <Row gutter={16}>
              <Col span={4}><Card size="small"><Typography.Text type="secondary">在借</Typography.Text><div style={{ fontSize: 22, fontWeight: 700 }}>{card.active_borrows}</div></Card></Col>
              <Col span={4}><Card size="small"><Typography.Text type="secondary">其中逾期</Typography.Text><div style={{ fontSize: 22, fontWeight: 700, color: card.overdue_count > 0 ? "var(--paint-danger)" : undefined }}>{card.overdue_count}</div></Card></Col>
              <Col span={4}><Card size="small"><Typography.Text type="secondary">可借</Typography.Text><div style={{ fontSize: 22, fontWeight: 700, color: card.borrow_block ? "var(--paint-ink-light)" : "var(--paint-secondary)" }}>{card.available_quota}</div></Card></Col>
              <Col span={4}><Card size="small"><Typography.Text type="secondary">押金</Typography.Text><div style={{ marginTop: 6 }}>{card.deposit_status === "paid" ? <Tag color="green">已缴 ￥{Number(card.deposit_available).toLocaleString()}</Tag> : <Tag color="red">{card.deposit_status === "unpaid" ? "未缴" : "异常"}</Tag>}</div></Card></Col>
            </Row>
            {/* 2026-09-21 用户反馈：未入会竟然显示"可借 30 本"——额度是数字，资格才是能不能借。
                这里与借书守卫同源（后端 borrow_gate 判定），不可借时说清为什么、能不能人工放行。 */}
            {card.borrow_block && (
              <Alert
                style={{ marginTop: 12 }}
                type={card.borrow_block_hard ? "error" : "warning"}
                showIcon
                message={`当前不可借：${card.borrow_block}`}
                description={
                  card.borrow_block_hard
                    ? "这是硬拦截，填放行原因也借不出（需先处理上面的状态）"
                    : "线下可人工放行并填写原因（会写进审计日志）"
                }
              />
            )}
          </Card>

          {/* 在借列表 */}
          <Table locale={{ emptyText: <PaintEmpty character="bear" /> }}
            rowKey="id" size="small" pagination={false} dataSource={card.records}
            columns={[
              // 2026-09-21 用户反馈：原表只有日期，"谁知道是什么书" → 补书名 + 副本码
              {
                title: "书名",
                dataIndex: "book_title",
                render: (v: string) => v || "—",
              },
              { title: "副本码", dataIndex: "copy_code", width: 180, render: (v: string) => v || "—" },
              {
                title: "借出时间",
                dataIndex: "borrowed_at",
                width: 130,
                render: (v: string) => fmtShort(v),
              },
              { title: "到期日", dataIndex: "due_at", width: 130, render: (v: string) => fmtShort(v) },
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
            ),
          },
          {
            key: "records",
            label: "借还记录",
            children: <BorrowRecordPanel active={tab === "records"} />,
          },
        ]}
      />
    </>
  );
}
