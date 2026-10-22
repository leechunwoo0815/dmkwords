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
  { value: "money.deposit_paid", label: "押金缴纳/补缴" },
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

// G9（2026-10-13）：里程碑达成是**知会**（馆员线下表扬/朋友圈/颁奖候选），不是待审事项——
// 后端 `NOTICE_SCENES` 已把它从待办墙默认名单里摘出去，这里给它一个**独立视图**（第三个下拉项），
// 免得混在"待处理/已处理"里看着乱（用户 2026-10-13 明确要求分开）。
const MILESTONE_SCENE = "admin.milestone_achieved";

// WM13 管理待办事项类型。
//
// P2-59（2026-10-20 第八轮）：原先只列 7 个（6 场景 + 里程碑），而
// `backend/common/admin_notification_models.AdminNotification` 里有 **18 个**场景 →
// 另外 10 个场景的通知在待办墙里**筛不出来**、且场景列显示**裸英文**（如
// `admin.pending_evaluation`），运营看不懂。现在与后端常量**逐一对齐**（含 2 个超时类）。
// 改后端场景常量时**必须同步这里**（`tests/unit/test_audit8_e4_admin_scenes_20261020.py` 钉住）。
const ADMIN_SCENE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部事项" },
  { value: "admin.refund_apply", label: "退款申请" },
  { value: "admin.withdrawal_apply", label: "退会申请" },
  { value: "admin.transfer_apply", label: "权益转让" },
  { value: "admin.activity_batch_refund", label: "活动批量退款" },
  { value: "admin.transfer_expiring", label: "转让临近超时" },
  { value: "admin.refund_execute_failed", label: "退款执行失败" },
  // ↓ P2-59 补齐的 10 个（后端有、前端原先没有）
  { value: "admin.activity_enroll_manual", label: "活动报名待确认" },
  { value: "admin.member_follow_up", label: "会员跟进" },
  { value: "admin.activity_order_voided", label: "活动订单作废" },
  { value: "admin.copy_damage_compensation", label: "副本损坏赔偿" },
  { value: "admin.payment_anomaly", label: "支付异常留痕" },
  { value: "admin.pending_evaluation", label: "待评估跟进" },
  { value: "admin.deposit_settle", label: "押金结清" },
  { value: "admin.reservation_exception", label: "预约转异常" },
  { value: "admin.refund_review_timeout", label: "退款审核超时" },
  { value: "admin.withdrawal_review_timeout", label: "退会审核超时" },
  { value: MILESTONE_SCENE, label: "里程碑达成" },
];

// 关联对象中文标签 + 管理端跳转路由（N1：order/activity/report 等可跳；
// borrow_record 无明细列表，归入详情展开，不跳转——审查 Q1/Q3 裁决）
const REF_META: Record<string, { label: string; route?: (r: AdminNotification) => string }> = {
  order: { label: "订单", route: () => "/members?tab=orders" },
  activity: { label: "活动", route: () => "/activities" },
  report: { label: "报告", route: (r) => (r.child_id ? `/growth?child_id=${r.child_id}` : "/growth") },
  reservation: { label: "预约", route: () => "/reservations" },
  // T20f：与管理待办操作列同款 URL（tab+highlight），两入口最终一致
  transfer: { label: "权益转让", route: (r) => `/refund-center?tab=transfers&highlight=${r.ref_id}` },
  withdrawal_request: { label: "退会申请", route: (r) => `/refund-center?tab=withdrawals&highlight=${r.ref_id}` },
  refund_request: { label: "退款申请", route: (r) => `/refund-center?tab=refunds&highlight=${r.ref_id}` },
  deposit: { label: "押金", route: () => "/deposits" },
  activity_enrollment: { label: "活动报名", route: () => "/members?tab=orders" },
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
  // R10：活动报名待确认——"处理"=确认收款（/members 订单 tab 为 confirm-payment
  // 唯一入口；活动中心报名名单区无收款动作，故跳订单 tab。简报声明选择）
  activity_enrollment: { path: "/members", tab: "orders" },
};

function wechatTag(status: string): React.ReactNode {
  switch (status) {
    case "sent":
      return <Tag color="green">微信已送达</Tag>;
    case "queued":
      // P2-24（2026-10-17 第六十九批）：异步通道的「已提交、结果未知」中间态——
      // 拿到微信回执后由后台线程回写 sent/failed，这里不许显示成「已送达」
      return <Tag color="blue">微信已提交·待回执</Tag>;
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
  // T20e #1：默认落管理待办（运营主工作面）；URL 显式 box=parent 时尊重（分享链接不破坏）
  const box = searchParams.get("box") === "parent" ? "parent" : "admin";
  // P2-56：本页两个列表接口后端都是 `page_size ≤ 200`（通知中心独有），显式给上限 200；
  // 其余管理端接口多为 ≤100——别把 200 带过去（带过去首屏就是 422）。
  const { page, setPage, pageSize, setPageSize } = usePaintPagination(
    20,
    Number(searchParams.get("page")) || 1,
    200,
  );
  // URL 五态镜像（WM13 批次二：box/tab/page/keyword/scene，replace 不堆栈）
  // tab 双语义：box=parent 时 all/unread/read；box=admin 时 todo/done
  const initialTab = searchParams.get("tab");
  const [parentTab, setParentTab] = useState<string>(
    initialTab === "unread" || initialTab === "read" ? initialTab : "all"
  );
  // 管理端三视图：todo（待处理）/ done（已处理）/ milestone（里程碑知会，G9 独立视图）
  const [adminTab, setAdminTab] = useState<string>(
    initialTab === "done" ? "done" : initialTab === "milestone" ? "milestone" : "todo"
  );
  const [keyword, setKeyword] = useState<string>(searchParams.get("keyword") ?? "");
  const [scene, setScene] = useState<string | undefined>(searchParams.get("scene") ?? undefined);
  // 家长通知（现状平移）
  const [items, setItems] = useState<AdminNotification[]>([]);
  const [total, setTotal] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  // T1（计数同源第 6 案）：loadParent 仅 box=parent 时跑——默认 admin 视角下
  // unreadCount 恒 0（假 0 违 U10）。胶囊兜底用 todo-counts 的 parent_unread
  // 全局数（与视角无关）；loadParent 落地后本地覆盖（筛选口径优先，双源同值）
  const [parentLoaded, setParentLoaded] = useState(false);
  const [allCount, setAllCount] = useState(0);
  const [loading, setLoading] = useState(false);
  // 管理待办（WM13）
  const [inbox, setInbox] = useState<AdminInboxItem[]>([]);
  const [inboxTotal, setInboxTotal] = useState(0);
  // P2-2（第七轮）：候选集被窗口截断时如实告知（total_all = 该筛选下历史精确总数）
  const [inboxTruncated, setInboxTruncated] = useState(false);
  const [inboxTotalAll, setInboxTotalAll] = useState(0);
  const [inboxReadOnly, setInboxReadOnly] = useState(false); // T20g：staff 可见只读
  // P3 管理端族（2026-10-22）：窗口上限由后端下发（原先写死「2000 条」）
  const [inboxScanLimit, setInboxScanLimit] = useState<number | null>(null);
  // R3（#5）：staff 待办"查看"=行展开详情（不跨页）——expandedRowKeys 受控
  const [inboxExpanded, setInboxExpanded] = useState<React.Key[]>([]);
  const { counts: todoCounts, failed: todoFailed } = useTodoCounts();  // WM13-F4：与徽标同源
  const [inboxLoading, setInboxLoading] = useState(false);
  const [handleTarget, setHandleTarget] = useState<AdminInboxItem | null>(null);
  const [handleReason, setHandleReason] = useState("");
  const [handleSaving, setHandleSaving] = useState(false);

  const mirrorUrl = useCallback(
    (next: { box: string; tab: string; page: number; keyword: string; scene?: string }) => {
      const params: Record<string, string> = {};
      // R1（#6）：box 显式写不再省略——T20e 默认值反转（parent→admin）后，
      // "省略=parent"的旧假设失效：点家长 tab 省略 box → URL 读回 admin → 弹回。
      // URL 已存在的旧链接（无 box）落 admin 属新默认，不迁移。
      if (next.box) params.box = next.box;
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
      setParentLoaded(true);
      setAllCount(data.all_count);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, scene, keyword, parentTab]);

  const loadInbox = useCallback(async () => {
    setInboxLoading(true);
    // G9：里程碑知会视图 = 固定按该场景取数（知会类已在后端默认名单外）；其余视图走待办口径。
    const milestoneView = adminTab === "milestone";
    try {
      const data = await apiListAdminInbox({
        page,
        page_size: pageSize,
        status_filter: milestoneView ? undefined : adminTab === "todo" ? "pending" : "finished",
        scene: milestoneView ? MILESTONE_SCENE : scene,
        keyword: keyword || undefined,
      });
      setInbox(data.items);
      setInboxTotal(data.total);
      setInboxTruncated(Boolean(data.truncated));
      setInboxTotalAll(data.total_all ?? data.total);
      setInboxReadOnly(Boolean(data.read_only)); // T20g
      setInboxScanLimit(data.scan_limit ?? null);
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
    // 切到"里程碑知会"视图时清掉事项类型筛选（该视图固定按里程碑场景取数）
    const nextScene = next === "milestone" ? undefined : scene;
    if (next === "milestone") setScene(undefined);
    mirrorUrl({ box, tab: next, page: 1, keyword, scene: nextScene });
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

  // R3：管理待办行展开——完整内容+关联对象+超管处理结果（staff 只读视角的"查看"目标）
  const adminExpandContent = (r: AdminInboxItem) => {
    const meta = ADMIN_REF_ROUTE[r.ref_type];
    // G9（2026-10-13）：里程碑达成是**知会**（PRD §7.4 线下表扬/朋友圈/颁奖候选），
    // 不是待审事项 → 显示态恒为"已达成·可线下表扬"，并给一条直达孩子成长档案的链。
    const milestone = r.scene === "admin.milestone_achieved";
    const label = milestone
      ? "里程碑"
      : meta
        ? { refund_request: "退款申请", withdrawal_request: "退会申请", transfer: "权益转让", activity: "活动", activity_enrollment: "活动报名" }[r.ref_type] ?? r.ref_type
        : r.ref_type;
    return (
      <div style={{ padding: "4px 8px 8px" }}>
        <div style={{ color: "rgba(0,0,0,0.75)" }}>
          <Typography.Text>完整内容：{r.content}</Typography.Text>
        </div>
        <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 8 }}>
          <Tag color="blue">{label}#{r.ref_id}</Tag>
          {milestone && (
            <Button size="small" icon={<LinkOutlined />} onClick={() => navigate(`/growth?child_id=${r.ref_id}`)}>
              查看成长档案
            </Button>
          )}
          <span style={{ color: "rgba(0,0,0,0.45)" }}>
            {r.effective_status === "done" && r.handled_by_name
              ? `超管 ${r.handled_by_name} 已处理`
              : r.effective_status === "done"
                // 机器推导的终态（审核推进自动审结 / 里程碑知会）：如实显示状态文案；
                // 原实现落在"待超管处理"——无人可处理却挂着待办提示，是误导。
                ? r.status_text
                : r.effective_status === "invalid"
                  ? r.status_text
                  : "待超管处理"}
          </span>
        </div>
      </div>
    );
  };

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
        ) : r.linkage ? (
          <Tag color="default" bordered={false}>{v}</Tag> // T20d：联动单灰态
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
        // T20g：staff 只读视角——去处理改"查看"、隐藏"标记已处理"（权限矩阵后端不动）
        // R3（#5）：staff"查看"=行展开详情（不跳退款中心——staff 无 audit.view，
        // 跨页即 403 风暴）；超管"查看"保持跳退款中心
        const readOnly = inboxReadOnly;
        return (
          <span style={{ display: "inline-flex", gap: 8 }}>
            {route && !readOnly && (
              <Button size="small" type="link" icon={<LinkOutlined />} onClick={() => navigate(route)}>
                {r.effective_status === "pending" ? "去处理" : "查看"}
              </Button>
            )}
            {readOnly && (
              <Button
                size="small"
                type="link"
                icon={<LinkOutlined />}
                onClick={() =>
                  setInboxExpanded((prev) =>
                    prev.includes(r.id) ? prev.filter((k) => k !== r.id) : [...prev, r.id]
                  )
                }
              >
                {inboxExpanded.includes(r.id) ? "收起" : "查看"}
              </Button>
            )}
            {r.effective_status === "pending" && !readOnly && (
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
  // T20e #2：红底白字胶囊（MemberManage 订单 tab D1 同款：M>0 胶囊，M=0/未知原样）
  const adminTabLabel = (
    <>
      管理待办
      {todoTabCount !== null && todoTabCount > 0 && (
        <span
          style={{
            background: "#ff4d4f",
            color: "#fff",
            borderRadius: 10,
            padding: "0 6px",
            marginLeft: 6,
            fontSize: 12,
          }}
        >
          {todoTabCount}
        </span>
      )}
    </>
  );

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
          value={adminTab === "done" ? "done" : adminTab === "milestone" ? "milestone" : "todo"}
          style={{ width: 190 }}
          options={[
            { value: "todo", label: todoTabCount === null ? "待处理" : `待处理（${todoTabCount}）` },
            { value: "done", label: "已处理" },
            // G9：知会类单独一档（不进待办数、不进待处理/已处理名单）
            { value: "milestone", label: "里程碑知会" },
          ]}
          onChange={changeAdminTab}
        />
        <Select
          allowClear
          placeholder="事项类型"
          style={{ width: 180 }}
          value={adminTab === "milestone" ? MILESTONE_SCENE : scene}
          disabled={adminTab === "milestone"}  // 该视图固定按里程碑场景取数
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
      {inboxReadOnly && (
        <div
          style={{
            background: "#fffbe6",
            border: "1px solid #ffe58f",
            borderRadius: 6,
            padding: "6px 12px",
            marginBottom: 12,
          }}
        >
          <Typography.Text type="warning">
            仅超级管理员可操作，您可查看处理进展
          </Typography.Text>
        </div>
      )}
      {inboxTruncated && (
        <div
          style={{
            background: "#f5f5f5",
            border: "1px solid #e0e0e0",
            borderRadius: 6,
            padding: "6px 12px",
            marginBottom: 12,
          }}
        >
          <Typography.Text type="secondary">
            该筛选下历史共 {inboxTotalAll} 条；待办墙按显示态实时排序，只加载最近
            {inboxScanLimit ?? "若干"} 条已审结记录（未销项一条不落）。更早的历史可用下方「场景 /
            关键词」缩小范围查询。
          </Typography.Text>
        </div>
      )}
      <Table<AdminInboxItem>
        rowKey="id"
        size="small"
        loading={inboxLoading}
        dataSource={inbox}
        locale={{
          emptyText: (
            <PaintEmpty
              message={
                adminTab === "todo"
                  ? "没有待处理的申请，一切安好"
                  : adminTab === "milestone"
                    ? "暂无里程碑达成记录"
                    : "暂无已处理记录"
              }
            />
          ),
        }}
        pagination={false}
        expandable={{
          expandedRowRender: adminExpandContent,
          expandedRowKeys: inboxExpanded,
          onExpandedRowsChange: (keys) => setInboxExpanded([...keys]),
        }}
        onRow={(r) => {
          if (r.effective_status === "pending") return { style: { background: "#fff7e6" } };
          if (r.linkage) return { style: { background: "#fafafa" } }; // T20d：联动单灰行
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
          {
            key: "parent",
            // S1（计数同源第 5 案·分工表）：侧边栏徽标=管理待办（干活优先级）｜
            // tab 胶囊=各自域内计数（本 tab=家长未读，管理待办 tab=待处理数）——
            // 两个真实数各归各，禁止互相冒充（教训 E-20260901-03 第 5 案）
            label: (
              <>
                家长通知（未读{" "}
                {(parentLoaded ? unreadCount : (todoCounts?.parent_unread ?? 0)) > 0 ? (
                  <span
                    style={{
                      background: "#ff4d4f",
                      color: "#fff",
                      borderRadius: 10,
                      padding: "0 6px",
                    }}
                  >
                    {parentLoaded ? unreadCount : (todoCounts?.parent_unread ?? 0)}
                  </span>
                ) : (
                  parentLoaded ? unreadCount : (todoCounts?.parent_unread ?? 0)
                )}
                ）
              </>
            ),
            children: parentPanel,
          },
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
                maxPageSize={200}
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
