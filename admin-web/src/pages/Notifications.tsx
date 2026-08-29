import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Card, Input, message, Select, Table, Tag, Typography } from "antd";
import { DownloadOutlined, LinkOutlined } from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";

import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
import { usePaintPagination } from "../hooks/usePaintPagination";
import {
  AdminNotification,
  apiExportNotifications,
  apiListNotifications,
  apiToggleNotificationRead,
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

export default function Notifications() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { page, setPage, pageSize, setPageSize } = usePaintPagination(
    20,
    Number(searchParams.get("page")) || 1
  );
  // URL 四态镜像（Q4 裁决：tab/page/keyword/scene，replace 不堆栈）；tab 取 all/unread/read（C42）
  const initialTab = searchParams.get("tab");
  const [tab, setTab] = useState<string>(
    initialTab === "unread" || initialTab === "read" ? initialTab : "all"
  );
  const [keyword, setKeyword] = useState<string>(searchParams.get("keyword") ?? "");
  const [scene, setScene] = useState<string | undefined>(searchParams.get("scene") ?? undefined);
  const [items, setItems] = useState<AdminNotification[]>([]);
  const [total, setTotal] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [allCount, setAllCount] = useState(0);
  const [loading, setLoading] = useState(false);

  const mirrorUrl = useCallback(
    (next: { tab: string; page: number; keyword: string; scene?: string }) => {
      const params: Record<string, string> = {};
      if (next.tab !== "all") params.tab = next.tab;
      if (next.page > 1) params.page = String(next.page);
      if (next.keyword) params.keyword = next.keyword;
      if (next.scene) params.scene = next.scene;
      setSearchParams(params, { replace: true });
    },
    [setSearchParams]
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiListNotifications({
        page,
        page_size: pageSize,
        category: undefined,
        scene,
        parent_name: keyword || undefined,
        unread: tab === "unread",
        read: tab === "read",
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
  }, [page, pageSize, scene, keyword, tab]);

  useEffect(() => {
    void load();
  }, [load]);

  const changeTab = (next: string) => {
    setTab(next);
    setPage(1);
    mirrorUrl({ tab: next, page: 1, keyword, scene });
  };

  const toggleRead = useCallback(
    async (r: AdminNotification) => {
      try {
        const resp = await apiToggleNotificationRead(r.id, !r.read, "通知中心协助标记");
        // 未读 Tab 标已读 / 已读 Tab 标未读（C42）→ 该行从列表消失；全部 Tab 行内翻转
        if ((tab === "unread" && !r.read) || (tab === "read" && r.read)) {
          setItems((prev) => prev.filter((i) => i.id !== r.id));
          setTotal((t) => Math.max(0, t - 1));
        } else {
          setItems((prev) => prev.map((i) => (i.id === r.id ? { ...i, read: !r.read } : i)));
        }
        // Tab 计数以服务端口径为准（F1b/C37）；有筛选时响应为全局口径不适用，本地推算
        if (!scene && !keyword) {
          setUnreadCount(resp.unread_count);
          if (tab === "unread") setTotal(resp.unread_count);
          else if (tab === "read") setTotal(Math.max(0, allCount - resp.unread_count));
        } else {
          setUnreadCount((c) => (r.read ? c + 1 : Math.max(0, c - 1)));
        }
      } catch (e) {
        message.error((e as Error).message);
      }
    },
    [tab, scene, keyword, allCount]
  );

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

  return (
    <Card
      title="通知记录中心"
      extra={
        <Button
          icon={<DownloadOutlined />}
          onClick={() => apiExportNotifications().catch((e) => message.error((e as Error).message))}
        >
          导出 Excel
        </Button>
      }
    >
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <Select
          value={tab === "unread" || tab === "read" ? tab : "all"}
          style={{ width: 150 }}
          options={[
            { value: "all", label: `全部（${allCount}）` },
            { value: "unread", label: `未读（${unreadCount}）` },
            { value: "read", label: `已读（${Math.max(0, allCount - unreadCount)}）` },
          ]}
          onChange={changeTab}
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
            mirrorUrl({ tab, page: 1, keyword, scene: next });
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
            mirrorUrl({ tab, page: 1, keyword: v, scene });
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
      />
      <div style={{ marginTop: 16, textAlign: "right" }}>
        <PaintPagination
          current={page}
          total={total}
          pageSize={pageSize}
          onChange={(p, ps) => {
            setPage(p);
            if (ps && ps !== pageSize) setPageSize(ps);
            mirrorUrl({ tab, page: p, keyword, scene });
          }}
        />
      </div>
    </Card>
  );
}