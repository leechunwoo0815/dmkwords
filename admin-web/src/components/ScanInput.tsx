// 通用扫码输入框（2026-09-21 任务包 C 批，红线 R5：**禁止写第二份扫码实现**）
// 机制从已验收的「活动扫码签到面板」(`ScanCheckin.tsx`) 原样提取，八条设计要点逐条保留：
//  1. 提交时从 DOM 读值（非受控）——扫码枪十几毫秒打完，受控输入会丢字符；
//  2. 回车与枪尾回车走同一条提交路径（onPressEnter）；
//  3. in-flight 锁（busyRef）——枪可能连发回车，重复提交会写重复审计/重复借还；
//  4. **成功与失败都回焦**——只在成功回焦，一次失败就打断整队人的连扫；
//  5. 就绪标识（聚焦=绿）——焦点被别处抢走要一眼可见；
//  6. 提示音成功高/失败低，可静默降级（浏览器要求先有用户手势才允许出声）；
//  7. 打开/切换即自动聚焦（多次重试，应对抽屉动画与 AntD focus restore）；
//  8. 并发/重入安全：清空走原生 setter + input 冒泡（否则 React value tracker 会把旧值写回）。
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Button, Input, Space, Tag, Typography } from "antd";
import type { InputRef } from "antd";
import { ScanOutlined } from "@ant-design/icons";

export interface ScanHandle {
  focus: () => void;
}

interface Props {
  placeholder: string;
  /** 业务提交；返回 true=成功、false=失败（决定提示音高低）。父组件负责自己的提示与状态。 */
  onScan: (code: string) => Promise<boolean>;
  actionLabel?: string;
  hint?: React.ReactNode;
  disabled?: boolean;
  /** 打开/切换时自动聚焦的重试序列（ms）；默认一次立即聚焦 */
  focusDelays?: number[];
  /** 该值变化时按 focusDelays 重新聚焦（例：抽屉 open、切换"当前读者"） */
  refocusKey?: unknown;
  /** 聚焦态上报（父组件用来画卡片边框/高亮） */
  onFocusChange?: (focused: boolean) => void;
  /** 是否显示"● 等待扫码/○ 未聚焦"标识（默认显示） */
  showIndicator?: boolean;
  /** 提交后是否把焦点收回到本框（默认 true）。
   *  焦点链场景要传 false：否则它会在 finally 里把父组件刚交棒出去的焦点**抢回来**
   *  （2026-09-21 用户实测：扫完会员码光标仍停在会员码框，就是这一条没关）。 */
  refocusAfterSubmit?: boolean;
}

/** 清空输入框：直接赋 value 不够——AntD Input 内部还留着 React 侧的值，下次重渲染会写回，
 *  下一枪就接着旧值追加成一串废码（连扫直接失效）。故走原生 setter + 派发冒泡 input 事件。 */
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

const ScanInput = forwardRef<ScanHandle, Props>(function ScanInput(
  {
    placeholder,
    onScan,
    actionLabel = "提交",
    hint,
    disabled,
    focusDelays = [120, 400, 800],
    refocusKey,
    onFocusChange,
    showIndicator = true,
    refocusAfterSubmit = true,
  },
  ref,
) {
  const inputRef = useRef<InputRef>(null);
  const busyRef = useRef(false);
  const audioRef = useRef<AudioContext | null>(null);
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);

  const focusInput = useCallback(() => inputRef.current?.focus(), []);
  useImperativeHandle(ref, () => ({ focus: focusInput }), [focusInput]);

  // 只在自己人手里时抢焦点：用户已点进别的输入框就不夺（避免"很智能地"打断手工输入）
  const refocusIfIdle = useCallback(() => {
    const el = document.activeElement as HTMLElement | null;
    const tag = el?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    focusInput();
  }, [focusInput]);

  useEffect(() => {
    if (disabled) return;
    const timers = focusDelays.map((ms) => setTimeout(refocusIfIdle, ms));
    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled, refocusKey, refocusIfIdle]);

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
      /* 静默降级：出声只是加分项 */
    }
  }, []);

  const submit = useCallback(async () => {
    if (busyRef.current || disabled) return;
    const code = (inputRef.current?.input?.value ?? "").trim().toUpperCase();
    if (!code) {
      focusInput();
      return;
    }
    busyRef.current = true;
    setBusy(true);
    let ok = false;
    try {
      ok = await onScan(code);
    } catch {
      ok = false; // 父组件负责报错文案；这里只决定提示音
    } finally {
      beep(ok);
      busyRef.current = false;
      setBusy(false);
      clearNativeInput(inputRef.current?.input);
      // 成功与失败都回焦：只在成功回焦会让一次失败打断整队人的连扫。
      // refocusAfterSubmit=false 时交给父组件（焦点链要交棒到下一个框）。
      if (refocusAfterSubmit) setTimeout(focusInput, 0);
    }
  }, [beep, disabled, focusInput, onScan, refocusAfterSubmit]);

  return (
    <Space direction="vertical" size={6} style={{ width: "100%" }}>
      <Space wrap>
        {showIndicator && (
          // 2026-09-21：未聚焦从"橙色告警"降级为灰标签——两个框各挂一条橙标太吵（用户反馈"眼晕"），
          // 焦点状态本身仍是绿的可见信号（标杆组的第 5 条机制不受影响）。
          <Tag color={focused ? "green" : "default"}>
            {focused ? "● 等待扫码…" : "○ 未聚焦"}
          </Tag>
        )}
        {hint && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {hint}
          </Typography.Text>
        )}
      </Space>
      <Space.Compact style={{ width: "100%" }}>
        <Input
          ref={inputRef}
          size="large"
          placeholder={placeholder}
          prefix={<ScanOutlined />}
          onPressEnter={() => void submit()}
          onFocus={() => {
            setFocused(true);
            onFocusChange?.(true);
          }}
          onBlur={() => {
            setFocused(false);
            onFocusChange?.(false);
          }}
          disabled={disabled}
          autoComplete="off"
        />
        <Button
          size="large"
          type="primary"
          loading={busy}
          disabled={disabled}
          onClick={() => void submit()}
        >
          {actionLabel}
        </Button>
      </Space.Compact>
    </Space>
  );
});

export default ScanInput;
