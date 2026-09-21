// 门店扫码签到面板（PRD §9.2.1，2026-09-15）：二维影像枪 HID 键盘模式连扫。
// 2026-09-21：输入框与焦点/防重/提示音机制**抽到通用件 `ScanInput`**（任务包 C 批红线 R5
// "禁止写第二份扫码实现"）——本文件只保留签到业务：调用接口、分级提示、最近签到列表。
import { useCallback, useEffect, useRef, useState } from "react";
import { App as AntdApp, Button, Card, Space, Tag, Typography } from "antd";
import { ScanOutlined } from "@ant-design/icons";

import { apiSignin } from "../api/activities";
import ScanInput from "./ScanInput";

const RECENT_MAX = 20;

interface RecentRow {
  seq: number;
  code: string;
  label: string; // 姓名 · 活动
  time: string;
  ok: boolean;
  text: string;
}

function fmtTime(iso: string): string {
  return iso ? iso.replace("T", " ").slice(0, 19) : "—";
}

// 失败四类：券码不存在 / 已签到过 / 报名状态不可签 / 活动已取消或结束。
// 后端文案已区分（NotFoundError/ConflictError/ValidationError），这里只做视觉分级。
function classify(msg: string): { level: "warning" | "error"; icon: string } {
  if (msg.includes("已签到过")) return { level: "warning", icon: "⚠" };
  if (msg.includes("不存在")) return { level: "error", icon: "✕" };
  if (msg.includes("取消") || msg.includes("结束")) return { level: "error", icon: "✕" };
  return { level: "error", icon: "✕" };
}

export default function ScanCheckin({
  open,
  activityTitle,
  onSignedIn,
}: {
  open: boolean;
  activityTitle?: string;
  onSignedIn?: () => void;
}) {
  const { message } = AntdApp.useApp();
  const seqRef = useRef(0);
  const [focused, setFocused] = useState(false);
  const [recent, setRecent] = useState<RecentRow[]>([]);

  // 每次打开清空上一次的名单（对账列表只反映本轮；原实现写在"打开即聚焦"的副作用里）
  useEffect(() => {
    if (open) setRecent([]);
  }, [open]);

  const submit = useCallback(
    async (code: string): Promise<boolean> => {
      try {
        const r = await apiSignin(code);
        const label = `${r.child_name ?? "（未知孩子）"} · ${r.activity_title}`;
        const mismatch = activityTitle && r.activity_title !== activityTitle;
        message.success({
          content: mismatch
            ? `✅ ${label} 签到成功（${fmtTime(r.checked_in_at)}）⚠ 该券不属于本场活动`
            : `✅ ${label} 签到成功（${fmtTime(r.checked_in_at)}）`,
          duration: 3,
        });
        seqRef.current += 1;
        setRecent((prev) =>
          [
            {
              seq: seqRef.current,
              code: r.ticket_code || code,
              label,
              time: fmtTime(r.checked_in_at).slice(11),
              ok: true,
              text: mismatch ? "签到成功（非本场）" : "签到成功",
            },
            ...prev,
          ].slice(0, RECENT_MAX),
        );
        onSignedIn?.();
        return true;
      } catch (e) {
        const msg = e instanceof Error ? e.message : "签到失败";
        const { level, icon } = classify(msg);
        const show = level === "warning" ? message.warning : message.error;
        show({ content: `${icon} ${msg}`, duration: 3 }); // 绝不静默
        seqRef.current += 1;
        setRecent((prev) =>
          [
            { seq: seqRef.current, code, label: "—", time: new Date().toLocaleTimeString(), ok: false, text: msg },
            ...prev,
          ].slice(0, RECENT_MAX),
        );
        return false;
      }
    },
    [activityTitle, message, onSignedIn],
  );

  return (
    <Card
      size="small"
      style={{ marginBottom: 12, borderColor: focused ? "var(--paint-secondary)" : undefined }}
      styles={{ body: { padding: 12 } }}
    >
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text strong>
          <ScanOutlined /> 扫码签到
        </Typography.Text>
        <ScanInput
          refocusKey={open}
          onScan={submit}
          onFocusChange={setFocused}
          placeholder="扫码枪扫码，或手工输入入场券码后回车"
          actionLabel="签到"
          hint="扫码枪扫家长出示的二维码/条形码即自动提交；也可手工输入券码后回车"
        />
        {recent.length > 0 && (
          <div>
            <Space style={{ marginBottom: 4 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                最近签到（{recent.length}）
              </Typography.Text>
              <Button type="link" size="small" onClick={() => setRecent([])}>
                清空
              </Button>
            </Space>
            <div style={{ maxHeight: 160, overflowY: "auto" }}>
              {recent.map((r) => (
                <Space key={r.seq} size={8} style={{ display: "flex", fontSize: 12, lineHeight: "20px" }}>
                  <Typography.Text type="secondary">{r.time}</Typography.Text>
                  <Typography.Text code style={{ fontSize: 12 }}>
                    {r.code}
                  </Typography.Text>
                  <span>{r.label}</span>
                  <Tag color={r.ok ? "green" : "red"} style={{ marginRight: 0 }}>
                    {r.text}
                  </Tag>
                </Space>
              ))}
            </div>
          </div>
        )}
      </Space>
    </Card>
  );
}
