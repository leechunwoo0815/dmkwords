import type { ThemeTokens } from "./types";

/**
 * `dark` — 大屏深色（2026-11-04 第一百六十四批第三条）
 *
 * 用途：**店里那台电视/投屏**。深底 + 高对比数据色 + 大圆点，远看比浅底清楚，
 * 这才是"科技感"能站得住的地方（浅色后台强行发光会变廉价）。
 *
 * 要点：
 * - 主色比企业风亮一档（#4C8DFF）：深底上原蓝色 #3370FF 会发闷；
 * - 中性色只做 4 级灰度，避免深浅不一的"脏灰"；
 * - 图表色板整体提亮，深底上仍能区分 6 个类别；
 * - 阴影几乎不可见 → 靠 1px 描边和层级底色区分层次（深色下阴影无效）。
 */
export const dark: ThemeTokens = {
  id: "dark",
  scheme: "dark",
  name: "大屏深色",
  description: "深底高对比，给电视/投屏用：主色提亮到 #4C8DFF，图表色板整体提亮，靠描边分层。",
  color: {
    primary: "#4C8DFF",
    primaryHover: "#6BA4FF",
    primaryActive: "#3370FF",
    primarySoft: "#1E3557",
    onPrimary: "#FFFFFF",
    success: "#23C343",
    successSoft: "#12351C",
    warning: "#FF9F45",
    warningSoft: "#3A2A16",
    danger: "#F76560",
    dangerHover: "#FF8580",
    dangerSoft: "#3A1D1D",
    info: "#22D3EE",
    infoSoft: "#103038",
    link: "#6BA4FF",
    bgLayout: "#0F1319",
    bgContainer: "#171C23",
    bgElevated: "#1E242D",
    bgSubtle: "#1F2630",
    bgHover: "#242C37",
    bgSelected: "#1E3557",
    border: "#2A323C",
    borderStrong: "#3C4756",
    divider: "#232A34",
    text: "#E6EAF0",
    textSecondary: "#A8B3C0",
    textTertiary: "#7C8896",
    textQuaternary: "#5C6876",
    textInverse: "#0F1319",
    siderBg: "#141920",
    siderBorder: "#262E38",
    siderText: "#A8B3C0",
    siderTextActive: "#6BA4FF",
    headerBg: "#141920",
    mask: "rgba(0, 0, 0, 0.65)",
    // 深底提亮版图表色板
    chart: ["#4C8DFF", "#23C343", "#FF9F45", "#9B6DFF", "#22D3EE", "#F76560"],
  },
  shape: {
    radiusSM: 4,
    radius: 6,
    radiusLG: 8,
    radiusXL: 12,
    buttonRadius: 6,
    borderWidth: 1,
  },
  shadow: {
    // 深色下阴影基本看不见：真正的层次来自底色与描边
    sm: "0 1px 2px rgba(0, 0, 0, 0.4)",
    base: "0 2px 8px rgba(0, 0, 0, 0.45)",
    lg: "0 6px 20px rgba(0, 0, 0, 0.5)",
    modal: "0 10px 30px rgba(0, 0, 0, 0.6)",
    card: "0 1px 0 rgba(255, 255, 255, 0.03) inset",
  },
  density: {
    controlHeight: 32,
    controlHeightSM: 26,
    controlHeightLG: 38,
    headerHeight: 56,
    siderWidth: 220,
    contentPadding: 16,
    cardPadding: 16,
    tableHeaderHeight: 40,
    tableRowHeight: 44,
    // 大屏观看距离远：字号比浅色主题大一档
    fontSize: 15,
    fontSizeSM: 13,
    fontSizeLG: 17,
    fontWeightStrong: 600,
  },
  font: {
    body: '"Segoe UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Helvetica Neue", Arial, sans-serif',
    heading:
      '"Segoe UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Helvetica Neue", Arial, sans-serif',
    mono: 'ui-monospace, "Cascadia Mono", "SF Mono", Consolas, "Microsoft YaHei", monospace',
    weightRegular: 400,
    weightMedium: 500,
    weightStrong: 600,
    letterSpacingHeading: "0",
  },
  motion: {
    duration: "0.18s",
    easing: "cubic-bezier(0.2, 0, 0, 1)",
  },
};
