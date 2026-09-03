import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Input,
  Modal,
  Select,
  Table,
  Tabs,
  Tag,
  Typography,
  App as AntdApp,
} from "antd";
import { DownloadOutlined, LinkOutlined } from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";

import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
import { usePaintPagination } from "../hooks/usePaintPagination";
import { TODO_REFRESH_EVENT, useTodoCounts } from "../hooks/useTodoCounts";
import { PaintHScrollbar } from "../components/PaintHScrollbar";
import {
  AdminNotification,
  apiExportNotifications,
  apiHandleAdminInbox,
  apiListAdminInbox,
  apiListNotifications,
  apiToggleNotificationRead,
  AdminInboxItem,
} from "../api/admin";

const SCENE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部场景" },
  { value: "money.order_paid", label: "付款成功" },
  { value: "money.refund_result", label: "退款审核结果" },
  { value: "money.refund_received", label: "退款到账" },
  { value: "money.refund_failed", label: "退款失败" },
  { value: "money.deposit_paid", label: "押金补缴" },
  { value: "borrow.success", label: "借书成功" },
  { value: "borrow.returned", label: "还书成功" },
  { value: "borrow.due_remind", label: "借阅到期提醒" },
  { value: "borrow.overdue", label: "图书已逾期" },
  { value: "reading.quiz_result", label: "测验成绩" },
  { value: "reading.milestone", label: "达成里程碑" },
  { value: "reading.level_up", label: "等级升级" },
  { value: "member.expire_remind", label: "会员到期提醒" },
  { value: "member.withdraw_result", label: "退会审核结果" },
  { value: "activity.enroll", label: "活动报名成功" },
  { value: "activity.remind", label: "活动开始提醒" },
  { value: "activity.cancel", label: "活动取消" },
  { value: "reservation.expiring", label: "预约即将到期" },
  { value: "reservation.released", label: "预约已释放" },
  { value: "report.generated", label: "报告生成" },
  { value: "other.evaluation_uploaded", label: "评估报告上传" },
  { value: "other.transfer_result", label: "权益转让审核" },
];

// WM13 管理待办事项类型（6 场景，Q5 裁定）
const ADMIN_SCENE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部事项" },
  { value: "admin.refund_apply", label: "退款申请" },
  { value: "admin.withdrawal_apply", label: "退会申请" },
  { value: "admin.transfer_apply", label: "权益转让" },
  { value: "admin.activity_batch_refund", label: "活动批量退款" },
  { value: "admin.transfer_expiring", label: "转让临近超时" },
  { value: "admin.refund_execute_failed", label: "退款执行失败" },
];

// 关联对象中文标签 + 管理端跳转路由（N1：order/activity/report 等可跳；
// borrow_record 无明细列表，归入详情展开，不跳转——审查 Q1/Q3 裁决）
const REF_META: Record<string, { label: string; route?: (r: AdminNotification) => string }> = {
  order: { label: "订单", route: () => "/members?tab=orders" },
  activity: { label: "活动", route: () => "/activities" },
  report: { label: "报告", route: (r) => (r.child_id ? `/growth?child_id=${r.child_id}` : "/growth") },
  reservation: { label: "预约", route: () => "/reservations" },
  transfer: { label: "权益转让", route: () => "/refund-center" },
  withdrawal_request: { label: "退会申请", route: () => "/refund-center" },
  refund_request: { label: "退款申请", route: () => "/refund-center" },
  deposit: { label: "押金", route: () => "/deposits" },
  child: { label: "孩子", route: () => "/members" },
  borrow_record: { label: "借阅记录（详情见上）" },
  parent: { label: "家长" },
};

// WM13 管理待办跳转（最后一公里）：定位 tab + highlight 单据 id（RefundCenter 只读解析）
const ADMIN_REF_ROUTE: Record<string, { path: string; tab: string }> = {
  refund_request: { path: "/refund-center", tab: "refunds" },
  withdrawal_request: { path: "/refund-center", tab: "withdrawals" },
  transfer: { path: "/refund-center", tab: "transfers" },
  activity: { path: "/activities", tab: "" },
};

function wechatTag(status: string): React.ReactNode {
  switch (status) {
    case "sent":
      return <Tag color="green">微信已送达</Tag>;
    case "failed":
      return <Tag color="red">微信失败</Tag>;
    case "skipped":
      return <Tag color="orange">微信跳过</Tag>;
    default:
      return <Tag color="default">未发送</Tag>;
  }
}

// WM13 等待时长（Q11 裁定：<1h 刚提交 / 1-24h 小时 / >24h 红字天+小时）
function waitCell(created: string): React.ReactNode {
  const ms = Date.now() - new Date(created).getTime();
  if (!(ms > 0)) return "—";
  const hours = ms / 3600000;
  if (hours < 1) return "刚刚提交";
  if (hours < 24) return `已等待 ${Math.floor(hours)} 小时`;
  const days = Math.floor(hours / 24);
  const h = Math.floor(hours % 24);
  return (
    <span style={{ color: "#cf1322", fontWeight: 600 }}>
      已等待 {days} 天 {h} 小时
    </span>
  );
}

export default function Notifications() {
  const { message } = AntdApp.useApp(); // F-L4/T34：App context 化（禁静态 message）
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const box = searchParams.get("box") === "admin" ? "admin" : "parent";
  const { page, setPage, pageSize, setPageSize } = usePaintPagination(
    20,
    Number(searchParams.get("page")) || 1
  );
  // URL 五态镜像（WM13 批次二：box/tab/page/keyword/scene，replace 不堆栈）
  // tab 双语义：box=parent 时 all/unread/read；box=admin 时 todo/done
  const initialTab = searchParams.get("tab");
  const [parentTab, setParentTab] = useState<string>(
    initialTab === "unread" || initialTab === "read" ? initialTab : "all"
  );
  const [adminTab, setAdminTab] = useState<string>(initialTab === "done" ? "done" : "todo");
  const [keyword, setKeyword] = useState<string>(searchParams.get("keyword") ?? "");
  const [scene, setScene] = useState<string | undefined>(searchParams.get("scene") ?? undefined);
  // 家长通知（现状平移）
  const [items, setItems] = useState<AdminNotification[]>([]);
  const [total, setTotal] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [allCount, setAllCount] = useState(0);
  const [loading, setLoading] = useState(false);
  // 管理待办（WM13）
  const [inbox, setInbox] = useState<AdminInboxItem[]>([]);
  const [inboxTotal, setInboxTotal] = useState(0);
  const { counts: todoCounts, failed: todoFailed } = useTodoCounts();  // WM13-F4：与徽标同源
  const [inboxLoading, setInboxLoading] = useState(false);
  const [handleTarget, setHandleTarget] = useState<AdminInboxItem | null>(null);
  const [handleReason, setHandleReason] = useState("");
  const [handleSaving, setHandleSaving] = useState(false);

  const mirrorUrl = useCallback(
    (next: { box: string; tab: string; page: number; keyword: string; scene?: string }) => {
      const params: Record<string, string> = {};
      if (next.box !== "parent") params.box = next.box;
      const tabDefault = next.box === "admin" ? "todo" : "all";
      if (next.tab !== tabDefault) params.tab = next.tab;
      if (next.page > 1) params.page = String(next.page);
      if (next.keyword) params.keyword = next.keyword;
      if (next.scene) params.scene = next.scene;
      setSearchParams(params, { replace: true });
    },
    [setSearchParams]
  );

  const loadParent = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiListNotifications({
        page,
        page_size: pageSize,
        category: undefined,
        scene,
        parent_name: keyword || undefined,
        unread: parentTab === "unread",
        read: parentTab === "read",
      });
      setItems(data.items);
      setTotal(data.total);
      setUnreadCount(data.unread);
      setAllCount(data.all_count);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, scene, keyword, parentTab]);

  const loadInbox = useCallback(async () => {
    setInboxLoading(true);
    try {
      const data = await apiListAdminInbox({
        page,
        page_size: pageSize,
        status_filter: adminTab === "todo" ? "pending" : "finished",
        scene,
        keyword: keyword || undefined,
      });
      setInbox(data.items);
      setInboxTotal(data.total);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setInboxLoading(false);
    }
  }, [page, pageSize, scene, keyword, adminTab]);

  useEffect(() => {
    if (box === "admin") void loadInbox();
    else void loadParent();
  }, [box, loadParent, loadInbox]);

  // W2：列表 60s 轮询（与 useTodoCounts 同周期）——小程序申请新退款后管理端
  // 列表不刷新看不到（徽标轮询有、列表没有）。页面隐藏时暂停避免后台空转。
  useEffect(() => {
    // X4：不因挂载时隐藏而跳过注册——后台标签页打开本页时，切回后轮询必须已就位
    const id = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (box === "admin") void loadInbox();
      else void loadParent();
    }, 60_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        if (box === "admin") void loadInbox();
        else void loadParent();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [box, loadParent, loadInbox]);

  const changeBox = (next: string) => {
    const b = next === "admin" ? "admin" : "parent";
    const tab = b === "admin" ? "todo" : "all";
    setAdminTab(b === "admin" ? adminTab : "todo");
    setParentTab(b === "parent" ? "all" : parentTab);
    setPage(1);
    mirrorUrl({ box: b, tab, page: 1, keyword: "", scene: undefined });
    setKeyword("");
    setScene(undefined);
  };

  const changeParentTab = (next: string) => {
    setParentTab(next);
    setPage(1);
    mirrorUrl({ box, tab: next, page: 1, keyword, scene });
  };

  const changeAdminTab = (next: string) => {
    setAdminTab(next);
    setPage(1);
    mirrorUrl({ box, tab: next, page: 1, keyword, scene });
  };

  const toggleRead = useCallback(
    async (r: AdminNotification) => {
      try {
        const resp = await apiToggleNotificationRead(r.id, !r.read, "通知中心协助标记");
        // 未读 Tab 标已读 / 已读 Tab 标未读（C42）→ 该行从列表消失；全部 Tab 行内翻转
        if ((parentTab === "unread" && !r.read) || (parentTab === "read" && r.read)) {
          setItems((prev) => prev.filter((i) => i.id !== r.id));
          setTotal((t) => Math.max(0, t - 1));
        } else {
          setItems((prev) => prev.map((i) => (i.id === r.id ? { ...i, read: !r.read } : i)));
        }
        // Tab 计数以服务端口径为准（F1b/C37）；有筛选时响应为全局口径不适用，本地推算
        if (!scene && !keyword) {
          setUnreadCount(resp.unread_count);
          if (parentTab === "unread") setTotal(resp.unread_count);
          else if (parentTab === "read") setTotal(Math.max(0, allCount - resp.unread_count));
        } else {
          setUnreadCount((c) => (r.read ? c + 1 : Math.max(0, c - 1)));
        }
      } catch (e) {
        message.error((e as Error).message);
      }
    },
    [parentTab, scene, keyword, allCount]
  );

  const submitHandle = useCallback(async () => {
    if (!handleTarget) return;
    if (!handleReason.trim()) {
      message.error("必须填写处理原因（留痕）");
      return;
    }
    setHandleSaving(true);
    try {
      await apiHandleAdminInbox(handleTarget.id, handleReason.trim());
      message.success("已标记为已处理");
      setHandleTarget(null);
      setHandleReason("");
      // L3：徽标计数即时刷新
      window.dispatchEvent(new Event(TODO_REFRESH_EVENT));
      void loadInbox();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setHandleSaving(false);
    }
  }, [handleTarget, handleReason, loadInbox]);

  const expandContent = useMemo(
    () => (r: AdminNotification) => {
      const meta = REF_META[r.ref_type];
      // F2/C38：ref_id 是数据库内部 ID，馆员无意义——标签只留中文类型名
      const refText = r.ref_type ? (meta?.label ?? r.ref_type) : "无关联对象";
      const refRoute = meta?.route?.(r);
      return (
        <div style={{ padding: "4px 8px 8px" }}>
          <div style={{ color: "rgba(0,0,0,0.75)" }}>
            <Typography.Text>完整内容：{r.content}</Typography.Text>
          </div>
          <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 8 }}>
            <Tag color="blue">{refText}</Tag>
            {refRoute && (
              <Button
                size="small"
                icon={<LinkOutlined />}
                onClick={() => {
                  navigate(refRoute);
                }}
              >
                查看关联
              </Button>
            )}
          </div>
        </div>
      );
    },
    [navigate]
  );

  // WM13 管理待办表格
  const inboxColumns = [
    { title: "时间", dataIndex: "created_at", width: 140, render: (v: string) => (v || "").replace("T", " ").slice(0, 16) },
    { title: "事项", dataIndex: "title", width: 130 },
    { title: "申请人", dataIndex: "applicant_name", width: 150 },
    {
      title: "金额",
      dataIndex: "amount",
      width: 100,
      render: (v: string | null) => (v ? `￥${Number(v).toLocaleString()}` : "—"),
    },
    {
      title: "状态",
      dataIndex: "status_text",
      width: 170,
      render: (v: string, r: AdminInboxItem) =>
        r.effective_status === "pending" ? (
          <Tag color="orange">{v}</Tag>
        ) : (
          <span style={{ color: "rgba(0,0,0,0.45)" }}>
            {v}
            {r.effective_status === "done" && r.handled_by_name ? ` · ${r.handled_by_name}` : ""}
          </span>
        ),
    },
    {
      title: "已等待",
      dataIndex: "created_at",
      width: 150,
      render: (v: string, r: AdminInboxItem) => (r.effective_status === "pending" ? waitCell(v) : "—"),
    },
    {
      title: "操作",
      key: "action",
      width: 200,
      render: (_: unknown, r: AdminInboxItem) => {
        const meta = ADMIN_REF_ROUTE[r.ref_type];
        const route = meta
          ? meta.tab
            ? `${meta.path}?tab=${meta.tab}&highlight=${r.ref_id}`
            : meta.path
          : undefined;
        return (
          <span style={{ display: "inline-flex", gap: 8 }}>
            {route && (
              <Button size="small" type="link" icon={<LinkOutlined />} onClick={() => navigate(route)}>
                {r.effective_status === "pending" ? "去处理" : "查看"}
              </Button>
            )}
            {r.effective_status === "pending" && (
              <Button
                size="small"
                onClick={() => {
                  setHandleTarget(r);
                  setHandleReason("");
                }}
              >
                标记已处理
              </Button>
            )}
          </span>
        );
      },
    },
  ];

  // WM13-F4：tab 标题与徽标同源（admin_total）；失败/未拉到 → 不显数字（U10 禁假 0）
  const todoTabCount = todoFailed || !todoCounts ? null : todoCounts.admin_total;
  const adminTabLabel =
    todoTabCount === null ? "管理待办" : `管理待办（${todoTabCount} 待处理）`;

  const parentPanel = (
    <>
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <Select
          value={parentTab === "unread" || parentTab === "read" ? parentTab : "all"}
          style={{ width: 150 }}
          options={[
            { value: "all", label: `全部（${allCount}）` },
            { value: "unread", label: `未读（${unreadCount}）` },
            { value: "read", label: `已读（${Math.max(0, allCount - unreadCount)}）` },
          ]}
          onChange={changeParentTab}
        />
        <Select
          allowClear
          placeholder="场景筛选"
          style={{ width: 200 }}
          value={scene}
          onChange={(v) => {
            const next = v || undefined; // 选「全部场景」(空串) 与 allowClear 清除同义
            setScene(next);
            setPage(1);
            mirrorUrl({ box, tab: parentTab, page: 1, keyword, scene: next });
          }}
          options={SCENE_OPTIONS}
        />
        <Input.Search
          allowClear
          placeholder="按家长姓名搜索"
          style={{ width: 220 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={(v) => {
            setPage(1);
            mirrorUrl({ box, tab: parentTab, page: 1, keyword: v, scene });
          }}
        />
      </div>
      <Table<AdminNotification>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        locale={{ emptyText: <PaintEmpty message="暂无通知记录" /> }}
        pagination={false}
        expandable={{
          expandedRowRender: expandContent,
        }}
        columns={[
          { title: "时间", dataIndex: "created_at", width: 140 },
          { title: "家长", dataIndex: "parent_name", width: 110 },
          { title: "分类", dataIndex: "category", width: 80 },
          { title: "标题", dataIndex: "title", width: 150 },
          {
            title: "微信通道",
            dataIndex: "wechat_status",
            width: 110,
            render: (v: string, r) => (
              <span title={r.wechat_error || undefined}>{wechatTag(v)}</span>
            ),
          },
          {
            title: "已读",
            dataIndex: "read",
            width: 120,
            render: (v: boolean, r) => (
              <Button
                size="small"
                type={r.read ? "default" : "primary"}
                onClick={() => void toggleRead(r)}
              >
                {v ? "标记未读" : "标记已读"}
              </Button>
            ),
          },
        ]}
       scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
    </>
  );

  const adminPanel = (
    <>
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <Select
          value={adminTab === "done" ? "done" : "todo"}
          style={{ width: 170 }}
          options={[
            { value: "todo", label: todoTabCount === null ? "待处理" : `待处理（${todoTabCount}）` },
            { value: "done", label: "已处理" },
          ]}
          onChange={changeAdminTab}
        />
        <Select
          allowClear
          placeholder="事项类型"
          style={{ width: 180 }}
          value={scene}
          onChange={(v) => {
            const next = v || undefined;
            setScene(next);
            setPage(1);
            mirrorUrl({ box, tab: adminTab, page: 1, keyword, scene: next });
          }}
          options={ADMIN_SCENE_OPTIONS}
        />
        <Input.Search
          allowClear
          placeholder="按申请人搜索"
          style={{ width: 220 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={(v) => {
            setPage(1);
            mirrorUrl({ box, tab: adminTab, page: 1, keyword: v, scene });
          }}
        />
      </div>
      <Table<AdminInboxItem>
        rowKey="id"
        size="small"
        loading={inboxLoading}
        dataSource={inbox}
        locale={{
          emptyText: (
            <PaintEmpty
              message={adminTab === "todo" ? "没有待处理的申请，一切安好" : "暂无已处理记录"}
            />
          ),
        }}
        pagination={false}
        onRow={(r) => {
          if (r.effective_status === "pending") return { style: { background: "#fff7e6" } };
          if (r.effective_status === "invalid") return { style: { background: "#fafafa" } };
          return {};
        }}
        columns={inboxColumns}
       scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
    </>
  );

  return (
    <Card
      title="通知中心"
      extra={
        // 导出按钮：仅家长通知 tab（WM13 裁定：管理待办量小，隐藏导出不做假功能）
        box === "parent" ? (
          <Button
            icon={<DownloadOutlined />}
            onClick={() => apiExportNotifications().catch((e) => message.error((e as Error).message))}
          >
            导出 Excel
          </Button>
        ) : undefined
      }
    >
      <Tabs
        activeKey={box}
        onChange={changeBox}
        items={[
          { key: "parent", label: `家长通知（未读 ${unreadCount}）`, children: parentPanel },
          { key: "admin", label: adminTabLabel, children: adminPanel },
        ]}
      />
      <div style={{ marginTop: 16, textAlign: "right" }}>
        <PaintPagination
          current={page}
          total={box === "admin" ? inboxTotal : total}
          pageSize={pageSize}
          onChange={(p, ps) => {
            setPage(p);
            if (ps && ps !== pageSize) setPageSize(ps);
            mirrorUrl({
              box,
              tab: box === "admin" ? adminTab : parentTab,
              page: p,
              keyword,
              scene,
            });
          }}
        />
      </div>
      <Modal
        title="标记已处理"
        open={handleTarget !== null}
        onOk={() => void submitHandle()}
        onCancel={() => setHandleTarget(null)}
        confirmLoading={handleSaving}
        okText="确认处理"
        cancelText="取消"
      >
        <div style={{ marginBottom: 8, color: "rgba(0,0,0,0.65)" }}>
          {handleTarget?.title} · {handleTarget?.applicant_name}
          <br />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {handleTarget?.content}
          </Typography.Text>
        </div>
        <Input.TextArea
          rows={3}
          maxLength={200}
          showCount
          placeholder="必须填写处理原因（审计留痕，如：家长线下协商解决）"
          value={handleReason}
          onChange={(e) => setHandleReason(e.target.value)}
        />
      </Modal>
    </Card>
  );
}
