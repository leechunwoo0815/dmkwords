import type { ThemeTokens } from "./types";

/**
 * `paint` — 旧「绘本风」复原（2026-11-04 第一百六十四批第二条）
 *
 * 甲方要求保留一套可切回的旧皮肤：暖纸底 + 活力橙 + 大圆角 + 粗描边 + 硬偏移阴影。
 * 用途：① 改造前后 A/B 对比；② 万一新企业风某个页面看着别扭，能立刻切回来定位。
 *
 * **复原范围（如实）**：
 * - 做到了：配色（纸面/墨色/橙绿黄蓝粉紫）、圆角（12/20/24 + 胶囊按钮）、2px 描边、
 *   硬偏移阴影、侧栏 240、Header 64、控件 38、表头米黄底。
 * - **没做到**：手绘字体（ZCOOL KuaiLe/Nunito 两个依赖已删）、水彩纹理、卡通插画、彩色图标圆圈
 *   ——这三样不是"令牌"能表达的，需要单独的组件分支；已按企业风统一为系统字体 + 线性图标。
 *   故本主题是"绘本风的配色与形态"，不是像素级复刻。
 */
export const paint: ThemeTokens = {
  id: "paint",
  scheme: "light",
  name: "绘本风（旧）",
  description: "改造前的暖纸书房：纸面米白、活力橙、大圆角、2px 描边、硬偏移阴影；字体与插画沿用企业风。",
  color: {
    primary: "#FF6B35",
    primaryHover: "#E85A28",
    primaryActive: "#D14A1C",
    primarySoft: "#FFEDE5",
    onPrimary: "#FFFFFF",
    success: "#4ADE80",
    successSoft: "#E0FCEA",
    warning: "#FCD34D",
    warningSoft: "#FFF7D6",
    danger: "#EF4444",
    dangerHover: "#DC2626",
    dangerSoft: "#FEE2E2",
    info: "#60A5FA",
    infoSoft: "#E0F2FE",
    link: "#FF6B35",
    bgLayout: "#FDF8F0",
    bgContainer: "#FFFDF7",
    bgElevated: "#FFFDF7",
    bgSubtle: "#FFF5E6",
    bgHover: "#FFF5E6",
    bgSelected: "#FFEDE5",
    border: "#D4C5B5",
    borderStrong: "#3B2F2F",
    divider: "#E7DACA",
    text: "#3B2F2F",
    textSecondary: "#6B5B5B",
    textTertiary: "#8B7E72",
    textQuaternary: "#A8A8A8",
    textInverse: "#FFFFFF",
    siderBg: "#FFFDF7",
    siderBorder: "#3B2F2F",
    siderText: "#3B2F2F",
    siderTextActive: "#FF6B35",
    headerBg: "#FFFDF7",
    mask: "rgba(59, 47, 47, 0.45)",
    // 旧图表色板：橙 / 绿 / 黄 / 蓝 / 粉 / 紫
    chart: ["#FF6B35", "#4ADE80", "#FCD34D", "#60A5FA", "#F472B6", "#A78BFA"],
  },
  shape: {
    radiusSM: 10,
    radius: 12,
    radiusLG: 20,
    radiusXL: 24,
    // 胶囊按钮是绘本风的标志性形态，单独给按钮开这个口子
    buttonRadius: 999,
    borderWidth: 2,
  },
  shadow: {
    sm: "2px 2px 0 #3B2F2F",
    base: "3px 3px 0 #3B2F2F",
    lg: "5px 5px 0 #3B2F2F",
    modal: "5px 5px 0 #3B2F2F",
    card: "4px 4px 0 rgba(59, 47, 47, 0.08)",
  },
  density: {
    controlHeight: 38,
    controlHeightSM: 30,
    controlHeightLG: 44,
    headerHeight: 64,
    siderWidth: 240,
    contentPadding: 24,
    cardPadding: 20,
    tableHeaderHeight: 44,
    tableRowHeight: 48,
    fontSize: 14,
    fontSizeSM: 12,
    fontSizeLG: 16,
    // 旧版是 700/800，但微软雅黑只有 regular/bold，合成加粗在 Windows 发虚——统一 600
    fontWeightStrong: 600,
  },
  // 手绘体（ZCOOL KuaiLe）+ Nunito：由 `theme/fonts.ts` 在切到本主题时懒加载
  font: {
    body: "'Nunito', 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', system-ui, sans-serif",
    heading: "'ZCOOL KuaiLe', 'Nunito', 'PingFang SC', 'Microsoft YaHei', sans-serif",
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

/**
 * 绘本插画色板（167 批）：PaintEmpty / PaintLoading / 侧栏图标圆圈共用。
 *
 * 这些是**插画固有颜色**，不是主题令牌——只有绘本风会渲染，故不随三套主题切换。
 * 抽到这里是为了让 TSX 零字面色：换项目/换绘本配色只改这个对象，不改组件。
 */
export const paintIllustration = {
  ink: "#3B2F2F",
  paper: "#FFFDF7",
  paperDeep: "#FFF5E6",
  white: "#FFFFFF",
  orange: "#FF6B35",
  green: "#4ADE80",
  yellow: "#FCD34D",
  blue: "#60A5FA",
  pink: "#F472B6",
  purple: "#A78BFA",
  brown: "#8B5E3C",
  red: "#EF4444",
  textSecondary: "#6B5B5B",
} as const;
