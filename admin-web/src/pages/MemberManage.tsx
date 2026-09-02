import { useCallback, useEffect, useRef, useState } from "react";
import {
  App as AntdApp,
  Button,
  Descriptions,
  Drawer,
  Form,
  Input,
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
import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";

import {
  apiGetChildReading,
  type ChildReadingProfile,
} from "../api/reservations";
import { apiUploadObservation } from "../api/observation";
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
import { usePaintPagination } from "../hooks/usePaintPagination";

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
  deposit: "押金", deposit_supplement: "押金补缴",
};
const ORDER_STATUS_LABEL: Record<string, string> = {
  pending_payment: "待支付", pending_manual_confirm: "待人工确认",
  paid: "已支付", cancelled: "已取消", refunded: "已退款",
};
const PAY_METHOD_OPTIONS = [
  { value: "scan", label: "微信扫码" },
  { value: "alipay", label: "支付宝" },
  { value: "transfer", label: "对公转账" },
  { value: "card", label: "刷卡" },
  { value: "cash", label: "现金" },
];

export default function MemberManage() {
  const { message } = AntdApp.useApp();
  const { user } = useAuth();
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
  const [tab, setTab] = useState<string>(() => {
    const t0 = readUrl().get("tab");
    return t0 === "orders" || t0 === "parents" ? t0 : "children";
  });
  const changeTab = useCallback((t: string) => {
    setTab(t);
    updateUrl({ tab: t, page: undefined });
  }, [updateUrl]);
  // 孩子列表
  const [children, setChildren] = useState<Child[]>([]);
  const [childTotal, setChildTotal] = useState(0);
  const [keyword, setKeyword] = useState<string>(() => readUrl().get("keyword") ?? "");
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  // 订单列表
  const [orders, setOrders] = useState<Order[]>([]);
  const [orderTotal, setOrderTotal] = useState(0);
  const [orderStatus, setOrderStatus] = useState<string | undefined>();
  const [orderKeyword, setOrderKeyword] = useState<string>(() => readUrl().get("okeyword") ?? ""); // WM3-A5
  const [orderLoading, setOrderLoading] = useState(false); // W6
  const [orderBy, setOrderBy] = useState<string | undefined>(); // W7
  const [orderCounts, setOrderCounts] = useState<{ pending_manual_confirm: number; total: number }>({ pending_manual_confirm: 0, total: 0 }); // W3
  // WM3-B1 家长管理 tab
  const [parents, setParents] = useState<ParentRow[]>([]);
  const [parentTotal, setParentTotal] = useState(0);
  const [parentLoading, setParentLoading] = useState(false);
  const [editParent, setEditParent] = useState<ParentRow | null>(null);
  const [parentEditForm] = Form.useForm();
  // WM3-B2 凭证：确认收款弹窗单图 + 查看凭证 Modal
  const [voucherFile, setVoucherFile] = useState<UploadFile[]>([]);
  const [viewVoucher, setViewVoucher] = useState<Order | null>(null);
  // F5：本地预览（onPreview 自定义；antd 默认 window.open(file.name)=空白页）
  const [localPreview, setLocalPreview] = useState<string | null>(null);
  const childPg = usePaintPagination(undefined, urlInitialPage);
  const orderPg = usePaintPagination(undefined, urlInitialPage);
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
      apiSearchParents("").then((list) => setParentOptions(list.slice(0, 5))).catch(() => setParentOptions([]));
      return;
    }
    parentSearchTimer.current = window.setTimeout(() => {
      setParentSearching(true);
      apiSearchParents(kw.trim())
        .then(setParentOptions)
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
  const [orderForm] = Form.useForm();
  const [confirmForm] = Form.useForm();
  const [editChild, setEditChild] = useState<Child | null>(null);
  const [editForm] = Form.useForm();

  const loadChildren = useCallback(
    (page: number) => {
      setLoading(true);
      apiListChildren({ page, page_size: childPg.pageSize, keyword: keyword || undefined, status: statusFilter })
        .then((r) => { setChildren(r.items ?? []); setChildTotal(r.total); })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [keyword, statusFilter, childPg.pageSize, message]
  );

  const loadOrders = useCallback(
    (page: number) => {
      setOrderLoading(true);
      apiListOrders({ page, page_size: orderPg.pageSize, status: orderStatus, order_by: orderBy, keyword: orderKeyword || undefined })
        .then((r) => { setOrders(r.items ?? []); setOrderTotal(r.total); })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setOrderLoading(false));
    },
    [orderStatus, orderBy, orderKeyword, orderPg.pageSize, message]
  );

  useEffect(() => { if (tab === "children") loadChildren(childPg.page); }, [loadChildren, childPg.page, tab]);

  const onRefundOrder = (o: Order) => {
    let remarkInput = "";
    Modal.confirm({
      title: "订单退款执行",
      content: (
        <div>
          <div style={{ marginBottom: 8 }}>
            {o.order_no} · ￥{Number(o.amount).toLocaleString()}（仅超管；99 元资格随退款恢复）
          </div>
          <Input.TextArea rows={2} onChange={(e) => { remarkInput = e.target.value; }} placeholder="退款说明（留痕）" />
        </div>
      ),
      okText: "确认退款",
      onOk: async () => {
        try {
          await apiRefundOrder(o.id, remarkInput || "退款执行");
          message.success("订单已退款");
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
          setTab("orders");
          loadOrders(1);
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
    setReadingLoading(true);
    apiGetChildReading(child.id)
      .then(setReadingProfile)
      .catch((e: Error) => message.error(e.message))
      .finally(() => setReadingLoading(false));
  }, [message]);
  useEffect(() => { if (tab === "orders") loadOrders(orderPg.page); }, [loadOrders, orderPg.page, tab]);

  // WM3-B1 家长管理列表
  const loadParents = useCallback(
    (page: number) => {
      setParentLoading(true);
      apiListParentsPage({ page, page_size: childPg.pageSize })
        .then((r) => { setParents(r.items ?? []); setParentTotal(r.total); })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setParentLoading(false));
    },
    [childPg.pageSize, message]
  );
  // F1（同族第三次，E-03 反模式 1）：家长 tab 标题计数唯一数据源=loadParents 的
  // total——守卫导致默认 tab 下「家长（0）」。挂载即拉（与 A1 对称，模式已验证无害）
  useEffect(() => { loadParents(childPg.page); }, [loadParents, childPg.page, tab]);

  // W3 订单 Tab 待确认计数（轻量单请求）；WM3-A1：页面挂载即拉，不设 tab 守卫
  // （WM13-F4 同族漏网：守卫导致默认 tab 下计数恒 0；orderTotal 变化自动刷新）
  useEffect(() => {
    apiOrderCounts()
      .then((c) => setOrderCounts({ pending_manual_confirm: c.pending_manual_confirm, total: c.total }))
      .catch(() => { /* 静默：计数失败不阻塞列表 */ });
  }, [tab, orderTotal]);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Typography.Title level={4} style={{ fontFamily: "var(--font-display)", margin: 0 }}>
          会员管理
        </Typography.Title>
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
              订单（{orderCounts.total || orderTotal} · 待确认{" "}
              {orderCounts.pending_manual_confirm > 0 ? (
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
          <Table<Child> locale={{ emptyText: <PaintEmpty character="star" /> }}
            rowKey="id" loading={loading} dataSource={children} size="middle"
            pagination={false}
            columns={[
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
          />
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
              onSearch={(v) => { setOrderKeyword(v); orderPg.setPage(1); updateUrl({ okeyword: v || undefined, page: undefined }); }}
            />
            <Select
              placeholder="订单状态" allowClear style={{ width: 140 }} value={orderStatus}
              onChange={(v) => { setOrderStatus(v); orderPg.setPage(1); }}
              // WM3-A4：头部"全部"选项
              options={[{ value: "", label: "全部" }, ...Object.entries(ORDER_STATUS_LABEL).map(([value, label]) => ({ value, label }))]}
            />
          </Space>
          <Table<Order> locale={{ emptyText: <PaintEmpty character="star" /> }}
            rowKey="id" dataSource={orders} size="middle" loading={orderLoading}
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
              { title: "类型", dataIndex: "order_type", width: 120, render: (t: string) => ORDER_TYPE_LABEL[t] ?? t },
              { title: "孩子", dataIndex: "child_name", width: 90, render: (v) => v ?? "—" },
              { title: "家长", dataIndex: "parent_name", width: 90 },
              { title: "金额", dataIndex: "amount", key: "amount", width: 100, sorter: true, render: (v: string) => <Typography.Text strong>￥{Number(v).toLocaleString()}</Typography.Text> },
              { title: "状态", dataIndex: "status", width: 110, render: (s: string) => (
                <Tag color={s === "paid" ? "green" : s === "cancelled" ? "default" : s === "refunded" ? "volcano" : "orange"}>
                  {ORDER_STATUS_LABEL[s] ?? s}
                </Tag>
              ) },
              { title: "收款方式", dataIndex: "pay_method", width: 100, render: (v) => v ? PAY_METHOD_OPTIONS.find((o) => o.value === v)?.label ?? v : "—" },
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
                      <Button type="link" size="small" onClick={() => setViewVoucher(r)}>查看凭证</Button>
                    )}
                    {r.status === "paid" && user?.role === "superadmin" && (
                      <Button type="link" size="small" danger onClick={() => onRefundOrder(r)}>退款</Button>
                    )}
                  </Space>
                ),
              },
            ]}
          />
          <PaintPagination current={orderPg.page} pageSize={orderPg.pageSize} total={orderTotal}
            onChange={(p, s) => { orderPg.onChange(p, s); updateUrl({ page: String(p) }); }} />
        </>
      )}

      {tab === "parents" && (
        <>
          <Table<ParentRow> locale={{ emptyText: <PaintEmpty character="star" /> }}
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
                          loadParents(childPg.page);
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
          />
          <PaintPagination current={childPg.page} pageSize={childPg.pageSize} total={parentTotal}
            onChange={(p, s) => { childPg.onChange(p, s); updateUrl({ page: String(p) }); }} />
        </>
      )}

      {/* 新建家长 */}
      <Modal
        title="新建家长账号" open={parentOpen} okText="创建" cancelText="取消" destroyOnClose
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
        title="为孩子建档" open={childOpen} okText="创建" cancelText="取消" destroyOnClose
        onOk={async () => {
          const v = await childForm.validateFields();
          try {
            await apiCreateChild(v.parent_id, {
              name: v.name, english_name: v.english_name || undefined,
              gender: v.gender, grade: v.grade ?? "",
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
        </Form>
      </Modal>

      {/* 创建订单 */}
      <Modal
        title="创建订单" open={orderOpen} okText="创建" cancelText="取消" destroyOnClose
        onOk={async () => {
          const v = await orderForm.validateFields();
          try {
            const order = await apiCreateOrder({ child_id: v.child_id, order_type: v.order_type, remark: v.remark ?? "" });
            message.success(`订单已创建（金额 ￥${Number(order.amount).toLocaleString()}），请到「订单」页确认收款`);
            setOrderOpen(false);
            setTab("orders");
            loadOrders(1);
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
              { value: "observation_fee", label: "观察期会员费（500 元/月）" },
              { value: "formal_fee", label: "正式年费（6000 元，二孩自动 5400）" },
              { value: "first_activity_fee", label: "首场亲子活动（99 元，每账号一次）" },
            ]} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 确认收款 */}
      <Modal
        title={`确认收款：${confirmOrder ? ORDER_TYPE_LABEL[confirmOrder.order_type] ?? confirmOrder.order_type : ""}`}
        open={confirmOrder !== null} okText="确认收款" cancelText="取消" destroyOnClose
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
            message.success("收款确认成功，会员权益已开通");
            setConfirmOrder(null);
            setVoucherFile([]);
            loadOrders(orderPg.page);
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
                // F5：本地 blob 预览（不走后端；URL.createObjectURL 挂 unmount 回收）
                if (file.originFileObj) {
                  const url = URL.createObjectURL(file.originFileObj);
                  setLocalPreview(url);
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
        open={!!obsChild} okText="上传" cancelText="取消" destroyOnClose
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
            // F5 同族（E-03 反模式 3）：评估报告图同样禁 antd 默认 window.open
            if (file.originFileObj) {
              const url = URL.createObjectURL(file.originFileObj);
              setLocalPreview(url);
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
        onClose={() => { setReadingChild(null); setReadingProfile(null); }}
      >
        {readingLoading && <Typography.Text type="secondary">加载中…</Typography.Text>}
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
        destroyOnClose
        onOk={async () => {
          if (!editChild) return;
          try {
            const v = await editForm.validateFields();
            await apiUpdateChild(editChild.id, {
              name: v.name || undefined,
              gender: v.gender ?? undefined,
              birthday: v.birthday || undefined,
              english_name: v.english_name || undefined,
              grade: v.grade || undefined,
              ar_level: v.ar_level || undefined,
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
              该孩子已创建订单，禁止修改/删除（守卫口径：订单为消费留痕，R-315 解禁说明）
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
          <Form.Item name="english_name" label="英文名（榜单展示用）">
            <Input maxLength={64} disabled={editChild?.has_orders} />
          </Form.Item>
          <Form.Item name="grade" label="年级">
            <Input maxLength={50} disabled={editChild?.has_orders} />
          </Form.Item>
          <Form.Item
            name="ar_level"
            label="AR 值（老师评估，只升不降）"
            extra="首次填写任意值；再次填写仅允许高于当前值（降级将被拒绝）"
          >
            <Input maxLength={10} placeholder="如 3.5" disabled={editChild?.has_orders} />
          </Form.Item>
        </Form>
      </Modal>

      {/* WM3-B1 编辑家长 */}
      <Modal
        title={`编辑家长 — ${editParent?.name ?? ""}`}
        open={!!editParent}
        okText="保存"
        cancelText="取消"
        destroyOnClose
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
            loadParents(childPg.page);
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
            rules={[{ pattern: /^\d{11}$/, message: "请输入 11 位手机号" }]}
            extra="手机号即小程序登录账号，修改后家长需用新号登录"
          >
            <Input maxLength={11} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>

      {/* WM3-B2 查看收款凭证 */}
      <Modal
        title={`收款凭证 — ${viewVoucher ? viewVoucher.order_no : ""}`}
        open={!!viewVoucher}
        footer={null}
        onCancel={() => setViewVoucher(null)}
        width={640}
      >
        {viewVoucher?.voucher_path ? (
          <img src={apiVoucherUrl(viewVoucher.id)} alt="收款凭证" style={{ maxWidth: "100%" }} />
        ) : null}
      </Modal>

      {/* F5 本地预览（确认收款弹窗选图后点眼睛） */}
      <Modal
        title="凭证图预览（本地上传前）"
        open={!!localPreview}
        footer={null}
        onCancel={() => {
          if (localPreview) URL.revokeObjectURL(localPreview);
          setLocalPreview(null);
        }}
        width={640}
      >
        {localPreview ? <img src={localPreview} alt="凭证预览" style={{ maxWidth: "100%" }} /> : null}
      </Modal>
    </>
  );
}
