import { useCallback, useEffect, useRef, useState } from "react";
import { cfgNum, money, useConfigs } from "../hooks/useConfigs";
import { TODO_REFRESH_EVENT } from "../hooks/useTodoCounts";
import {
  App as AntdApp,
  Button,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { Tooltip, Upload } from "antd";
import type { UploadFile } from "antd";

import { useAuth } from "../auth";
import AvatarPicker from "../components/AvatarPicker";
import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
import { useImageViewer } from "../components/PreviewImage";

import {
  apiGetChildReading,
  type ChildReadingProfile,
} from "../api/reservations";
import { apiUploadObservation } from "../api/observation";
import { apiListActivities } from "../api/activities";
import {
  apiCancelOrder,
  apiRefundOrder,
  apiConfirmPayment,
  apiCreateChild,
  apiCreateOrder,
  apiCreateParent,
  apiEvaluateApprove,
  apiListChildren,
  apiListOrders,
  apiMarkPendingEvaluation,
  apiOrderCounts,
  apiSearchParents,
  apiUpdateChild,
  apiListParentsPage,
  apiUpdateParent,
  apiDeleteParent,
  apiDeleteChild,
  apiUploadVoucher,
  apiVoucherUrl,
  type Child,
  type Order,
  type Parent,
  type ParentRow,
} from "../api/members";
import { PAY_METHOD_OPTIONS, payMethodLabel } from "../constants/pay";
import { usePaintPagination } from "../hooks/usePaintPagination";
import { PaintHScrollbar } from "../components/PaintHScrollbar";
import PageTitle from "../components/PageTitle";

const MEMBER_LABEL: Record<string, string> = {
  none: "未入会", observation: "观察期", pending_evaluation: "待评估",
  formal: "正式会员", expired: "已过期", withdrawn: "已退会",
};
const MEMBER_COLOR: Record<string, string> = {
  none: "default", observation: "blue", pending_evaluation: "orange",
  formal: "green", expired: "red", withdrawn: "default",
};
const ORDER_TYPE_LABEL: Record<string, string> = {
  first_activity_fee: "首场亲子活动", observation_fee: "观察期会员费",
  formal_fee: "正式年费", activity_fee: "活动费",
  deposit: "押金", deposit_supplement: "押金补缴", custom: "自定义",
};
const ORDER_STATUS_LABEL: Record<string, string> = {
  pending_payment: "待支付", pending_manual_confirm: "待人工确认",
  paid: "已支付", cancelled: "已取消", refunded: "已退款",
};

export default function MemberManage() {
  const { configs, failed: configsFailed } = useConfigs();
  const { message } = AntdApp.useApp();
  const { user } = useAuth();
  // 二孩年费（下单弹窗的订单类型文案用）：配置 `second_child_discount_percent` 的语义是
  // **按此比例收取**（90 = 收 90% = 九折），与后端定价同源——`order_service.price_member_order`
  // 就是 `base * percent / 100`。
  // 2026-10-15 修正（用户现场发现）：本页文案原先写成 `1 - percent/100`，把 90 当成了"折扣幅度
  // 90%"，于是 6000 元年费显示成「二孩自动 600」。后端实际收 5400 —— 属**纯标签算错**（不影响下单金额）。
  const formalFee = cfgNum(configs, "formal_fee", 6000);
  const siblingFee = Math.round(
    (formalFee * cfgNum(configs, "second_child_discount_percent", 90)) / 100,
  );
  // W5 URL 状态镜像：tab/keyword/page 进 URL（replaceState 不堆栈），刷新还原
  const readUrl = () => new URLSearchParams(window.location.search);
  const updateUrl = useCallback((params: Record<string, string | undefined>) => {
    const url = new URL(window.location.href);
    Object.entries(params).forEach(([k, v]) => {
      if (v === undefined || v === "") url.searchParams.delete(k);
      else url.searchParams.set(k, v);
    });
    window.history.replaceState(null, "", url.toString());
  }, []);
  const urlInitialPage = Number(readUrl().get("page")) || 1;
  // W6-D10（2026-10-28）：三个 tab 各自分页键——原先共用一个 `page`，刷新后串页
  const urlOrderPage = Number(readUrl().get("opage")) || urlInitialPage;
  const urlParentPage = Number(readUrl().get("ppage")) || 1;
  const [tab, setTab] = useState<string>(() => {
    const t0 = readUrl().get("tab");
    return t0 === "orders" || t0 === "parents" ? t0 : "children";
  });
  const changeTab = useCallback((t: string) => {
    setTab(t);
    updateUrl({ tab: t, page: undefined, opage: undefined, ppage: undefined });
  }, [updateUrl]);
  // 孩子列表
  const [children, setChildren] = useState<Child[]>([]);
  const [childTotal, setChildTotal] = useState(0);
  const [keyword, setKeyword] = useState<string>(() => readUrl().get("keyword") ?? "");
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  // 订单列表
  const [orders, setOrders] = useState<Order[]>([]);
  // R10b（插修 14 目视补刀）：通知"去处理"跳订单 tab 行高亮 3 秒——
  // 锚点=订单行响应的 enrollment_id（通知 ref_id）；时序从数据到达起算
  // （R5 教训：挂载即起算接口返回前窗口已烧掉）
  const [orderHighlightId, setOrderHighlightId] = useState<number | null>(
    () => (readUrl().get("tab") === "orders" && readUrl().get("highlight")
      ? Number(readUrl().get("highlight"))
      : null)
  );
  const [orderHlArmed, setOrderHlArmed] = useState(false);
  useEffect(() => {
    if (!orderHlArmed && tab === "orders" && orders.length) setOrderHlArmed(true);
  }, [tab, orders, orderHlArmed]);
  useEffect(() => {
    if (orderHighlightId === null || !orderHlArmed) return;
    const t = setTimeout(() => setOrderHighlightId(null), 3000);
    return () => clearTimeout(t);
  }, [orderHighlightId, orderHlArmed]);
  const [orderTotal, setOrderTotal] = useState(0);
  const [orderStatus, setOrderStatus] = useState<string | undefined>();
  const [orderKeyword, setOrderKeyword] = useState<string>(() => readUrl().get("okeyword") ?? ""); // WM3-A5
  const [orderLoading, setOrderLoading] = useState(false); // W6
  const [orderBy, setOrderBy] = useState<string | undefined>(); // W7
  // W6-D4（第十轮 P2，2026-10-24）：初值 0 让"还没取到/取失败"与"真值 0"不可区分——
  // 人工收款确认队列（资金域）会显示「待确认 0」→ 漏确认/漏收款。改成 `null` = **未知**，
  // label 渲染「—」而不是 0（同仓 Layout/Notifications/CircleManage 的"失败不假 0"范式）。
  const [orderCounts, setOrderCounts] = useState<{
    pending_manual_confirm: number | null;
    total: number | null;
  }>({ pending_manual_confirm: null, total: null }); // W3
  // WM3-B1 家长管理 tab
  const [parents, setParents] = useState<ParentRow[]>([]);
  const [parentTotal, setParentTotal] = useState(0);
  const [parentLoading, setParentLoading] = useState(false);
  // D6 挂账收口（2026-10-24）：本页是**一页三列表**（孩子/订单/家长）。此前三处 catch 只弹
  // toast（`message.error`），表格会回落成空态——后端故障时看起来像"库里没有这些数据"，
  // 与 U10「禁假 0/假空」相反。检查器 A2 是**文件级**判据（页面里有一个失败态就算过），
  // 所以本页长期假绿；这里**逐列表**留痕（各自 banner + 重试 + 失败时抑制空态）。
  const [childListError, setChildListError] = useState("");
  const [orderListError, setOrderListError] = useState("");
  const [parentListError, setParentListError] = useState("");
  const [editParent, setEditParent] = useState<ParentRow | null>(null);
  const [parentEditForm] = Form.useForm();
  // WM3-B2 凭证：确认收款弹窗单图；查看凭证 / 选图前本地预览统一走全屏干净预览
  const [voucherFile, setVoucherFile] = useState<UploadFile[]>([]);
  const viewer = useImageViewer();
  const childPg = usePaintPagination(undefined, urlInitialPage);
  const orderPg = usePaintPagination(undefined, urlOrderPage);
  const parentPg = usePaintPagination(undefined, urlParentPage);
  // 弹窗
  const [parentOpen, setParentOpen] = useState(false);
  const [childOpen, setChildOpen] = useState(false);
  const [orderOpen, setOrderOpen] = useState(false);
  const [confirmOrder, setConfirmOrder] = useState<Order | null>(null);
  const [selectedOrderChild, setSelectedOrderChild] = useState<Child | null>(null); // W12
  const [parentForm] = Form.useForm();
  const [childForm] = Form.useForm();
  // W1 建档家长远程搜索
  const [parentOptions, setParentOptions] = useState<Parent[]>([]);
  const [parentSearching, setParentSearching] = useState(false);
  const parentSearchTimer = useRef<number>();
  const searchParents = useCallback((kw: string) => {
    window.clearTimeout(parentSearchTimer.current);
    if (!kw.trim()) {
      // WM3-B4：空关键字=默认展示最新 5 位（后端 ParentService.search(None) id 倒序）
      // 建档选择器的远程搜索下拉：空列表 = 本次无候选（重输关键词即重试），不是"没有家长"的结论
    apiSearchParents("").then((list) => setParentOptions(list.slice(0, 5))).catch(() => setParentOptions([])); // A1-ok: 搜索下拉兜底，非列表结论
      return;
    }
    parentSearchTimer.current = window.setTimeout(() => {
      setParentSearching(true);
      apiSearchParents(kw.trim())
        .then(setParentOptions)
        // A1-ok: 建档搜索下拉兜底，非列表结论（重输关键词即重试；与空关键字分支同口径）
        .catch(() => { setParentOptions([]); })
        .finally(() => setParentSearching(false));
    }, 300);
  }, []);

  // W4 Modal 脏数据保护（参照 WM2 P2-11）：表单 touched / 受控快照非空 → 挽留
  const confirmDiscardIfDirty = useCallback((dirty: boolean, doDiscard: () => void) => {
    if (dirty) {
      Modal.confirm({
        title: "有未保存的修改",
        content: "已填写的内容将丢失，确定放弃？",
        okText: "放弃修改",
        okButtonProps: { danger: true },
        cancelText: "继续编辑",
        onOk: doDiscard,
      });
    } else {
      doDiscard();
    }
  }, []);
  const [obsChild, setObsChild] = useState<Child | null>(null);
  const [obsFileList, setObsFileList] = useState<UploadFile[]>([]);
  const [obsRemark, setObsRemark] = useState("");
  const [readingChild, setReadingChild] = useState<Child | null>(null);
  const [readingProfile, setReadingProfile] = useState<ChildReadingProfile | null>(null);
  const [readingLoading, setReadingLoading] = useState(false);
  // W6-D3（第十轮 P2，2026-10-24）：阅读档案抽屉取数失败原先只有 toast → 抽屉里**只剩标题**
  // （与 GrowthManage 的成长档案同族）。改为抽屉内失败态 + 重试。
  const [readingError, setReadingError] = useState("");
  const [orderForm] = Form.useForm();
  // R3/FEAT-080：活动单下拉数据源（创建订单弹窗打开时拉一次）
  const [activityOptions, setActivityOptions] = useState<{ value: number; label: string }[]>([]);
  const [confirmForm] = Form.useForm();
  const [editChild, setEditChild] = useState<Child | null>(null);
  const [editForm] = Form.useForm();

  const loadChildren = useCallback(
    (page: number) => {
      setLoading(true);
      apiListChildren({ page, page_size: childPg.pageSize, keyword: keyword || undefined, status: statusFilter })
        .then((r) => { setChildren(r.items ?? []); setChildTotal(r.total); setChildListError(""); })
        .catch((e: Error) => setChildListError(e.message || "列表加载失败"))
        .finally(() => setLoading(false));
    },
    [keyword, statusFilter, childPg.pageSize, message]
  );

  const loadOrders = useCallback(
    (page: number) => {
      setOrderLoading(true);
      apiListOrders({ page, page_size: orderPg.pageSize, status: orderStatus, order_by: orderBy, keyword: orderKeyword || undefined })
        .then((r) => { setOrders(r.items ?? []); setOrderTotal(r.total); setOrderListError(""); })
        .catch((e: Error) => setOrderListError(e.message || "列表加载失败"))
        .finally(() => setOrderLoading(false));
    },
    [orderStatus, orderBy, orderKeyword, orderPg.pageSize, message]
  );

  useEffect(() => { if (tab === "children") loadChildren(childPg.page); }, [loadChildren, childPg.page, tab]);

  // R10c（计数同源第 8 案·假 0，20260907 目视）：URL 直跳 orders tab 时
  // children 懒加载守卫（tab==="children"）不触发——label「孩子档案（0）」假 0
  // （违 U10），切进去才变真数（"里外不一致"）。orders tab 的 W3 counts 先例
  // 同款兜底：挂载无条件拉一次轻量 total（page_size=1）；parents 挂载即拉
  // （F1 已修）无此病，兜底拉取冗余无害（prev===0 守卫防覆盖）双保险。
  useEffect(() => {
    // 徽标 total 的冗余兜底（title 栏懒加载负责主取数；prev===0 守卫防覆盖）
    apiListChildren({ page: 1, page_size: 1 })
      .then((r) => setChildTotal((prev) => (prev === 0 ? r.total : prev)))
      .catch(() => undefined); // A1-ok: 徽标兜底，主取数另有失败态
    apiListParentsPage({ page: 1, page_size: 1 })
      .then((r) => setParentTotal((prev) => (prev === 0 ? r.total : prev)))
      .catch(() => undefined); // A1-ok: 同上（家长总数徽标）
    // 仅挂载一次（懒加载落地后 total 由其维护——不重复拉）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // P2-61（第八轮）：**文案必须如实**——本按钮调的是 `/orders/{id}/refund`，
  // 后端 B-15 改造后它**只建一笔"待审"退款申请**（走 R-308 审核链：review→execute），
  // 钱**没有**出去。原先标题写"订单退款执行"、成功提示写"订单已退款"，
  // 操作员会以为钱已经退了（资金口径误导，属"声称已执行"族）。
  const onRefundOrder = (o: Order) => {
    let remarkInput = "";
    Modal.confirm({
      title: "发起退款申请（待审核）",
      content: (
        <div>
          <div style={{ marginBottom: 8 }}>
            {o.order_no} · ￥{Number(o.amount).toLocaleString()}（仅超管；{cfgNum(configs, "first_activity_fee", 99)} 元资格随退款恢复）
          </div>
          <div style={{ marginBottom: 8, color: "#b26a00" }}>
            这里只**发起申请**，钱不会立即退出——需再到「退款中心」审核通过并执行。
          </div>
          <Input.TextArea rows={2} onChange={(e) => { remarkInput = e.target.value; }} placeholder="退款说明（留痕）" />
        </div>
      ),
      okText: "发起申请",
      onOk: async () => {
        try {
          const r = await apiRefundOrder(o.id, remarkInput || "退款执行");
          // 如实告知下一步（去哪审、当前状态）
          message.success(`已发起退款申请（${r?.status ?? "待审核"}）——请到「退款中心」审核执行`);
          window.dispatchEvent(new Event(TODO_REFRESH_EVENT));
          loadOrders(orderPg.page);
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  const onUploadObservation = async () => {
    if (!obsFileList.length) {
      message.warning("请先选择图片（≤9 张）");
      return;
    }
    const fd = new FormData();
    obsFileList.forEach((f) => {
      if (f.originFileObj) fd.append("files", f.originFileObj);
    });
    fd.append("remark", obsRemark);
    try {
      const r = await apiUploadObservation(obsChild!.id, fd);
      message.success(`已上传 ${r.images.length} 张，家长端可见`);
      setObsChild(null);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onMarkPendingEvaluation = (c: Child) => {
    let reason = "";
    Modal.confirm({
      title: `标记 ${c.name} 为待评估`,
      content: (
        <div>
          <div style={{ marginBottom: 8 }}>
            观察期到期后标记（权益保留、无时限）；到期自动转换任务将在通知模块上线。操作将写入审计日志。
          </div>
          <Input.TextArea rows={2} placeholder="操作原因（留痕，如：观察期一个月已到）" onChange={(e) => { reason = e.target.value; }} />
        </div>
      ),
      okText: "标记待评估",
      onOk: async () => {
        try {
          const r = await apiMarkPendingEvaluation(c.id, reason.trim() || "观察期到期，标记待评估");
          message.success(`${r.name} 已标记为待评估`);
          loadChildren(childPg.page);
        } catch (e) {
          message.error((e as Error).message);
          return Promise.reject(e);
        }
      },
    });
  };

  const onEvaluateApprove = (c: Child) => {
    let reason = "";
    Modal.confirm({
      title: `评估通过 — ${c.name} 转正`,
      content: (
        <div>
          <div style={{ marginBottom: 8 }}>
            转正需支付正式年费（二孩折扣自动判定）：确认后将创建年费订单（待人工确认），收款确认后自动转为正式会员。
          </div>
          <Input.TextArea rows={2} placeholder="评估结论（留痕，如：听力达标，同意转正）" onChange={(e) => { reason = e.target.value; }} />
        </div>
      ),
      okText: "通过并创建年费订单",
      onOk: async () => {
        try {
          const order = await apiEvaluateApprove(c.id, reason.trim() || "评估通过，转正式会员");
          message.success(`已创建年费订单 ￥${Number(order.amount).toLocaleString()}，请到「订单」页确认收款`);
          changeTab("orders");
          if (orderPg.page !== 1) orderPg.setPage(1);
          else loadOrders(1);
        } catch (e) {
          message.error((e as Error).message);
          return Promise.reject(e);
        }
      },
    });
  };

  const openReadingProfile = useCallback((child: Child) => {
    setReadingChild(child);
    setReadingProfile(null);
    setReadingError("");
    setReadingLoading(true);
    apiGetChildReading(child.id)
      .then((d) => {
        setReadingProfile(d);
        setReadingError("");
      })
      .catch((e: Error) => {
        message.error(e.message);
        setReadingError(e.message || "阅读档案加载失败");
      })
      .finally(() => setReadingLoading(false));
  }, [message]);
  useEffect(() => { if (tab === "orders") loadOrders(orderPg.page); }, [loadOrders, orderPg.page, tab]);

  // WM3-B1 家长管理列表
  const loadParents = useCallback(
    (page: number) => {
      setParentLoading(true);
      apiListParentsPage({ page, page_size: parentPg.pageSize })
        .then((r) => { setParents(r.items ?? []); setParentTotal(r.total); setParentListError(""); })
        .catch((e: Error) => setParentListError(e.message || "列表加载失败"))
        .finally(() => setParentLoading(false));
    },
    [parentPg.pageSize, message]
  );
  // F1（同族第三次，E-03 反模式 1）：家长 tab 标题计数唯一数据源=loadParents 的
  // total——守卫导致默认 tab 下「家长（0）」。挂载即拉（与 A1 对称，模式已验证无害）
  useEffect(() => { loadParents(parentPg.page); }, [loadParents, parentPg.page, tab]);

  // W3 订单 Tab 待确认计数（轻量单请求）；WM3-A1：页面挂载即拉，不设 tab 守卫
  // B4（插修5）计数不刷新同族第四案（A1→F4→W3→B4）：状态变更点显式刷计数，
  // 不靠派生依赖（orderTotal 不是计数的因——确认收款只翻状态不改总数）
  const refreshOrderCounts = useCallback(() => {
    apiOrderCounts()
      .then((c) => setOrderCounts({ pending_manual_confirm: c.pending_manual_confirm, total: c.total }))
      // A1-ok: 计数失败置 **null（未知）**——label 随之显示「—」而非 0，属可见未知态而非"假 0"；
      // 整页列表另有各自失败态，计数不阻塞列表（W6-D4：原实现是静默保留初值 0）
      .catch(() => setOrderCounts({ pending_manual_confirm: null, total: null }));
  }, []);
  useEffect(() => { refreshOrderCounts(); }, [refreshOrderCounts, tab]);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <PageTitle>会员管理</PageTitle>
      {/* P2-55：配置没取到时订单文案里的金额是**默认值**——如实提示（调价后不一致会误导收银） */}
      {configsFailed && (
        <Typography.Text type="warning" style={{ display: "block", marginBottom: 12 }}>
          价格配置未取到：订单金额文案按默认值展示，可能与实际收费不一致——请刷新或到「系统配置」确认。
        </Typography.Text>
      )}
        <Space>
          <Button onClick={() => {
            childForm.resetFields();
            setChildOpen(true);
            // WM3-B4：建档弹窗打开即拉最新家长前 5 位，预选第一位（仍可改选/搜索）
            apiSearchParents("")
              .then((list) => {
                setParentOptions(list.slice(0, 5));
                const latest = list[0];
                if (latest) childForm.setFieldsValue({ parent_id: latest.id });
              })
              .catch(() => { /* 静默：预选失败不阻塞建档 */ });
          }}>为孩子建档</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { parentForm.resetFields(); setParentOpen(true); }}>
            新建家长账号
          </Button>
        </Space>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 4 }}>
        家长建档 → 添加孩子 → 创建订单 → 确认收款（人工收款路径；微信线上支付在后期模块接入）。
      </Typography.Paragraph>

      <Tabs activeKey={tab} onChange={changeTab} items={[
        { key: "children", label: `孩子档案（${childTotal}）` },
        { key: "parents", label: `家长（${parentTotal}）` },
        {
          key: "orders",
          // WM3-D1：待确认数 M>0 时红底白字胶囊（M=0 保持原样避免满屏红）
          label: (
            <>
              {/* B8：筛选态显示筛选后的总数（并标注），未筛选才显示全局计数——两套口径不再混用 */}
              订单（{orderKeyword || orderStatus ? `${orderTotal}（筛选）` : (orderCounts.total ?? (orderTotal || "—"))} · 待确认{" "}
              {orderCounts.pending_manual_confirm === null ? (
                // W6-D4：未取到 = 未知，显式写「—」（不许 0 —— 那是"确认队列空"的错误结论）
                <Typography.Text type="warning">—（未取到）</Typography.Text>
              ) : orderCounts.pending_manual_confirm > 0 ? (
                <span style={{ background: "#ff4d4f", color: "#fff", borderRadius: 10, padding: "0 6px" }}>
                  {orderCounts.pending_manual_confirm}
                </span>
              ) : (
                orderCounts.pending_manual_confirm
              )}
              ）
            </>
          ),
        },
      ]} />

      {tab === "children" && (
        <>
          <Space style={{ marginBottom: 12 }}>
            <Input.Search
              placeholder="孩子姓名 / 英文名 / 家长手机号" allowClear style={{ width: 260 }} defaultValue={keyword}
              onSearch={(v) => { setKeyword(v); childPg.setPage(1); updateUrl({ keyword: v || undefined, page: undefined }); }}
            />
            <Select
              placeholder="会员状态" allowClear style={{ width: 130 }} value={statusFilter}
              onChange={(v) => { setStatusFilter(v); childPg.setPage(1); }}
              // WM3-A4：头部"全部"选项（空串后端 falsy 天然不过滤；allowClear 小 × 无感知）
              options={[{ value: "", label: "全部" }, ...Object.entries(MEMBER_LABEL).map(([value, label]) => ({ value, label }))]}
            />
          </Space>
          {childListError && (
            <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
              {childListError} · 本次刷新失败：列表可能不是最新的（不代表没有孩子档案）——请重试
              <Typography.Link onClick={() => loadChildren(childPg.page)} style={{ marginLeft: 8 }}>重试</Typography.Link>
            </Typography.Text>
          )}
          <Table<Child> locale={{ emptyText: childListError ? null : <PaintEmpty character="star" /> }}
            rowKey="id" loading={loading} dataSource={children} size="middle"
            pagination={false}
            columns={[
              // fix33 R4：头像列（与建档/编辑弹窗同源，走 public/avatars 本地资产零 token）
              { title: "头像", key: "avatar", width: 64, render: (_, r) => (
                r.avatar ? (
                  <img
                    src={`/avatars/${r.avatar}.png`}
                    alt={r.avatar}
                    width={32}
                    height={32}
                    style={{ borderRadius: "50%", display: "block" }}
                  />
                ) : <Typography.Text type="secondary">—</Typography.Text>
              ) },
              { title: "孩子", key: "child", width: 170, render: (_, r) => (
                <div>
                  <div>{r.name}{r.english_name ? `（${r.english_name}）` : ""}</div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.grade || "—"}{r.ar_level ? ` · AR ${r.ar_level}` : ""}</Typography.Text>
                </div>
              ) },
              { title: "家长", key: "parent", width: 160, render: (_, r) => (
                <div>
                  <div>{r.parent_name}</div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.parent_phone}</Typography.Text>
                </div>
              ) },
              { title: "性别", dataIndex: "gender", width: 70, render: (g) => (g === 1 ? "男" : g === 2 ? "女" : "—") },
              { title: "年级", dataIndex: "grade", width: 90, render: (v) => v || "—" },
              // F4：AR 值列（BookManage 风格；空显 —）
              { title: "AR", dataIndex: "ar_level", width: 70, render: (v) => (v ? <Typography.Text code>{v}</Typography.Text> : "—") },
              { title: "会员状态", dataIndex: "member_status", width: 110, render: (s: string) => (
                <Tag color={MEMBER_COLOR[s]}>{MEMBER_LABEL[s] ?? s}</Tag>
              ) },
              { title: "会员到期", dataIndex: "member_expire", width: 150, render: (v) => {
                if (!v) return "—";
                const expireMs = new Date(String(v).replace(/-/g, "/")).getTime();
                const days = Math.ceil((expireMs - Date.now()) / 86400000);
                if (days < 0) return (
                  <span style={{ color: "#cf1322", fontWeight: 600 }}>{v} · 已过期 {-days} 天</span>
                );
                const soon = days <= 7;
                return (
                  <span style={soon ? { color: "#d46b08", fontWeight: 600 } : undefined}>
                    {v} · 剩 {days} 天
                  </span>
                );
              } },
              { title: "会员开始", dataIndex: "member_start", width: 120, render: (v) => v ?? "—" },
              {
                title: "操作", key: "op", width: 280,
                render: (_, r) => (
                  <Space size={0} wrap>
                    <Button type="link" size="small" onClick={() => {
                      orderForm.setFieldsValue({ child_id: r.id, order_type: r.member_status === "none" ? "observation_fee" : "formal_fee" });
                      setSelectedOrderChild(r);
                      setOrderOpen(true);
                      // R3：活动单下拉数据源（打开弹窗时拉一次；失败静默降级）
                      apiListActivities()
                        .then((acts) => setActivityOptions(
                          acts.items.filter((a) => a.status === "published")
                            .map((a) => ({ value: a.id, label: `${a.title}（￥${a.fee}）` }))
                        ))
                        // A1-ok: 活动下拉候选兜底——空选项只是本次无可选活动，主数据（订单行）不受影响
                        .catch(() => setActivityOptions([]));
                    }}>创建订单</Button>
                    {r.member_status === "observation" && (
                      <Button type="link" size="small" onClick={() => onMarkPendingEvaluation(r)}>标记待评估</Button>
                    )}
                    {r.member_status === "pending_evaluation" && (
                      <Button type="link" size="small" onClick={() => onEvaluateApprove(r)}>评估通过</Button>
                    )}
                    <Button type="link" size="small" onClick={() => openReadingProfile(r)}>阅读档案</Button>
                    <Button type="link" size="small" onClick={() => { setObsChild(r); setObsFileList([]); setObsRemark(""); }}>评估报告</Button>
                    <Button type="link" size="small" onClick={() => {
                      setEditChild(r);
                      editForm.setFieldsValue({
                        name: r.name, english_name: r.english_name || "",
                        gender: r.gender ?? undefined, birthday: r.birthday || undefined,
                        grade: r.grade || "", ar_level: r.ar_level || "",
                        avatar: r.avatar || "",
                      });
                    }}>编辑</Button>
                    {/* WM3-B1 删除（订单守卫禁用+tooltip） */}
                    <Popconfirm
                      title={r.has_orders ? "该孩子已创建订单，禁止删除" : `确认删除孩子 ${r.name}？`}
                      okText="删除" okButtonProps={{ danger: true }}
                      disabled={r.has_orders}
                      onConfirm={async () => {
                        try {
                          await apiDeleteChild(r.id);
                          message.success("孩子已删除（软删，订单历史保留）");
                          loadChildren(childPg.page);
                        } catch (e) {
                          message.error((e as Error).message);
                        }
                      }}
                    >
                      <Tooltip title={r.has_orders ? "该孩子已创建订单，禁止修改删除" : undefined}>
                        <Button type="link" size="small" danger disabled={r.has_orders}>删除</Button>
                      </Tooltip>
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
           scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
          <PaintPagination current={childPg.page} pageSize={childPg.pageSize} total={childTotal}
            onChange={(p, s) => { childPg.onChange(p, s); updateUrl({ page: String(p) }); }} />
        </>
      )}

      {tab === "orders" && (
        <>
          <Space style={{ marginBottom: 12 }}>
            {/* WM3-A5：订单号 / 备注关键字搜索（后端 service.py list_orders keyword 已就绪） */}
            <Input.Search
              placeholder="订单号 / 备注" allowClear style={{ width: 240 }} defaultValue={orderKeyword}
              onSearch={(v) => { setOrderKeyword(v); orderPg.setPage(1); updateUrl({ okeyword: v || undefined, opage: undefined }); }}
            />
            <Select
              placeholder="订单状态" allowClear style={{ width: 140 }} value={orderStatus}
              onChange={(v) => { setOrderStatus(v); orderPg.setPage(1); }}
              // WM3-A4：头部"全部"选项
              options={[{ value: "", label: "全部" }, ...Object.entries(ORDER_STATUS_LABEL).map(([value, label]) => ({ value, label }))]}
            />
          </Space>
          {orderListError && (
            <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
              {orderListError} · 本次刷新失败：列表可能不是最新的（不代表没有订单）——请重试
              <Typography.Link onClick={() => loadOrders(orderPg.page)} style={{ marginLeft: 8 }}>重试</Typography.Link>
            </Typography.Text>
          )}
          <Table<Order> locale={{ emptyText: orderListError ? null : <PaintEmpty character="star" /> }}
            rowKey="id" dataSource={orders} size="middle" loading={orderLoading}
            rowClassName={(r) => (r.enrollment_id != null && r.enrollment_id === orderHighlightId ? "wm13-highlight-row" : "")}
            pagination={false}
            onChange={(_p, _f, sorter) => {
              const sort = Array.isArray(sorter) ? sorter[0] : sorter;
              if (sort?.order) {
                const next = `${String(sort.columnKey)}_${sort.order === "ascend" ? "asc" : "desc"}`;
                setOrderBy(next);
                orderPg.setPage(1);
              } else {
                setOrderBy(undefined);
              }
            }}
            columns={[
              { title: "订单号", dataIndex: "order_no", width: 200, render: (v) => <Typography.Text code style={{ fontSize: 12 }}>{v}</Typography.Text> },
              { title: "类型", dataIndex: "order_type", width: 200, render: (t: string, r: Order) => (
                <>
                  {ORDER_TYPE_LABEL[t] ?? t}
                  {t === "deposit" || t === "deposit_supplement" ? (
                    Number(r.deposit_refunded_amount || 0) > 0 ? (
                      <Tag color="volcano" style={{ marginLeft: 4 }}>
                        {r.deposit_status === "refunded" ? "押金已退" : "押金部分退"} ￥
                        {Number(r.deposit_refunded_amount).toFixed(2)}
                      </Tag>
                    ) : null
                  ) : null}
                </>
              ) },
              { title: "孩子", dataIndex: "child_name", width: 90, render: (v) => v ?? "—" },
              { title: "家长", dataIndex: "parent_name", width: 90 },
              { title: "金额", dataIndex: "amount", key: "amount", width: 100, sorter: true, render: (v: string) => <Typography.Text strong>￥{Number(v).toLocaleString()}</Typography.Text> },
              { title: "状态", dataIndex: "status", width: 110, render: (s: string) => (
                <Tag color={s === "paid" ? "green" : s === "cancelled" ? "default" : s === "refunded" ? "volcano" : "orange"}>
                  {ORDER_STATUS_LABEL[s] ?? s}
                </Tag>
              ) },
              { title: "收款方式", dataIndex: "pay_method", width: 100, render: (v) => payMethodLabel(v) },
              { title: "创建时间", dataIndex: "create_time", key: "created_at", width: 170, sorter: true, render: (v) => String(v ?? "").slice(0, 19).replace("T", " ") },
              {
                title: "操作", key: "op", width: 150,
                render: (_, r) => (
                  <Space>
                    {r.status === "pending_manual_confirm" && (
                      <>
                        <Button type="link" size="small" onClick={() => { confirmForm.resetFields(); setConfirmOrder(r); }}>
                          确认收款
                        </Button>
                        <Popconfirm
                          title={`确认取消订单 ${r.order_no}（￥${Number(r.amount).toLocaleString()}）？取消后需重新下单`}
                          okText="取消订单" okButtonProps={{ danger: true }}
                          onConfirm={async () => {
                            try {
                              await apiCancelOrder(r.id);
                              message.success("订单已取消");
                              loadOrders(orderPg.page);
                            } catch (e) {
                              message.error((e as Error).message);
                            }
                          }}
                        >
                          <Button type="link" size="small" danger>取消</Button>
                        </Popconfirm>
                      </>
                    )}
                    {r.status === "paid" && r.voucher_path && (
                      <Button type="link" size="small" onClick={() => viewer.open(apiVoucherUrl(r.id))}>查看凭证</Button>
                    )}
                    {r.status === "paid" && user?.role === "superadmin" && (
                      <Button type="link" size="small" danger onClick={() => onRefundOrder(r)}>退款</Button>
                    )}
                  </Space>
                ),
              },
            ]}
           scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
          <PaintPagination current={orderPg.page} pageSize={orderPg.pageSize} total={orderTotal}
            onChange={(p, s) => { orderPg.onChange(p, s); updateUrl({ opage: String(p) }); }} />
        </>
      )}

      {tab === "parents" && (
        <>
          {parentListError && (
            <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
              {parentListError} · 本次刷新失败：列表可能不是最新的（不代表没有家长账号）——请重试
              <Typography.Link onClick={() => loadParents(parentPg.page)} style={{ marginLeft: 8 }}>重试</Typography.Link>
            </Typography.Text>
          )}
          <Table<ParentRow> locale={{ emptyText: parentListError ? null : <PaintEmpty character="star" /> }}
            rowKey="id" dataSource={parents} size="middle" loading={parentLoading}
            pagination={false}
            columns={[
              { title: "姓名", dataIndex: "name", width: 140 },
              { title: "手机号", dataIndex: "phone", width: 140 },
              { title: "备注", dataIndex: "remark", width: 160, render: (v) => v || "—" },
              { title: "名下孩子", dataIndex: "children_count", width: 90 },
              { title: "创建时间", dataIndex: "create_time", width: 170, render: (v) => String(v ?? "").slice(0, 19).replace("T", " ") },
              {
                title: "操作", key: "op", width: 160,
                render: (_, r) => (
                  <Space>
                    <Tooltip title={r.has_orders ? "名下孩子已创建订单，禁止修改" : undefined}>
                      <Button type="link" size="small" disabled={r.has_orders} onClick={() => {
                        setEditParent(r);
                        parentEditForm.setFieldsValue({ name: r.name, phone: r.phone, remark: r.remark || "" });
                      }}>编辑</Button>
                    </Tooltip>
                    <Popconfirm
                      title={r.has_orders ? "名下孩子已创建订单，禁止删除" : `确认删除家长 ${r.name}（名下孩子一并隐藏）？`}
                      okText="删除" okButtonProps={{ danger: true }}
                      disabled={r.has_orders}
                      onConfirm={async () => {
                        try {
                          await apiDeleteParent(r.id);
                          message.success("家长已删除（软删，订单历史保留）");
                          loadParents(parentPg.page);
                        } catch (e) {
                          message.error((e as Error).message);
                        }
                      }}
                    >
                      <Tooltip title={r.has_orders ? "名下孩子已创建订单，禁止删除" : undefined}>
                        <Button type="link" size="small" danger disabled={r.has_orders}>删除</Button>
                      </Tooltip>
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
           scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
          <PaintPagination current={parentPg.page} pageSize={parentPg.pageSize} total={parentTotal}
            onChange={(p, s) => { parentPg.onChange(p, s); updateUrl({ ppage: String(p) }); }} />
        </>
      )}

      {/* 新建家长 */}
      <Modal
        title="新建家长账号" open={parentOpen} okText="创建" cancelText="取消" destroyOnHidden
        onOk={async () => {
          const v = await parentForm.validateFields();
          try {
            const created = await apiCreateParent(v);
            message.success(`家长 ${created.name}（${created.phone}）创建成功，已预选为建档家长`);
            setParentOptions([created]); // F1：预选值进 options，避免 antd 显示裸数字 ID
            setParentOpen(false);
            setChildOpen(true);
            setTimeout(() => {
              childForm.resetFields();
              childForm.setFieldsValue({ parent_id: created.id });
            }, 0);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "创建失败");
          }
        }}
        onCancel={() => confirmDiscardIfDirty(parentForm.isFieldsTouched(), () => { setParentOpen(false); parentForm.resetFields(); })}
      >
        <Form form={parentForm} layout="vertical">
          <Form.Item name="name" label="家长姓名" rules={[{ required: true }]}>
            <Input placeholder="如：张女士" />
          </Form.Item>
          <Form.Item name="phone" label="手机号" rules={[{ required: true }, { pattern: /^\d{11}$/, message: "请输入 11 位手机号" }]}>
            <Input placeholder="用于登录与联系" maxLength={11} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 为孩子建档 */}
      <Modal
        title="为孩子建档" open={childOpen} okText="创建" cancelText="取消" destroyOnHidden
        onOk={async () => {
          const v = await childForm.validateFields();
          try {
            await apiCreateChild(v.parent_id, {
              name: v.name, english_name: v.english_name || undefined,
              gender: v.gender, grade: v.grade ?? "",
              avatar: v.avatar || undefined,
            });
            message.success(`孩子 ${v.name} 建档成功（会员状态：未入会）`);
            setChildOpen(false);
            loadChildren(1);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "创建失败");
          }
        }}
        onCancel={() => confirmDiscardIfDirty(childForm.isFieldsTouched(), () => { setChildOpen(false); childForm.resetFields(); })}
      >
        <Form form={childForm} layout="vertical">
          <Form.Item name="parent_id" label="所属家长" rules={[{ required: true }]} extra="输入家长手机号或姓名搜索选择">
            <Select
              showSearch placeholder="搜索家长（姓名 / 手机号）" filterOption={false}
              options={parentOptions.map((p) => ({ value: p.id, label: `${p.name}（${p.phone}）` }))}
              onSearch={searchParents} loading={parentSearching}
              notFoundContent={null}
            />
          </Form.Item>
          <Form.Item name="name" label="孩子姓名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="english_name" label="英文名（榜单展示用）">
            <Input />
          </Form.Item>
          <Form.Item name="gender" label="性别">
            <Radio.Group options={[{ value: 1, label: "男" }, { value: 2, label: "女" }]} />
          </Form.Item>
          <Form.Item name="grade" label="年级">
            <Input placeholder="如：一年级" />
          </Form.Item>
          <Form.Item name="avatar" label="头像（系统内置，可后改）">
            <AvatarPicker />
          </Form.Item>
        </Form>
      </Modal>

      {/* 创建订单 */}
      <Modal
        title="创建订单" open={orderOpen} okText="创建" cancelText="取消" destroyOnHidden
        onOk={async () => {
          const v = await orderForm.validateFields();
          try {
            const order = await apiCreateOrder({
              child_id: v.child_id, order_type: v.order_type, remark: v.remark ?? "",
              ...(v.order_type === "activity_fee" ? { activity_id: v.activity_id } : {}),
              ...(v.order_type === "custom" ? { amount: v.custom_amount } : {}),
            });
            message.success(`订单已创建（金额 ￥${Number(order.amount).toLocaleString()}），请到「订单」页确认收款`);
            setOrderOpen(false);
            changeTab("orders");
            if (orderPg.page !== 1) orderPg.setPage(1);
            else loadOrders(1);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "创建失败");
          }
        }}
        onCancel={() => confirmDiscardIfDirty(orderForm.isFieldsTouched(), () => { setOrderOpen(false); setSelectedOrderChild(null); orderForm.resetFields(); })}
      >
        <Form form={orderForm} layout="vertical">
          <Form.Item name="child_id" hidden rules={[{ required: true, message: "请选择孩子" }]} />
          <Form.Item label="孩子">
            <Typography.Text strong>
              {selectedOrderChild ? `${selectedOrderChild.name}（${selectedOrderChild.parent_phone}）` : "—"}
            </Typography.Text>
          </Form.Item>
          <Form.Item name="order_type" label="订单类型" rules={[{ required: true }]} extra="年费金额自动判定二孩折扣；观察期/活动费不打折">
            <Select options={[
              // E-20260912-10：金额一律读后端配置（禁硬编码），调价后文案自动跟随
              {
                value: "observation_fee",
                label: `观察期会员费（${money(cfgNum(configs, "observation_fee", 500))} 元/月）`,
              },
              {
                value: "formal_fee",
                label: `正式年费（${money(formalFee)} 元，二孩 ${money(siblingFee)}，省 ${money(
                  formalFee - siblingFee,
                )}）`,
              },
              {
                value: "first_activity_fee",
                label: `首场亲子活动（${cfgNum(configs, "first_activity_fee", 99)} 元，每账号一次）`,
              },
              {
                value: "deposit",
                label: `押金（${money(cfgNum(configs, "deposit_amount", 1200))} 元，标准配置）`,
              },
              { value: "activity_fee", label: "活动费（选活动带出金额）" },
              { value: "custom", label: "自定义（自输说明与金额）" },
            ]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(a, b) => a.order_type !== b.order_type}>
            {({ getFieldValue }) =>
              getFieldValue("order_type") === "activity_fee" ? (
                <Form.Item name="activity_id" label="选择活动" rules={[{ required: true, message: "请选择活动" }]}>
                  <Select
                    placeholder="选择活动（金额带出）" showSearch optionFilterProp="label"
                    options={activityOptions}
                  />
                </Form.Item>
              ) : getFieldValue("order_type") === "custom" ? (
                <>
                  <Form.Item name="custom_amount" label="金额（元）"
                    rules={[{ required: true, message: "请输入金额" },
                      { pattern: /^\d+(\.\d{1,2})?$/, message: "最多两位小数" }]}>
                    <InputNumber style={{ width: "100%" }} min={0.01} max={99999} step={0.01} />
                  </Form.Item>
                  <Form.Item name="remark" label="类型说明（必填）"
                    rules={[{ required: true, message: "自定义单必须填写类型说明" }]}>
                    <Input placeholder="如：春季材料费" maxLength={100} />
                  </Form.Item>
                </>
              ) : getFieldValue("order_type") === "deposit" ? (
                <Typography.Text type="secondary">押金金额按标准配置（{money(cfgNum(configs, "deposit_amount", 1200))} 元），确认收款后自动激活押金账户。</Typography.Text>
              ) : null}
            </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 确认收款 */}
      <Modal
        title={`确认收款：${confirmOrder ? ORDER_TYPE_LABEL[confirmOrder.order_type] ?? confirmOrder.order_type : ""}`}
        open={confirmOrder !== null} okText="确认收款" cancelText="取消" destroyOnHidden
        onOk={async () => {
          if (!confirmOrder) return;
          const v = await confirmForm.validateFields();
          try {
            // WM3-B2 两步式：选了凭证图先上传（可选传），再确认收款
            const vf = voucherFile[0];
            if (vf?.originFileObj) {
              const fd = new FormData();
              fd.append("file", vf.originFileObj);
              await apiUploadVoucher(confirmOrder.id, fd);
            }
            await apiConfirmPayment(confirmOrder.id, { pay_method: v.pay_method, remark: v.remark ?? "" });
            // S2（#2）：toast 按 order_type 分文案——"会员权益已开通"仅适用会员费单
            const CONFIRM_TOAST: Record<string, string> = {
              observation_fee: "收款确认成功，观察期会员权益已开通",
              formal_fee: "收款确认成功，年费会员权益已开通",
              first_activity_fee: "收款确认成功，首场活动费已确认",
              deposit: "收款确认成功，押金已入账",
              deposit_supplement: "收款确认成功，押金补缴已入账",
              activity_fee: "收款确认成功，活动费已确认",
              custom: "收款确认成功，款项已确认",
            };
            message.success(CONFIRM_TOAST[confirmOrder.order_type] ?? "收款确认成功");
            setConfirmOrder(null);
            setVoucherFile([]);
            loadOrders(orderPg.page);
            refreshOrderCounts(); // B4：确认收款 → 待确认数即变（同点触发，不刷页面）
          } catch (e) {
            message.error(e instanceof Error ? e.message : "确认失败");
          }
        }}
        onCancel={() => confirmDiscardIfDirty(confirmForm.isFieldsTouched(), () => { setConfirmOrder(null); setVoucherFile([]); confirmForm.resetFields(); })}
      >
        {confirmOrder && (
          <Typography.Paragraph>
            金额：<Typography.Text strong style={{ fontSize: 18 }}>￥{Number(confirmOrder.amount).toLocaleString()}</Typography.Text>
            <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
              {confirmOrder.child_name} · {confirmOrder.parent_name}
            </Typography.Text>
          </Typography.Paragraph>
        )}
        <Form form={confirmForm} layout="vertical">
          <Form.Item name="pay_method" label="收款方式" rules={[{ required: true }]}>
            <Select options={PAY_METHOD_OPTIONS} placeholder="选择实际收款方式" />
          </Form.Item>
          <Form.Item name="remark" label="凭证说明（留痕）">
            <Input.TextArea rows={2} placeholder="如：转账截图已存档 / 备注家长姓名" />
          </Form.Item>
          {/* WM3-B2：收款凭证上传（单图，可选传，带预览；统一转 JPG 存储） */}
          <Form.Item label="收款凭证图（可选）" extra="转账/扫码截图存档，已支付订单可回看">
            <Upload
              listType="picture-card" fileList={voucherFile}
              beforeUpload={() => false}
              onChange={({ fileList: fl }) => setVoucherFile(fl.slice(0, 1))}
              onPreview={(file) => {
                // 本地 blob 预览（不走后端；不用 antd 默认 window.open 会开空白页）
                if (file.originFileObj) {
                  const url = URL.createObjectURL(file.originFileObj);
                  viewer.open(url, () => URL.revokeObjectURL(url));
                }
              }}
              accept=".png,.jpg,.jpeg,.webp"
            >
              {voucherFile.length < 1 ? "+ 凭证图" : null}
            </Upload>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={obsChild ? `为 ${obsChild.name} 上传评估报告` : ""}
        open={!!obsChild} okText="上传" cancelText="取消" destroyOnHidden
        onOk={onUploadObservation}
        onCancel={() => confirmDiscardIfDirty(
          obsFileList.length > 0 || obsRemark.trim() !== "",
          () => { setObsChild(null); setObsFileList([]); setObsRemark(""); }
        )}
      >
        <Upload
          listType="picture-card" fileList={obsFileList}
          beforeUpload={() => false}
          onChange={({ fileList: fl }) => setObsFileList(fl.slice(0, 9))}
          onPreview={(file) => {
            // 同上：评估报告图也走全屏干净预览，禁 antd 默认 window.open
            if (file.originFileObj) {
              const url = URL.createObjectURL(file.originFileObj);
              viewer.open(url, () => URL.revokeObjectURL(url));
            }
          }}
          accept=".png,.jpg,.jpeg"
        >
          {obsFileList.length < 9 ? "+ 图片" : null}
        </Upload>
        <Input.TextArea
          rows={2} style={{ marginTop: 12 }} value={obsRemark}
          onChange={(e) => setObsRemark(e.target.value)}
          placeholder="评估备注（如：第一阶段听力优秀）"
        />
      </Modal>

      <Drawer
        title={readingChild ? `${readingChild.name} 的阅读档案` : "阅读档案"}
        width={520} open={!!readingChild}
        onClose={() => { setReadingChild(null); setReadingProfile(null); setReadingError(""); }}
      >
        {readingLoading && <Typography.Text type="secondary">加载中…</Typography.Text>}
        {!readingLoading && readingError && (
          <Typography.Text type="warning" style={{ display: "block" }}>
            {readingError} · 阅读档案未加载（不代表这个孩子没有阅读数据）——请重试
            {readingChild && (
              <Typography.Link onClick={() => openReadingProfile(readingChild)} style={{ marginLeft: 8 }}>
                重试
              </Typography.Link>
            )}
          </Typography.Text>
        )}
        {readingProfile && (
          <>
            <Descriptions column={2} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="已读完">{readingProfile.total_finished} 本</Descriptions.Item>
              <Descriptions.Item label="阅读时长">{readingProfile.total_reading_minutes} 分钟</Descriptions.Item>
              <Descriptions.Item label="打卡天数">{readingProfile.total_checkin_days} 天</Descriptions.Item>
              <Descriptions.Item label="当前连续">{readingProfile.current_streak} 天</Descriptions.Item>
            </Descriptions>
            <Typography.Title level={5}>完播书单</Typography.Title>
            <List
              size="small"
              dataSource={readingProfile.finished_books}
              locale={{ emptyText: <PaintEmpty character="bookworm" message="还没有听完的书" /> }}
              renderItem={(b) => (
                <List.Item>
                  <List.Item.Meta
                    title={`${b.title}（${b.word_count ?? "?"} 词）`}
                    description={`${b.finished_at.slice(0, 16).replace("T", " ")} · 时长 ${b.reading_minutes} 分钟`}
                  />
                </List.Item>
              )}
            />
          </>
        )}
      </Drawer>

      <Modal
        title={`编辑孩子资料 — ${editChild?.name ?? ""}`}
        open={!!editChild}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
        onOk={async () => {
          if (!editChild) return;
          try {
            const v = await editForm.validateFields();
            // P2-60（第八轮）：**`|| undefined` 表达不了"清空"**——字段被省略 = 后端视为"不改"，
            // 于是英文名/年级/生日**永远清不掉**（表单里删掉、保存后原值还在）。
            // 口径：文本字段送**空串**（后端 `english_name or None` 落 NULL）；日期字段送 **null**
            // （后端按"显式传 null"清空，见 `update_profile(clear_birthday=...)`）。
            // R11-d2/A10：AR 只升不降——清空不能被静默吞掉（`|| undefined` 丢键后仍提示"已保存"）
            if (editChild.ar_level && !v.ar_level) {
              message.warning("AR 值只升不降，不能清空；如需修正请联系技术");
              return;
            }
            await apiUpdateChild(editChild.id, {
              name: v.name || undefined,
              gender: v.gender ?? undefined,
              // P1-5（第九轮审查）：控件是 `<Input>`（值恒为字符串），原写法 `v.birthday.format(...)`
              // 打在字符串上必抛 `TypeError: v.birthday.format is not a function` → **保存整体失败**
              // （姓名/英文名/年级一起存不进去，且只弹一句报错）。统一按字符串送；空串 = 清空
              // （后端 schema `birthday: date | None` 会把非法格式挡成 422，错误文案走上面的 catch）。
              birthday: (v.birthday || "").trim() || null,
              english_name: v.english_name ?? "",
              grade: v.grade ?? "",
              ar_level: v.ar_level || undefined,
              avatar: v.avatar ?? "",
            });
            message.success("已保存");
            setEditChild(null);
            loadChildren(childPg.page);
          } catch (e) {
            if ((e as Error).message) message.error((e as Error).message);
          }
        }}
        onCancel={() => confirmDiscardIfDirty(editForm.isFieldsTouched(), () => { setEditChild(null); editForm.resetFields(); })}
      >
        <Form form={editForm} layout="vertical">
          {editChild?.has_orders && (
            <Typography.Paragraph type="warning">
              该孩子已创建订单：姓名/性别/生日锁定，英文名/年级/AR 仍可修改
            </Typography.Paragraph>
          )}
          <Form.Item name="name" label="孩子姓名" rules={[{ required: true }]}>
            <Input maxLength={64} disabled={editChild?.has_orders} />
          </Form.Item>
          <Form.Item name="gender" label="性别">
            <Radio.Group options={[{ value: 1, label: "男" }, { value: 2, label: "女" }]} disabled={editChild?.has_orders} />
          </Form.Item>
          <Form.Item name="birthday" label="生日">
            <Input placeholder="如 2020-05-01" maxLength={10} disabled={editChild?.has_orders} />
          </Form.Item>
          {/* F7：学籍动态字段（英文名/年级/AR）放开（有订单也可改） */}
          <Form.Item name="english_name" label="英文名（榜单展示用）">
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item name="grade" label="年级">
            <Input maxLength={50} />
          </Form.Item>
          <Form.Item name="avatar" label="头像（系统内置）">
            <AvatarPicker />
          </Form.Item>
          <Form.Item
            name="ar_level"
            label="AR 值（老师评估，只升不降）"
            extra="首次填写任意值；再次填写仅允许高于当前值（降级将被拒绝）"
          >
            <Input maxLength={10} placeholder="如 3.5" />
          </Form.Item>
        </Form>
      </Modal>

      {/* WM3-B1 编辑家长 */}
      <Modal
        title={`编辑家长 — ${editParent?.name ?? ""}`}
        open={!!editParent}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
        onOk={async () => {
          if (!editParent) return;
          try {
            const v = await parentEditForm.validateFields();
            await apiUpdateParent(editParent.id, {
              name: v.name || undefined,
              phone: v.phone || undefined,
              remark: v.remark ?? undefined,
            });
            message.success(v.phone && v.phone !== editParent.phone ? "已保存（手机号即登录账号，家长需用新号登录）" : "已保存");
            setEditParent(null);
            loadParents(parentPg.page);
          } catch (e) {
            if ((e as Error).message) message.error((e as Error).message);
          }
        }}
        onCancel={() => confirmDiscardIfDirty(parentEditForm.isFieldsTouched(), () => { setEditParent(null); parentEditForm.resetFields(); })}
      >
        <Form form={parentEditForm} layout="vertical">
          <Form.Item name="name" label="家长姓名" rules={[{ required: true }]}>
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item
            name="phone"
            label="手机号（登录标识）"
            rules={[{ required: true, message: "手机号必填（登录账号）" }, { pattern: /^\d{11}$/, message: "请输入 11 位手机号" }]}
            extra="手机号即小程序登录账号，修改后家长需用新号登录"
          >
            <Input maxLength={11} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 收款凭证 / 选图本地预览：统一走全屏干净预览（无窗口壳，可缩放旋转） */}
      {viewer.node}
    </>
  );
}
