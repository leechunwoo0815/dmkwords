import React, { useEffect, useRef, useState } from "react";

interface PaintHScrollbarProps {
  target: HTMLElement | null;
  className?: string;
}

export const PaintHScrollbar: React.FC<PaintHScrollbarProps> = ({ target, className }) => {
  const trackRef = useRef<HTMLDivElement>(null);
  const thumbRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [thumbWidth, setThumbWidth] = useState(0);
  const [thumbLeft, setThumbLeft] = useState(0);

  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startScrollLeftRef = useRef(0);

  useEffect(() => {
    if (!target) return;

    const update = () => {
      const { scrollWidth, clientWidth, scrollLeft } = target;
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
    target.addEventListener("scroll", update, { passive: true });

    let ro: ResizeObserver | null = null;
    if ("ResizeObserver" in window) {
      ro = new ResizeObserver(update);
      ro.observe(target);
    }

    window.addEventListener("resize", update);

    return () => {
      target.removeEventListener("scroll", update);
      ro?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [target]);

  useEffect(() => {
    const thumb = thumbRef.current;
    if (!thumb || !target) return;

    const onPointerDown = (e: PointerEvent) => {
      e.preventDefault();
      draggingRef.current = true;
      startXRef.current = e.clientX;
      startScrollLeftRef.current = target.scrollLeft;
      thumb.setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!draggingRef.current || !trackRef.current) return;
      const trackW = trackRef.current.clientWidth;
      const deltaX = e.clientX - startXRef.current;
      const maxScroll = target.scrollWidth - target.clientWidth;
      const maxThumbLeft = trackW - thumbWidth;
      if (maxThumbLeft <= 0) return;
      const ratio = deltaX / maxThumbLeft;
      target.scrollLeft = startScrollLeftRef.current + ratio * maxScroll;
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
    if (!target || !trackRef.current || e.target === thumbRef.current) return;
    const trackRect = trackRef.current.getBoundingClientRect();
    const clickX = e.clientX - trackRect.left;
    const maxScroll = target.scrollWidth - target.clientWidth;
    const maxThumbLeft = trackRef.current.clientWidth - thumbWidth;
    const desiredScrollLeft = (clickX / maxThumbLeft) * maxScroll;
    target.scrollTo({ left: desiredScrollLeft, behavior: "smooth" });
  };

  if (!visible) return null;

  return (
    <div
      ref={trackRef}
      className={`paint-hscrollbar-track ${className || ""}`}
      onClick={onTrackClick}
      style={{
        width: "100%",
        height: 12,
        background: "var(--paint-ink-light)",
        border: "2px solid var(--paint-ink)",
        borderRadius: 8,
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
          height: 8,
          top: 0,
          background: "var(--paint-paper)",
          border: "1px solid var(--paint-ink)",
          borderRadius: 4,
          cursor: "grab",
        }}
      />
    </div>
  );
};
