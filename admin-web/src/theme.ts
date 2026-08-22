/**
 * DmkWords 管理端主题 — 「暖纸书房」设计语言 v2 (2026-08-18)
 * 与 design-reuse/pcweb/css/base.css、miniapp/app.wxss 三端同源。
 *
 * 用法（新项目 admin-web/src/theme.ts）：
 *   <ConfigProvider theme={appTheme}> ... </ConfigProvider>
 *
 * 概念：布面精装书的牛津蓝 × 书页暖纸 × 书脊烫金 × 绘本印刷色
 * 去 AI 味三原则：
 *   1) 暖纸底色（拒绝冷灰白 SaaS 底）
 *   2) 硬边偏移阴影（拒绝千篇一律弥散阴影）— 见 hardShadow 常量
 *   3) 衬线标题字体（拒绝全无衬线模板脸）
 */

// 品牌常量（CSS-in-JS / styled-components / tailwind config 共用）
export const ink = '#262419'; // 暖炭墨
export const paper = {
  bg: '#f6f2e9', // 暖纸米白
  surface: '#fffefa', // 暖白纸面
  dim: '#f0eadc', // 奶油深档
  border: '#e4dcc8', // 暖沙边
  stripe: '#f4efe2', // 斑马纹
};
export const brand = {
  primary: '#2c4a6e', // 牛津布面蓝
  primaryHover: '#1f3a59',
  primarySoft: '#e6edf5', // 蓝染纸痕
  secondary: '#e8945a', // 蜜杏橙（绘本活力，仅装饰）
  secondaryInk: '#9d5c22', // 蜜杏橙文字档（对比度达标）
  success: '#35703c', // 叶绿
  warning: '#8d6610', // 深芥末
  error: '#b54028', // 砖红（番茄印刷红）
  info: '#2f6b8a', // 湖蓝
  gold: '#c9a227', // 书脊金（成就/等级专用）
  goldSoft: '#f6edd3',
};
export const hardShadow = '3px 3px 0 rgba(38, 36, 25, 0.12)';
export const hardShadowSm = '2px 2px 0 rgba(38, 36, 25, 0.10)';
export const hardShadowLg = '5px 5px 0 rgba(38, 36, 25, 0.10)';
export const focusRing = '0 0 0 3px rgba(44, 74, 110, 0.16)';
export const fontDisplay =
  "Georgia, 'Times New Roman', 'Songti SC', 'STSong', 'SimSun', 'Noto Serif SC', serif";
export const fontBody =
  "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', system-ui, sans-serif";

export const appTheme = {
  token: {
    // 主色 — 牛津布面蓝
    colorPrimary: brand.primary,
    colorInfo: brand.info,
    colorSuccess: brand.success,
    colorWarning: brand.warning,
    colorError: brand.error,
    colorLink: brand.primary,

    // 纸面
    colorBgLayout: paper.bg,
    colorBgContainer: paper.surface,
    colorBgElevated: paper.surface,

    // 文字 — 暖炭墨
    colorTextBase: ink,
    colorTextSecondary: '#5d5748',
    colorTextTertiary: '#6f685a',
    colorTextQuaternary: '#a39a86',
    colorBorder: paper.border,
    colorBorderSecondary: '#ece5d4',

    // 圆角 — 编辑感收紧一档
    borderRadius: 10,
    borderRadiusSM: 7,
    borderRadiusLG: 14,

    // 线条与焦点
    lineWidth: 1,
    controlOutline: 'rgba(44, 74, 110, 0.16)',
    controlOutlineWidth: 3,

    // 字体 — 正文无衬线，标题处组件用 fontDisplay 覆盖
    fontFamily: fontBody,
    fontSizeHeading: 15,

    // 阴影 — 弥散极轻（硬边阴影在 components 级按需加）
    boxShadow: '0 1px 2px rgba(38, 36, 25, 0.05), 0 4px 12px rgba(38, 36, 25, 0.05)',
    boxShadowSecondary:
      '0 2px 4px rgba(38, 36, 25, 0.05), 0 12px 28px rgba(38, 36, 25, 0.07)',
  },
  components: {
    Button: {
      // 实体按压感：主按钮硬边偏移阴影
      primaryShadow: hardShadow,
      defaultShadow: hardShadowSm,
      dangerShadow: hardShadow,
      fontWeight: 600,
      controlHeight: 34,
    },
    Table: {
      // 暖纸表头 + 衬线小 caps
      headerBg: paper.dim,
      headerColor: '#6f685a',
      rowHoverBg: '#dde7f2',
      borderColor: paper.border,
      headerBorderRadius: 10,
    },
    Card: {
      // 纸面卡片：暖沙边框 + 极轻弥散
      colorBorderSecondary: paper.border,
      borderRadiusLG: 14,
    },
    Tag: {
      // 印刷贴纸感：soft 底 + 深字（默认实心改淡）
      defaultBg: brand.primarySoft,
      defaultColor: brand.primary,
    },
    Modal: {
      // 桌上的一册书
      borderRadiusLG: 14,
    },
    Tabs: {
      // 书脊条下划线
      inkBarColor: brand.primary,
      horizontalItemPadding: '10px 18px',
    },
    Segmented: {
      itemSelectedBg: brand.primarySoft,
      trackBg: paper.dim,
      borderRadius: 7,
    },
    Pagination: {
      itemActiveBg: brand.primary,
    },
    Layout: {
      // 侧栏 = 纸面
      siderBg: paper.surface,
      headerBg: 'rgba(240, 234, 220, 0.92)',
      bodyBg: paper.bg,
    },
    Menu: {
      // 蓝染纸痕激活态 + 左书脊条
      itemSelectedBg: brand.primarySoft,
      itemSelectedColor: brand.primary,
      activeBarHeight: 3,
      activeBarBorderWidth: 0,
    },
    Tooltip: {
      colorBgSpotlight: ink,
    },
  },
} as const;

export default appTheme;
