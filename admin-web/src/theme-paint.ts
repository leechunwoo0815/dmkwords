/**
 * DmkWords 管理端主题 — 「绘本风」设计语言 v3 (2026-08-25)
 * 核心：形态 > 颜色 > 字体。大圆角、粗边框、硬边偏移阴影、水彩纹理。
 */

export const ink = '#3B2F2F';
export const inkLight = '#6B5B5B';
export const canvas = '#FDF8F0';
export const paper = {
  bg: '#FDF8F0',
  surface: '#FFFDF7',
  dim: '#FFF5E6',
  border: '#3B2F2F',
  stripe: '#FFF5E6',
};

export const palette = {
  primary: '#FF6B35',      // 活力橙
  primaryHover: '#E85A28',
  primarySoft: '#FFEDE5',
  secondary: '#4ADE80',    // 嫩芽绿
  secondaryHover: '#36C96C',
  secondarySoft: '#E0FCEA',
  yellow: '#FCD34D',       // 太阳黄
  yellowSoft: '#FFF7D6',
  blue: '#60A5FA',         // 天空蓝
  blueSoft: '#E0F2FE',
  pink: '#F472B6',         // 樱花粉
  pinkSoft: '#FCE7F3',
  purple: '#A78BFA',       // 薰衣草紫
  purpleSoft: '#F3E8FF',
  danger: '#EF4444',       // 番茄红
  dangerHover: '#DC2626',
  dangerSoft: '#FEE2E2',
  brown: '#8B5E3C',        // 借阅熊棕
};

const hardShadow = `3px 3px 0 ${ink}`;
const hardShadowSm = `2px 2px 0 ${ink}`;
export const cardShadow = `4px 4px 0 rgba(59, 47, 47, 0.08)`;

export const fontDisplay =
  "'ZCOOL KuaiLe', 'Nunito', 'PingFang SC', 'Microsoft YaHei', sans-serif";
export const fontBody =
  "'Nunito', 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', system-ui, sans-serif";

export const watercolorTexture =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='100'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.08'/%3E%3C/svg%3E\")";

export const paintTheme = {
  token: {
    // 绘本调色板
    colorPrimary: palette.primary,
    colorInfo: palette.blue,
    colorSuccess: palette.secondary,
    colorWarning: palette.yellow,
    colorError: palette.danger,
    colorLink: palette.blue,

    // 纸面
    colorBgLayout: paper.bg,
    colorBgContainer: paper.surface,
    colorBgElevated: paper.surface,

    // 文字
    colorTextBase: ink,
    colorTextSecondary: inkLight,
    colorTextTertiary: '#8B7E72',
    colorTextQuaternary: '#A8A8A8',
    colorBorder: paper.border,
    colorBorderSecondary: '#D4C5B5',

    // 大圆角
    borderRadius: 12,
    borderRadiusSM: 10,
    borderRadiusLG: 20,
    borderRadiusXS: 8,

    // 粗线条
    lineWidth: 2,
    controlOutline: 'rgba(255, 107, 53, 0.2)',
    controlOutlineWidth: 3,

    // 字体
    fontFamily: fontBody,
    fontSizeHeading: 15,

    // 阴影硬边化
    boxShadow: '0 1px 2px rgba(59, 47, 47, 0.05), 0 4px 12px rgba(59, 47, 47, 0.05)',
    boxShadowSecondary:
      '0 2px 4px rgba(59, 47, 47, 0.05), 0 12px 28px rgba(59, 47, 47, 0.07)',
  },
  components: {
    Button: {
      primaryShadow: hardShadow,
      defaultShadow: hardShadowSm,
      dangerShadow: hardShadow,
      fontWeight: 700,
      controlHeight: 38,
      controlHeightLG: 44,
      borderRadius: 999,
      borderRadiusSM: 999,
      borderRadiusLG: 999,
    },
    Table: {
      headerBg: paper.dim,
      headerColor: ink,
      rowHoverBg: '#FFF5E6',
      borderColor: 'transparent',
      headerBorderRadius: 12,
    },
    Card: {
      colorBorderSecondary: paper.border,
      borderRadiusLG: 20,
    },
    Tag: {
      defaultBg: palette.primarySoft,
      defaultColor: palette.primary,
      borderRadiusSM: 999,
    },
    Modal: {
      borderRadiusLG: 24,
      headerBg: paper.surface,
      titleColor: ink,
      titleFontSize: 18,
    },
    Tabs: {
      inkBarColor: palette.primary,
      horizontalItemPadding: '10px 18px',
      borderRadius: 12,
    },
    Segmented: {
      itemSelectedBg: palette.primarySoft,
      itemSelectedColor: palette.primary,
      trackBg: paper.dim,
      borderRadius: 12,
    },
    Pagination: {
      itemActiveBg: palette.primary,
      itemActiveColor: '#fff',
      borderRadius: 10,
    },
    Layout: {
      siderBg: paper.surface,
      headerBg: 'rgba(255, 253, 247, 0.96)',
      bodyBg: paper.bg,
    },
    Menu: {
      itemSelectedBg: palette.primarySoft,
      itemSelectedColor: ink,
      activeBarHeight: 0,
      activeBarBorderWidth: 0,
      itemColor: ink,
      itemHoverColor: palette.primary,
      itemHoverBg: 'transparent',
      iconSize: 18,
      iconMarginInlineEnd: 12,
    },
    Tooltip: {
      colorBgSpotlight: ink,
    },
    Input: {
      borderRadius: 12,
      borderRadiusLG: 12,
      borderRadiusSM: 10,
      hoverBorderColor: ink,
      activeBorderColor: ink,
    },
    Select: {
      borderRadius: 12,
    },
    Form: {
      labelColor: ink,
      labelFontSize: 14,
    },
    Upload: {
      actionsColor: palette.primary,
    },
    Empty: {
      colorTextDescription: inkLight,
      colorTextHeading: ink,
    },
  },
} as const;

export default paintTheme;
