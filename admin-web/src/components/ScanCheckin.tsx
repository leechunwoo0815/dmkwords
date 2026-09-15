// 门店扫码签到面板（PRD §9.2.1，2026-09-15）：二维影像枪 HID 键盘模式连扫。
// 设计要点（每条都对应一个真实坑，别省）：
//  1. 提交时从 DOM 读值，不走受控 state——扫码枪整串十几毫秒打完，受控输入会丢字符；
//  2. 回车与扫码枪的结尾回车走同一条提交路径（onPressEnter）；
//  3. in-flight 锁（busyRef）——枪可能连发回车，重复提交会写出重复审计；
//  4. 成功与失败**都**回焦——只在成功回焦，一次失败就打断整队人的连扫；
//  5. 就绪标识（聚焦=绿）——焦点被别处抢走要一眼可见；
//  6. 四类失败各有醒目标识，绝不静默；
//  7. 最近签到列表=多人同时到店时的对账凭据；
//  8. 提示音可静默降级（门店嘈杂；浏览器要求先有用户手势才允许出声）。
import { useCallback, useEffect, useRef, useState } from "react";
import { App as AntdApp, Button, Card, Input, Space, Tag, Typography } from "antd";
import type { InputRef } from "antd";
import { ScanOutlined } from "@ant-design/icons";

import { apiSignin } from "../api/activities";

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

// 本地时分秒：对账列表要跟墙上时钟一致（toISOString 是 UTC，门店会看错 8 小时）
function nowHms(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// 清空输入框。直接 `el.value = ""` 不够：AntD Input 内部还留着 React 侧的值，
// 下一次重渲染会把旧值写回 DOM——券码留在框里，下一枪就会**接着后面追加**成
// 一串废码（连扫直接失效）。所以走原生 setter + 派发冒泡 input 事件，让 React
// 的 value tracker 同步到空串。（提交读值仍读 DOM，不依赖这里。）
function clearNativeInput(el: HTMLInputElement | null | undefined): void {
  if (!el) return;
  try {
    const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el) as object, "value");
    if (desc && desc.set) desc.set.call(el, "");
    else el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  } catch {
    el.value = "";
  }
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
  open, activityTitle, onSignedIn,
}: {
  open: boolean;
  activityTitle?: string;
  onSignedIn?: () => void;
}) {
  const { message } = AntdApp.useApp();
  const inputRef = useRef<InputRef>(null);
  const busyRef = useRef(false);
  const audioRef = useRef<AudioContext | null>(null);
  const seqRef = useRef(0);
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);
  const [recent, setRecent] = useState<RecentRow[]>([]);

  const focusInput = useCallback(() => {
    inputRef.current?.focus();
  }, []);

  // 只在自己人手里时抢焦点：用户已经点进别的输入框就不夺（避免"很智能地"打断手工输入）
  const refocusIfIdle = useCallback(() => {
    const el = document.activeElement as HTMLElement | null;
    const tag = el?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    focusInput();
  }, [focusInput]);

  // 抽屉打开即自动聚焦——馆员不需要先点输入框。
  // 重试三次而不是一次：①Drawer 挂载/动画有延迟；②从"活动详情抽屉"点报名名单过来时，
  // 前一个抽屉关闭会做焦点回填（AntD 的 focus restore），一次性的 focus 会被它盖掉。
  useEffect(() => {
    if (!open) return;
    setRecent([]);
    const timers = [120, 400, 800].map((ms) => setTimeout(refocusIfIdle, ms));
    return () => timers.forEach(clearTimeout);
  }, [open, refocusIfIdle]);

  // 提示音：成功高、失败低；首次需用户手势，失败静默降级
  const beep = useCallback((ok: boolean) => {
    try {
      const Ctx =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      if (!audioRef.current) audioRef.current = new Ctx();
      const ac = audioRef.current;
      if (ac.state === "suspended") void ac.resume();
      const t0 = ac.currentTime;
      const freqs = ok ? [880, 1320] : [320, 220];
      freqs.forEach((f, i) => {
        const osc = ac.createOscillator();
        const gain = ac.createGain();
        osc.type = "sine";
        osc.frequency.value = f;
        gain.gain.value = 0.06;
        osc.connect(gain);
        gain.connect(ac.destination);
        osc.start(t0 + i * 0.09);
        osc.stop(t0 + i * 0.09 + 0.08);
      });
    } catch {
      /* 静默降级：门店嘈杂，出声只是加分项 */
    }
  }, []);

  const submit = useCallback(async () => {
    if (busyRef.current) return;
    // 从 DOM 读原始值（非受控）——扫码枪极速输入下受控 onChange 会丢字符
    const code = (inputRef.current?.input?.value ?? "").trim().toUpperCase();
    if (!code) {
      focusInput();
      return;
    }
    busyRef.current = true;
    setBusy(true);
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
      beep(true);
      seqRef.current += 1;
      setRecent((prev) => [
        {
          seq: seqRef.current,
          code: r.ticket_code || code,
          label,
          time: fmtTime(r.checked_in_at).slice(11),
          ok: true,
          text: mismatch ? "签到成功（非本场）" : "签到成功",
        },
        ...prev,
      ].slice(0, RECENT_MAX));
      onSignedIn?.();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "签到失败";
      const { level, icon } = classify(msg);
      const show = level === "warning" ? message.warning : message.error;
      show({ content: `${icon} ${msg}`, duration: 3 }); // 绝不静默
      beep(false);
      seqRef.current += 1;
      setRecent((prev) => [
        { seq: seqRef.current, code, label: "—", time: nowHms(), ok: false, text: msg },
        ...prev,
      ].slice(0, RECENT_MAX));
    } finally {
      busyRef.current = false;
      setBusy(false);
      clearNativeInput(inputRef.current?.input);
      // 成功与失败都回焦：只在成功回焦会让一次失败打断整队人的连扫
      setTimeout(focusInput, 0);
    }
  }, [activityTitle, beep, focusInput, message, onSignedIn]);

  return (
    <Card
      size="small"
      style={{ marginBottom: 12, borderColor: focused ? "var(--paint-secondary)" : undefined }}
      styles={{ body: { padding: 12 } }}
    >
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Space wrap>
          <Typography.Text strong>
            <ScanOutlined /> 扫码签到
          </Typography.Text>
          <Tag color={focused ? "green" : "orange"}>
            {focused ? "● 等待扫码…" : "○ 未聚焦（点输入框）"}
          </Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            扫码枪扫家长出示的二维码/条形码即自动提交；也可手工输入券码后回车
          </Typography.Text>
        </Space>
        <Space.Compact style={{ width: "100%" }}>
          <Input
            ref={inputRef}
            size="large"
            placeholder="扫码枪扫码，或手工输入入场券码后回车"
            prefix={<ScanOutlined />}
            onPressEnter={() => void submit()}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            autoComplete="off"
          />
          <Button size="large" type="primary" loading={busy} onClick={() => void submit()}>
            签到
          </Button>
        </Space.Compact>
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
