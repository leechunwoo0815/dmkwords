import React, { useEffect, useRef, useState } from "react";

interface PaintHScrollbarProps {
  target?: HTMLElement | null;
  /** auto：自动发现本页 .ant-table-content（列表页统一接入用，免逐页手写 ref 探测） */
  auto?: boolean;
  className?: string;
}

export const PaintHScrollbar: React.FC<PaintHScrollbarProps> = ({ target, auto, className }) => {
  const trackRef = useRef<HTMLDivElement>(null);
  const [autoTarget, setAutoTarget] = useState<HTMLElement | null>(null);
  const thumbRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [thumbWidth, setThumbWidth] = useState(0);
  const [thumbLeft, setThumbLeft] = useState(0);

  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startScrollLeftRef = useRef(0);

  // auto 模式：立即/RAF/500ms 三次探测 + MO 防 tab 切换重渲染（BookManage 先例同款）
  useEffect(() => {
    if (!auto) return;
    const find = () => {
      let node: HTMLElement | null = trackRef.current;
      while (node && node !== document.body) {
        node = node.parentElement;
        const el = node?.querySelector<HTMLElement>(".ant-table-content");
        if (el) {
          setAutoTarget((prev) => (prev === el ? prev : el));
          return;
        }
      }
    };
    find();
    const id = requestAnimationFrame(find);
    const timer = setTimeout(find, 500);
    const mo = new MutationObserver(find);
    if (trackRef.current?.parentElement) {
      mo.observe(trackRef.current.parentElement, { childList: true, subtree: true });
    }
    return () => {
      cancelAnimationFrame(id);
      clearTimeout(timer);
      mo.disconnect();
    };
  }, [auto]);

  const scrollTarget = target ?? autoTarget;

  useEffect(() => {
    if (!scrollTarget) return;

    const update = () => {
      const { scrollWidth, clientWidth, scrollLeft } = scrollTarget;
      const overflow = scrollWidth > clientWidth;
      setVisible(overflow);
      if (!overflow) return;

      const trackW = trackRef.current?.clientWidth || clientWidth;
      const ratio = clientWidth / scrollWidth;
      const thumbW = Math.max(30, trackW * ratio);
      const maxScroll = scrollWidth - clientWidth;
      const maxThumbLeft = trackW - thumbW;
      const left = maxScroll <= 0 ? 0 : (scrollLeft / maxScroll) * maxThumbLeft;
      setThumbWidth(thumbW);
      setThumbLeft(left);
    };

    update();
    scrollTarget.addEventListener("scroll", update, { passive: true });

    let ro: ResizeObserver | null = null;
    if ("ResizeObserver" in window) {
      ro = new ResizeObserver(update);
      ro.observe(scrollTarget);
    }

    window.addEventListener("resize", update);

    return () => {
      scrollTarget.removeEventListener("scroll", update);
      ro?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [scrollTarget]);

  useEffect(() => {
    const thumb = thumbRef.current;
    if (!thumb || !scrollTarget) return;

    const onPointerDown = (e: PointerEvent) => {
      e.preventDefault();
      draggingRef.current = true;
      startXRef.current = e.clientX;
      startScrollLeftRef.current = scrollTarget.scrollLeft;
      thumb.setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!draggingRef.current || !trackRef.current) return;
      const trackW = trackRef.current.clientWidth;
      const deltaX = e.clientX - startXRef.current;
      const maxScroll = scrollTarget.scrollWidth - scrollTarget.clientWidth;
      const maxThumbLeft = trackW - thumbWidth;
      if (maxThumbLeft <= 0) return;
      const ratio = deltaX / maxThumbLeft;
      scrollTarget.scrollLeft = startScrollLeftRef.current + ratio * maxScroll;
    };

    const onPointerUp = (e: PointerEvent) => {
      draggingRef.current = false;
      thumb.releasePointerCapture(e.pointerId);
    };

    thumb.addEventListener("pointerdown", onPointerDown);
    thumb.addEventListener("pointermove", onPointerMove);
    thumb.addEventListener("pointerup", onPointerUp);

    return () => {
      thumb.removeEventListener("pointerdown", onPointerDown);
      thumb.removeEventListener("pointermove", onPointerMove);
      thumb.removeEventListener("pointerup", onPointerUp);
    };
  }, [target, thumbWidth]);

  const onTrackClick = (e: React.MouseEvent) => {
    if (!scrollTarget || !trackRef.current || e.target === thumbRef.current) return;
    const trackRect = trackRef.current.getBoundingClientRect();
    const clickX = e.clientX - trackRect.left;
    const maxScroll = scrollTarget.scrollWidth - scrollTarget.clientWidth;
    const maxThumbLeft = trackRef.current.clientWidth - thumbWidth;
    const desiredScrollLeft = (clickX / maxThumbLeft) * maxScroll;
    scrollTarget.scrollTo({ left: desiredScrollLeft, behavior: "smooth" });
  };

  if (!visible) return null;

  return (
    <div
      ref={trackRef}
      className={`paint-hscrollbar-track ${className || ""}`}
      onClick={onTrackClick}
      style={{
        width: "100%",
        height: 24,
        background: "var(--paint-ink-light)",
        border: "2px solid var(--paint-ink)",
        borderRadius: 10,
        marginTop: 8,
        position: "relative",
        cursor: "pointer",
        flexShrink: 0,
      }}
    >
      <div
        ref={thumbRef}
        style={{
          position: "absolute",
          left: thumbLeft,
          width: thumbWidth,
          height: 16,
          top: 2,
          background: "var(--paint-paper)",
          border: "1px solid var(--paint-ink)",
          borderRadius: 8,
          cursor: "grab",
        }}
      />
    </div>
  );
};
