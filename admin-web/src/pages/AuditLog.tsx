import { useCallback, useEffect, useState } from "react";
import { App as AntdApp, Select, Space, Table, Tag, Typography } from "antd";
import PageTitle from "../components/PageTitle";

import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";

import { apiListAuditLogs, type AuditLog } from "../api/admin";
import { usePaintPagination } from "../hooks/usePaintPagination";
import { PaintHScrollbar } from "../components/PaintHScrollbar";

// P3 管理端族（2026-10-22 第一百一十二批）：原先只列 2 项（login / config.update），
// 后端实际写入 **67 个 action**（`publish_audit(action="…")` 字面量实测去重）→ 其余既**筛不出来**、
// 场景列还显示**裸英文**。本表与后端**逐一对齐**；新增 action 必须同步这里
// （守护用例 `tests/unit/test_audit8_p3_admin_labels_20261022.py` 钉住"后端 ⊆ 前端"）。
const ACTION_LABEL: Record<string, string> = {
  "login": "登录",
  "config.update": "配置变更",
  "activity.cancel": "取消活动",
  "activity.cover": "活动封面",
  "activity.create": "创建活动",
  "activity.detail_blocks": "活动图文块",
  "activity.detail_image": "活动详情图",
  "activity.refund_review": "活动退款审核",
  "activity.signin": "活动签到",
  "activity.update": "编辑活动",
  "admin_notification.handle": "待办销项",
  "book.import": "图书批量导入",
  "child.create": "建档孩子",
  "child.delete": "删除孩子",
  "child.evaluate_approve": "评估通过（转正）",
  "child.mark_pending_evaluation": "置待评估",
  "child.update_profile": "编辑孩子档案",
  "circle.admin_like": "阅读圈点赞",
  "circle.admin_unlike": "阅读圈取消赞",
  "circle.delete_post": "删帖",
  "circle.pin": "置顶帖",
  "circle.unpin": "取消置顶",
  "circulation.borrow": "借书",
  "circulation.copy_unavailable_with_active_reservation": "副本不可用（有在用预约）",
  "circulation.renew": "续借",
  "circulation.return": "还书",
  "dead_letter.handle": "死信销项",
  "deposit.deduct": "押金扣除（赔偿）",
  "deposit.pay": "押金收款确认",
  "deposit.refund_overdraft": "押金超额退款",
  "deposit.supplement_order": "创建补缴单",
  "growth.level_recalc": "等级重算",
  "growth.milestone_check": "里程碑检查",
  "growth.points_adjust": "积分调整",
  "growth.report_generate": "生成报告",
  "media.purge": "回收站清空",
  "media.restore": "媒体还原",
  "media.trash": "媒体移入回收站",
  "member.transition_rejected": "会员状态转移被拒",
  "notification.toggle_read": "消息已读切换",
  "observation.report_upload": "评估报告上传",
  "order.cancel": "取消订单",
  "order.confirm_payment": "确认收款",
  "order.create": "创建订单",
  "order.payment_callback": "支付回调",
  "order.refund_apply": "发起退款申请",
  "order.voucher": "上传收款凭证",
  "parent.create": "建档家长",
  "parent.delete": "删除家长",
  "parent.update": "编辑家长",
  "payment.anomaly": "支付异常留痕",
  "quiz.attempts_reset": "重置测验机会",
  "refund.execute": "执行退款",
  "refund.gateway_queried": "退款查单",
  "refund.gateway_query_failed": "退款查单失败",
  "refund.gateway_submitted": "退款提交网关",
  "refund.gateway_unknown": "退款网关结果未知",
  "refund.notify_state_skipped": "退款通知跳过",
  "refund.review": "退款审核",
  "reservation.copy_switched": "预约改锁副本",
  "reservation.copy_unavailable_exception": "预约转异常",
  "staff.create": "新建后台账号",
  "staff.reset_password": "重置密码",
  "staff.status": "启停账号",
  "staff.update": "编辑账号",
  "task.manual_run": "手动执行任务",
  "transfer.annual_fee_no_refund": "转让年费不退留痕",
  "transfer.approve": "转让审核通过",
  "withdrawal.approve": "退会审核通过",
};

const ACTION_COLOR: Record<string, string> = {
  login: "default",
  "config.update": "blue",
  // 资金族红、状态机紫、内容族绿、系统族灰（只影响观感，未知 action 走 default）
  "deposit.pay": "red", "deposit.deduct": "red", "deposit.supplement_order": "red",
  "order.confirm_payment": "red", "order.payment_callback": "red", "refund.execute": "red",
  "refund.review": "red", "order.refund_apply": "red", "activity.refund_review": "red",
  "withdrawal.approve": "purple", "transfer.approve": "purple", "child.evaluate_approve": "purple",
  "activity.signin": "green", "circle.pin": "green", "circle.delete_post": "green",
  "media.trash": "gray", "media.purge": "gray", "media.restore": "gray",
};

// 对象类型同样与后端 `target_type="…"` 对齐（实测 21 个）
const TARGET_LABEL: Record<string, string> = {
  "activity": "活动",
  "admin_notification": "待办事项",
  "admin_user": "后台账号",
  "book": "书目",
  "book_copy": "副本",
  "borrow": "借阅记录",
  "child": "孩子",
  "circle_post": "阅读圈帖子",
  "config": "配置",
  "dead_letter": "事件死信",
  "deposit": "押金账户",
  "enrollment": "活动报名",
  "media": "媒体文件",
  "notification": "站内消息",
  "order": "订单",
  "parent": "家长",
  "refund_request": "退款单",
  "reservation": "预约",
  "system_config": "系统配置",
  "task": "定时任务",
  "transfer": "转让申请",
};

/** detail JSON → 人话（配置变更渲染为「旧值 → 新值」） */
function renderDetail(log: AuditLog): string {
  if (log.action === "config.update") {
    try {
      const d = JSON.parse(log.detail) as { old?: string; new?: string };
      const oldV = d.old === "true" ? "开启" : d.old === "false" ? "关闭" : d.old;
      const newV = d.new === "true" ? "开启" : d.new === "false" ? "关闭" : d.new;
      return `${oldV ?? "?"} → ${newV ?? "?"}`;
    } catch {
      /* 解析失败回落原始文本 */
    }
  }
  if (log.action === "login") return "登录成功";
  return log.detail || "—";
}

/** target_type:target_id → 友好对象名（配置项显示中文，账号显示操作人自己） */
function renderTarget(log: AuditLog, configNames: Record<string, string>): string {
  if (log.target_type === "system_config") {
    return configNames[log.target_id] ?? log.target_id;
  }
  if (log.target_type === "admin_user") {
    return log.actor_name || "账号";
  }
  return log.target_id || "—";
}

export default function AuditLogPage() {
  const { message } = AntdApp.useApp();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const { page, pageSize, setPage, onChange: onPageChange } = usePaintPagination();
  const [action, setAction] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [configNames, setConfigNames] = useState<Record<string, string>>({});

  useEffect(() => {
    import("../api/admin")
      .then(({ apiListConfigs }) => apiListConfigs())
      .then((configs) => {
        const map: Record<string, string> = {};
        for (const c of configs) map[c.config_key] = c.display_name || c.description || c.config_key;
        setConfigNames(map);
      })
      // T26 全量枚举处置：降级保留静默——失败时展示 config_key 原文（数据本体在），
      // 仅 display_name 美化缺失，不构成"假 0 误判"
      .catch(() => undefined);
  }, []);

  const load = useCallback(
    (targetPage: number) => {
      setLoading(true);
      apiListAuditLogs({ page: targetPage, page_size: pageSize, action })
        .then((result) => {
          setLogs(result.items ?? []);
          setTotal(result.total);
        })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [action, pageSize, message]
  );

  useEffect(() => {
    load(page);
  }, [load, page]);

  return (
    <>
      <PageTitle>审计日志</PageTitle>
      <Typography.Paragraph type="secondary">
        敏感操作（登录 / 配置变更 / 资金操作 / 人工放行）的留痕记录；只读不可篡改。
      </Typography.Paragraph>
      <Space style={{ marginBottom: 16 }}>
        <Select
          placeholder="按动作筛选"
          allowClear
          style={{ width: 140 }}
          value={action}
          onChange={(v) => {
            setAction(v);
            setPage(1);
          }}
          options={Object.entries(ACTION_LABEL).map(([value, label]) => ({ value, label }))}
        />
      </Space>
      <Table<AuditLog> locale={{ emptyText: <PaintEmpty character="bird" /> }}
        rowKey="id"
        loading={loading}
        dataSource={logs}
        size="middle"
        pagination={false}
        columns={[
          { title: "时间", dataIndex: "created_at", width: 170 },
          { title: "操作人", dataIndex: "actor_name", width: 110 },
          {
            title: "动作",
            dataIndex: "action",
            width: 100,
            render: (a: string) => (
              <Tag color={ACTION_COLOR[a] ?? "default"}>{ACTION_LABEL[a] ?? a}</Tag>
            ),
          },
          {
            title: "对象",
            key: "target",
            width: 200,
            render: (_, r) => (
              <span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {TARGET_LABEL[r.target_type] ?? r.target_type}
                </Typography.Text>
                <br />
                {renderTarget(r, configNames)}
              </span>
            ),
          },
          {
            title: "内容",
            key: "detail",
            render: (_, r) => renderDetail(r),
          },
          { title: "原因", dataIndex: "reason", ellipsis: true, width: 180 },
        ]}
       scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
      <PaintPagination current={page} pageSize={pageSize} total={total} onChange={onPageChange} />
    </>
  );
}
