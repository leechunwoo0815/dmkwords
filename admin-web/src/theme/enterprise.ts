import type { ThemeTokens } from "./types";

/**
 * `enterprise` — 企业级主题（默认，甲方 2026-11-04 选定）
 *
 * 视觉参照：飞书 / 钉钉后台。目标场景是 **Windows 浏览器为主的馆内经营后台**，
 * 所以：系统字体栈（Segoe UI + 微软雅黑）、1px 描边、小圆角、极轻阴影、
 * 紧凑密度 32px 控件；不用手绘字体、胶囊按钮、2px 粗描边与硬偏移阴影。
 *
 * 改这个对象会影响全站（antd 组件 + 自研 CSS + 图表色板），不需要动任何组件。
 */
export const enterprise: ThemeTokens = {
  id: "enterprise",
  scheme: "light",
  name: "企业·飞书蓝",
  description: "飞书/钉钉式企业级后台：飞书蓝 #3370FF、紧凑 32px、1px 描边、轻阴影、系统字体。",
  color: {
    primary: "#3370FF",
    primaryHover: "#245BDB",
    primaryActive: "#1A47B8",
    primarySoft: "#E1EAFF",
    onPrimary: "#FFFFFF",
    success: "#00B42A",
    successSoft: "#E8FFEA",
    warning: "#FF7D00",
    warningSoft: "#FFF7E8",
    danger: "#F53F3F",
    dangerHover: "#CB2634",
    dangerSoft: "#FFECE8",
    info: "#3491FA",
    infoSoft: "#E8F7FF",
    link: "#3370FF",
    bgLayout: "#F5F6F7",
    bgContainer: "#FFFFFF",
    bgElevated: "#FFFFFF",
    bgSubtle: "#F7F8FA",
    bgHover: "#F2F3F5",
    bgSelected: "#E1EAFF",
    border: "#E5E6EB",
    borderStrong: "#C9CDD4",
    divider: "#F0F1F2",
    text: "#1F2329",
    textSecondary: "#646A73",
    textTertiary: "#8F959E",
    textQuaternary: "#C9CDD4",
    textInverse: "#FFFFFF",
    siderBg: "#FFFFFF",
    siderBorder: "#E5E6EB",
    siderText: "#4E5969",
    siderTextActive: "#3370FF",
    headerBg: "#FFFFFF",
    mask: "rgba(0, 0, 0, 0.45)",
    chart: ["#3370FF", "#00B42A", "#FF7D00", "#722ED1", "#14C9C9", "#F53F3F"],
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
    sm: "0 1px 2px rgba(31, 35, 41, 0.04)",
    base: "0 1px 3px rgba(31, 35, 41, 0.06)",
    lg: "0 4px 12px rgba(31, 35, 41, 0.08)",
    modal: "0 8px 24px rgba(31, 35, 41, 0.12)",
    card: "0 1px 2px rgba(31, 35, 41, 0.04)",
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
    fontSize: 14,
    fontSizeSM: 12,
    fontSizeLG: 16,
    fontWeightStrong: 600,
  },
  font: {
    // Windows 优先：Segoe UI（数字/拉丁）+ 微软雅黑（中文）；macOS 落到苹方。
    // 只用 400/500/600 三档——微软雅黑只有 regular/bold，700/800 会被合成加粗发虚。
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
    duration: "0.15s",
    easing: "cubic-bezier(0.2, 0, 0, 1)",
  },
};
