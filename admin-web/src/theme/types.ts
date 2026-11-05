/**
 * 管理端主题令牌契约（第一百六十四批，2026-11-04）
 *
 * 设计口径（甲方决定）：
 *  - 一套令牌 = 一套主题；**新增主题只需在 `theme/<preset>.ts` 里加一个 ThemeTokens 对象**
 *    （照 `enterprise.ts` 抄），不需要改任何组件/CSS。
 *  - 令牌是**唯一色板来源**：antd `ConfigProvider` 的 ThemeConfig 与 CSS 变量都由
 *    本文件的结构派生（`cssVars()` / `buildAntdTheme()`），不允许在 CSS 里再写死颜色。
 *  - 控件密度由 `density` 表达：紧凑 32px（甲方 2026-11-04 选定），不再是「舒适 36–38px」。
 *
 * 为什么要这层抽象：原先是「antd token + paint.css 手写颜色 + TSX 内联 hex」三份并行，
 * 换皮要改 20 个页面（见 docs/31 §一 审计：paint.css 917 行 / 135 个 !important /
 * TSX 221 处 hex）。收敛成契约后，换皮 = 换一个对象。
 */

/** 品牌与语义色。所有值必须是可直接写进 CSS / antd token 的颜色字符串。 */
export interface ThemeColorTokens {
  /** 主色（品牌色）：主按钮、选中态、链接、焦点环 */
  primary: string;
  primaryHover: string;
  primaryActive: string;
  /** 主色浅底：选中行、浅色标签、聚焦态背景 */
  primarySoft: string;
  /** 主色之上文字/图标颜色 */
  onPrimary: string;
  success: string;
  successSoft: string;
  warning: string;
  warningSoft: string;
  danger: string;
  dangerHover: string;
  dangerSoft: string;
  info: string;
  infoSoft: string;
  link: string;
  /** 页面底色 */
  bgLayout: string;
  /** 卡片/表格/容器底色 */
  bgContainer: string;
  /** 浮层底色（下拉、弹窗、气泡） */
  bgElevated: string;
  /** 表头、次级区块、只读区底色 */
  bgSubtle: string;
  /** hover 底色 */
  bgHover: string;
  /** 选中底色 */
  bgSelected: string;
  border: string;
  /** 需要强调的分割/描边（输入框 hover 等） */
  borderStrong: string;
  divider: string;
  text: string;
  textSecondary: string;
  textTertiary: string;
  textQuaternary: string;
  textInverse: string;
  siderBg: string;
  siderBorder: string;
  siderText: string;
  siderTextActive: string;
  headerBg: string;
  /** 遮罩（弹窗/抽屉底） */
  mask: string;
  /** 图表色板（按顺序取用，至少 6 色） */
  chart: string[];
}

/** 形态：圆角与描边粗细。企业风 = 小圆角 + 1px。 */
export interface ThemeShapeTokens {
  radiusSM: number;
  radius: number;
  radiusLG: number;
  radiusXL: number;
  /** 按钮专用圆角：企业风 6px；绘本风要复刻"胶囊按钮"就填 999 */
  buttonRadius: number;
  borderWidth: number;
}

/** 阴影：企业风 = 极轻，禁止硬偏移阴影。 */
export interface ThemeShadowTokens {
  sm: string;
  base: string;
  lg: string;
  modal: string;
  /** 卡片专用（比 base 略淡） */
  card: string;
}

/** 密度：控件高度、行高、间距、字号。 */
export interface ThemeDensityTokens {
  controlHeight: number;
  controlHeightSM: number;
  controlHeightLG: number;
  headerHeight: number;
  siderWidth: number;
  contentPadding: number;
  cardPadding: number;
  tableHeaderHeight: number;
  tableRowHeight: number;
  fontSize: number;
  fontSizeSM: number;
  fontSizeLG: number;
  fontWeightStrong: number;
}

/** 字体：全部走系统字体栈（Windows 优先 Segoe UI + 微软雅黑）。 */
export interface ThemeFontTokens {
  body: string;
  heading: string;
  mono: string;
  weightRegular: number;
  weightMedium: number;
  weightStrong: number;
  letterSpacingHeading: string;
}

export interface ThemeMotionTokens {
  duration: string;
  easing: string;
}

export interface ThemeTokens {
  /** 稳定 id：会写进 `<html data-theme>` 与 localStorage */
  id: string;
  name: string;
  /** 明暗基调：决定 antd 用 defaultAlgorithm 还是 darkAlgorithm，以及原生控件配色 */
  scheme: "light" | "dark";
  /** 说明：给后续维护者看「这套主题为什么长这样」 */
  description: string;
  color: ThemeColorTokens;
  shape: ThemeShapeTokens;
  shadow: ThemeShadowTokens;
  density: ThemeDensityTokens;
  font: ThemeFontTokens;
  motion: ThemeMotionTokens;
}
