import { Image } from "antd";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

/**
 * 全后台统一的图片 / 凭证预览（2026-09-15 用户裁定）。
 *
 * 基准效果 = 图书详情「封面」预览：缩略图（圆角 6 + 1px 描边 + 纸色底）→ 点击全屏预览
 * （antd Image preview：缩放 / 旋转 / 翻转 / Esc 关闭，**没有窗口壳**）。
 * 禁止再用 `<Modal><img/></Modal>` 那种「带窗口的预览」——图被压在 640px 弹窗里又小
 * 又放不大，看收款凭证根本没法用（用户原话：「很不好用」）。
 *
 * 两种用法：
 *  1) 缩略图 + 点开（封面 / 成就卡 / 报告图）：
 *       <PreviewImage src={url} width={72} height={100} emptyText="未上传" />
 *  2) 已经有按钮 / 图标当入口（查看凭证、上传前本地预览），命令式打开：
 *       const viewer = useImageViewer();
 *       <Button onClick={() => viewer.open(url)}>查看凭证</Button>
 *       {viewer.node}
 *     Blob URL 场景把回收挂在关闭回调上：
 *       viewer.open(url, () => URL.revokeObjectURL(url))
 *
 * ⚠️ 2026-09-17 用户报障「封面预览又小又糊、每次刷新换一本」——根因见 useNaturalSize 注释：
 * 预览大图的布局尺寸依赖**图片解码出的原始尺寸**，挂载瞬间没解码完时 Safari 会按错尺寸
 * 栅格化并卡住。故预览必须**显式带上原图 width/height**（同一 URL 预加载，走缓存，零额外请求）。
 */

const BLANK_PX =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

/**
 * 取图片原始像素尺寸（同一 URL 预加载 → 命中浏览器缓存，不发多余请求）。
 *
 * [Why 必须有] antd 预览大图 `.ant-image-preview-img` 只给了 `max-width/max-height`，
 * **没有显式 width/height**，所以它的布局尺寸完全由"解码出的 intrinsic size"决定。
 * 预览刚挂载、图还没解码完时 intrinsic size 为 0（或退化成占位尺寸），Safari 会按这个
 * 错误尺寸做一次栅格化并**保持不回刷**（Chromium 会重栅格化，所以我这边一直看不到问题），
 * 表现为"预览又小又糊"，且每次刷新轮到哪张图就哪张坏（竞态，无规律）。
 * 把真实尺寸作为 width/height 属性交给预览 img，初始布局即正确，竞态不成立。
 */
function useNaturalSize(src?: string | null) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    setSize(null);
    if (!src) return;
    let alive = true;
    const probe = new window.Image();
    const apply = () => {
      if (alive && probe.naturalWidth > 0) {
        setSize({ w: probe.naturalWidth, h: probe.naturalHeight });
      }
    };
    probe.onload = apply;
    probe.src = src;
    if (probe.complete) apply(); // 已在缓存里：同步拿到
    return () => {
      alive = false;
    };
  }, [src]);
  return size;
}

type Props = {
  src?: string | null;
  alt?: string;
  /** 数字 = 固定缩略图宽；百分比 = 撑满父容器 */
  width?: number | string;
  /** 给了 = 固定高度画框（缩略图）；不给 = 按原图比例自适应（报告长图） */
  height?: number | string;
  /** cover 用于封面/凭证等有裁切的场景；contain 用于报告长图 */
  fit?: "cover" | "contain";
  radius?: number;
  /** 无图时的占位文案；不传则整块不渲染（由调用方决定是否显示占位） */
  emptyText?: string;
  style?: CSSProperties;
};

export default function PreviewImage({
  src,
  alt = "",
  width = 72,
  height,
  fit = "cover",
  radius = 6,
  emptyText,
  style,
}: Props) {
  // height 给了 = 固定画框（缩略图）；不给 = 按原图比例撑满 width（长图报告）
  const hasHeight = height !== undefined && height !== null;
  const natural = useNaturalSize(src);
  const wrapRef = useRef<HTMLDivElement>(null);

  /**
   * 预览入场动画的"起飞点"：把缩略图相对屏幕中心的偏移写进根元素 CSS 变量，
   * 预览大图（`paint.css` 的 `pv-fly-in`）据此从缩略图那一侧缩小态飞入居中。
   *
   * 为什么自己做动画：antd 自带的 zoom 会在**动画前先按最终位置画约 60ms**（rc-motion 的
   * appear class 晚一帧才加），用户看到的是"先闪一下最终态、再跳回起点飞过来"（2026-09-17 报障）。
   * 自己写在 img 上的 CSS 动画从**第一帧**就是起始态，不会再出现预滚帧。
   * 关掉 antd 动画用 `preview.transitionName = ""`（rc-motion 见假值即不启用）。
   */
  const rememberFlyOrigin = useCallback((e: React.MouseEvent) => {
    const el = wrapRef.current?.querySelector("img");
    if (!el || typeof document === "undefined") return;
    const r = el.getBoundingClientRect();
    const dx = Math.round(r.left + r.width / 2 - window.innerWidth / 2);
    const dy = Math.round(r.top + r.height / 2 - window.innerHeight / 2);
    const root = document.documentElement.style;
    root.setProperty("--pv-dx", `${dx}px`);
    root.setProperty("--pv-dy", `${dy}px`);
    // 起始缩放：按缩略图宽度相对大图（原图按 82vh 上限估）折算，让"起飞尺寸≈缩略图"
    const shown = natural ? Math.min(natural.h, window.innerHeight * 0.82) : window.innerHeight * 0.5;
    const ratio = natural ? Math.max(0.12, Math.min(0.6, (r.width * (natural.h / natural.w)) / shown)) : 0.3;
    root.setProperty("--pv-scale", ratio.toFixed(3));
    void e;
  }, [natural]);

  if (!src) {
    if (!emptyText) return null;
    return (
      <div
        style={{
          width: typeof width === "number" ? width : "100%",
          height: hasHeight ? height : undefined,
          minHeight: hasHeight ? undefined : 96,
          borderRadius: radius,
          border: "1px dashed var(--paint-border)",
          background: "var(--paint-paper-dim)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          color: "var(--paint-ink-light)",
          fontSize: 12,
          ...style,
        }}
      >
        {emptyText}
      </div>
    );
  }

  return (
    <div
      ref={wrapRef}
      style={{ display: "contents" }}
      // 记录点击位置（用 capture，抢在 rc-image 打开预览之前写 CSS 变量）：
      // 我们的入场动画让大图从缩略图那一侧飞入，方向由这里算出的位移决定。
      onClickCapture={rememberFlyOrigin}
    >
      <Image
        src={src}
        alt={alt}
        width={width}
        height={height}
        // rc-image 把 style 落到 <img>（遮罩层另读 display 决定隐藏），所以宽高都拉满
        // 外层容器；没给 height 的场景才回落到原图比例。
        style={{
          width: "100%",
          height: hasHeight ? "100%" : "auto",
          objectFit: fit,
          borderRadius: radius,
          border: "1px solid var(--paint-border)",
          background: "var(--paint-paper-dim)",
          display: "block",
          cursor: "zoom-in",
          ...style,
        }}
        // 悬停遮罩文案统一为「预览」（与图书详情封面同款；给字符串会顶掉 antd 默认的眼睛图标）
        // width/height = **原图真实尺寸**（不是缩略图画框）：rc-image 会把它落到预览大图的
        // img 上，使预览一挂载就有正确布局，消除"Safari 按未解码尺寸栅格化并卡住"的竞态。
        // transitionName: "" = 关掉 antd 自带 zoom 动画，入场动画改由 paint.css 的 `pv-fly-in`
        // 自己做（原因见那里的注释：antd 的动画前有约 60ms「先画最终态」预滚 → 看着像闪一下）。
        preview={{
          mask: "预览",
          width: natural?.w,
          height: natural?.h,
          transitionName: "",
        }}
      />
    </div>
  );
}

/** 命令式全屏预览宿主：把已存在的按钮 / 图标接到统一预览上 */
export function ImageViewerHost({
  url,
  onClose,
}: {
  url: string | null;
  onClose: () => void;
}) {
  // 同 PreviewImage：预览必须带原图真实尺寸，否则 cold 打开时 Safari 会按未解码尺寸卡住
  const natural = useNaturalSize(url);
  return (
    <Image
      src={url ?? BLANK_PX}
      // 只借它的 Preview 门户（rc-image 的 Preview 渲染在 Fragment 兄弟位、并 Portal
      // 到 body，不受这里 display:none 影响）
      style={{ display: "none" }}
      preview={{
        visible: !!url,
        src: url ?? undefined,
        width: natural?.w,
        height: natural?.h,
        onVisibleChange: (v) => {
          if (!v) onClose();
        },
      }}
    />
  );
}

export function useImageViewer() {
  const [url, setUrl] = useState<string | null>(null);
  const closeCbRef = useRef<(() => void) | null>(null);

  const close = useCallback(() => {
    setUrl(null);
    const cb = closeCbRef.current;
    closeCbRef.current = null;
    cb?.();
  }, []);

  const open = useCallback((next?: string | null, onClose?: () => void) => {
    if (!next) return;
    // 命令式预览没有"点击点"：把飞入起点复位到屏幕中心、小幅缩放（轻微放大感），
    // 否则会沿用上一次封面点击留下的 --pv-dx/dy，图从无关位置飞进来。
    if (typeof document !== "undefined") {
      const root = document.documentElement.style;
      root.setProperty("--pv-dx", "0px");
      root.setProperty("--pv-dy", "0px");
      root.setProperty("--pv-scale", "0.88");
    }
    closeCbRef.current = onClose ?? null;
    setUrl(next);
  }, []);

  return { open, close, node: <ImageViewerHost url={url} onClose={close} /> };
}
