import { Image } from "antd";
import { useCallback, useRef, useState, type CSSProperties } from "react";

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
 */

const BLANK_PX =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

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
      preview={{ mask: "预览" }}
    />
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
  return (
    <Image
      src={url ?? BLANK_PX}
      // 只借它的 Preview 门户（rc-image 的 Preview 渲染在 Fragment 兄弟位、并 Portal
      // 到 body，不受这里 display:none 影响）
      style={{ display: "none" }}
      preview={{
        visible: !!url,
        src: url ?? undefined,
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
    closeCbRef.current = onClose ?? null;
    setUrl(next);
  }, []);

  return { open, close, node: <ImageViewerHost url={url} onClose={close} /> };
}
