/**
 * TechIcon —— 自绘「科技感」SVG 图标集（第一百六十四批）
 *
 * 为什么不用 antd 自带图标：那套是办公软件的通用轮廓，放在看板上辨识度和气质都偏弱。
 * 这里按一套统一语言手绘：
 *  · 24×24 视框、1.5px 细线、圆角端点/连接（技术图的"细线"感）
 *  · 每个图标带一处**主色实心小元素**（方/点/条），作为识别锚点，也自然继承主题色
 *  · 只用 `currentColor` 与 `--color-primary`，换主题自动跟随（含绘本风/深色）
 *
 * 用法：`<TechIcon name="dashboard" size={18} />`；颜色取父级 color，主色锚点取 `--color-primary`。
 */
const P = "var(--color-primary)";

type IconName =
  | "dashboard"
  | "book"
  | "member"
  | "wallet"
  | "borrow"
  | "reserve"
  | "trophy"
  | "calendar"
  | "refund"
  | "circle"
  | "bell"
  | "task"
  | "staff"
  | "config"
  | "audit"
  | "empty"
  | "trend"
  | "gauge"
  | "pie"
  | "rank"
  | "group";

function paths(name: IconName) {
  switch (name) {
    case "dashboard":
      return (
        <>
          <rect x="3" y="3" width="7.5" height="7.5" rx="1.8" />
          <rect x="13.5" y="3" width="7.5" height="4.5" rx="1.8" />
          <rect x="13.5" y="10.5" width="7.5" height="10.5" rx="1.8" />
          <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.8" fill={P} stroke="none" opacity="0.85" />
        </>
      );
    case "book":
      return (
        <>
          <path d="M4 5.2A1.7 1.7 0 0 1 5.7 3.5h4.1A2.2 2.2 0 0 1 12 5.7V20a2.2 2.2 0 0 0-2.2-2.2H5.7A1.7 1.7 0 0 0 4 19.5z" />
          <path d="M20 5.2a1.7 1.7 0 0 0-1.7-1.7h-4.1A2.2 2.2 0 0 0 12 5.7V20a2.2 2.2 0 0 1 2.2-2.2h4.1a1.7 1.7 0 0 1 1.7 1.7z" />
          <rect x="6.5" y="6.6" width="3" height="1.4" rx="0.7" fill={P} stroke="none" />
          <rect x="14.5" y="6.6" width="3" height="1.4" rx="0.7" fill={P} stroke="none" opacity="0.6" />
        </>
      );
    case "member":
      return (
        <>
          <circle cx="9" cy="8" r="3.2" />
          <path d="M3.4 19.6c0-3.1 2.5-5.6 5.6-5.6s5.6 2.5 5.6 5.6" />
          <path d="M16.2 5.2a3 3 0 0 1 0 5.6" />
          <path d="M17.4 14.4c2 .7 3.4 2.6 3.4 4.9" opacity="0.55" />
          <circle cx="9" cy="8" r="1.1" fill={P} stroke="none" />
        </>
      );
    case "wallet":
      return (
        <>
          <rect x="3" y="6" width="18" height="13" rx="2.2" />
          <path d="M3 10.2h18" />
          <rect x="14.6" y="12.6" width="4.6" height="3.2" rx="1.2" fill={P} stroke="none" />
        </>
      );
    case "borrow":
      return (
        <>
          <path d="M4 8h12.5l-3-3" />
          <path d="M20 16H7.5l3 3" />
          <rect x="16.4" y="4.6" width="3.4" height="3.4" rx="1" fill={P} stroke="none" opacity="0.8" />
        </>
      );
    case "reserve":
      return (
        <>
          <path d="M12 21s6.5-5.2 6.5-10.2A6.5 6.5 0 0 0 5.5 10.8C5.5 15.8 12 21 12 21z" />
          <circle cx="12" cy="10.5" r="2.3" fill={P} stroke="none" />
        </>
      );
    case "trophy":
      return (
        <>
          <path d="M7 4h10v5.2a5 5 0 0 1-10 0z" />
          <path d="M7 5.6H4.6v1.6a3 3 0 0 0 3 3M17 5.6h2.4v1.6a3 3 0 0 1-3 3" opacity="0.6" />
          <path d="M12 14.4V18M8.4 20.4h7.2" />
          <rect x="10.4" y="6.4" width="3.2" height="1.6" rx="0.8" fill={P} stroke="none" />
        </>
      );
    case "calendar":
      return (
        <>
          <rect x="3.4" y="5" width="17.2" height="15.4" rx="2.2" />
          <path d="M3.4 9.6h17.2M8.2 3.4v3.2M15.8 3.4v3.2" />
          <rect x="6.4" y="12.4" width="4" height="3.6" rx="1" fill={P} stroke="none" />
        </>
      );
    case "refund":
      return (
        <>
          <path d="M12 3.2 4.6 6.4v5.4c0 4.3 3 7.7 7.4 9 4.4-1.3 7.4-4.7 7.4-9V6.4z" />
          <path d="M8.8 12.2h5.4M11.6 9.6l2.6 2.6-2.6 2.6" />
          <circle cx="12" cy="7.6" r="1.1" fill={P} stroke="none" />
        </>
      );
    case "circle":
      return (
        <>
          <circle cx="12" cy="12" r="8.4" />
          <path d="M12 6.6l1.7 3.6 3.9.5-2.9 2.7.8 3.9-3.5-1.9-3.5 1.9.8-3.9L6.4 10.7l3.9-.5z" />
          <circle cx="12" cy="12" r="1.2" fill={P} stroke="none" />
        </>
      );
    case "bell":
      return (
        <>
          <path d="M6.4 16.4V11a5.6 5.6 0 0 1 11.2 0v5.4l1.4 2H5z" />
          <path d="M10 20a2.2 2.2 0 0 0 4 0" />
          <circle cx="12" cy="10.4" r="1.2" fill={P} stroke="none" />
        </>
      );
    case "task":
      return (
        <>
          <rect x="3.6" y="4.4" width="16.8" height="15.2" rx="2.2" />
          <path d="M7.4 9.6l1.8 1.8 3-3" />
          <path d="M7.4 15.4h5.4M15.6 15.4h1.6" opacity="0.6" />
          <rect x="14.6" y="8.2" width="3.2" height="1.6" rx="0.8" fill={P} stroke="none" />
        </>
      );
    case "staff":
      return (
        <>
          <circle cx="12" cy="8.2" r="3.4" />
          <path d="M4.8 20.2c0-3.6 3.2-6.4 7.2-6.4s7.2 2.8 7.2 6.4" />
          <path d="M9.4 8.2h5.2" opacity="0.5" />
          <circle cx="12" cy="8.2" r="1.1" fill={P} stroke="none" />
        </>
      );
    case "config":
      return (
        <>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 3.4v2.4M12 18.2v2.4M3.4 12h2.4M18.2 12h2.4M5.9 5.9l1.7 1.7M16.4 16.4l1.7 1.7M18.1 5.9l-1.7 1.7M7.6 16.4l-1.7 1.7" />
          <circle cx="12" cy="12" r="1" fill={P} stroke="none" />
        </>
      );
    case "audit":
      return (
        <>
          <path d="M6.4 3.6h8.2l4 4v12.8a1.8 1.8 0 0 1-1.8 1.8H6.4a1.8 1.8 0 0 1-1.8-1.8V5.4a1.8 1.8 0 0 1 1.8-1.8z" />
          <path d="M14.4 3.8v4.2h4" opacity="0.7" />
          <path d="M8.2 12.6h6.4M8.2 16h4.2" opacity="0.6" />
          <rect x="8.2" y="9" width="2.6" height="1.4" rx="0.7" fill={P} stroke="none" />
        </>
      );
    case "trend":
      return (
        <>
          <path d="M3.6 19.2h16.8" opacity="0.5" />
          <path d="M4.6 15.4l4.2-4.6 3.4 2.6 3.2-4.4 4 3" />
          <circle cx="18.4" cy="12" r="1.8" fill={P} stroke="none" />
        </>
      );
    case "gauge":
      return (
        <>
          <path d="M4 16.6a8.6 8.6 0 1 1 16 0" />
          <path d="M12 16.6l4-5.2" />
          <circle cx="12" cy="16.8" r="1.8" fill={P} stroke="none" />
        </>
      );
    case "pie":
      return (
        <>
          <circle cx="12" cy="12" r="8.4" />
          <path d="M12 3.6V12l6 5.2" />
          <path d="M12 12l6 5.2" opacity="0" />
          <circle cx="12" cy="12" r="1.5" fill={P} stroke="none" />
        </>
      );
    case "rank":
      return (
        <>
          <rect x="3.6" y="12.4" width="4.6" height="8" rx="1.2" />
          <rect x="9.7" y="7.6" width="4.6" height="12.8" rx="1.2" fill={P} stroke="none" opacity="0.85" />
          <rect x="15.8" y="10" width="4.6" height="10.4" rx="1.2" opacity="0.7" />
        </>
      );
    case "group":
      return (
        <>
          <circle cx="8.4" cy="9" r="2.6" />
          <circle cx="15.6" cy="9" r="2.6" opacity="0.7" />
          <path d="M3.6 19.4c0-2.6 2.2-4.4 4.8-4.4s4.8 1.8 4.8 4.4" />
          <path d="M14 15.4c2.4.2 4.4 1.9 4.4 4" opacity="0.7" />
          <circle cx="12" cy="9" r="1" fill={P} stroke="none" />
        </>
      );
    case "empty":
      return (
        <>
          <path d="M4.4 9.2 7 4.4h10l2.6 4.8v9.4a1.9 1.9 0 0 1-1.9 1.9H6.3a1.9 1.9 0 0 1-1.9-1.9z" />
          <path d="M4.4 9.2h15.2" />
          <path d="M9.4 13.4h5.2" opacity="0.55" />
          <rect x="10.6" y="6.2" width="2.8" height="1.4" rx="0.7" fill={P} stroke="none" opacity="0.85" />
        </>
      );
    default:
      return null;
  }
}

export default function TechIcon({
  name,
  size = 18,
  className,
  strokeWidth = 1.5,
}: {
  name: IconName;
  size?: number;
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
    >
      {paths(name)}
    </svg>
  );
}

export type { IconName };
