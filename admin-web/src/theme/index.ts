/**
 * 主题注册表 + 应用器（第一百六十四批，2026-11-04）
 *
 * 用法（唯一入口，`main.tsx` 开机调用一次）：
 *
 *   applyTheme();                      // 取默认/上次保存的主题，注入 CSS 变量
 *   <ConfigProvider theme={buildAntdTheme(getTheme())}>
 *
 * 新增主题（以后换皮/复用到别的项目）：
 *   1. 在 `presets/` 里加一个 `ThemeTokens` 对象（照 `enterprise.ts` 抄）；
 *   2. 在下面 `THEMES` 注册表里登记；
 *   3. 若要给用户切换入口，调 `applyTheme(id)` 即可（变量 + antd token 同时生效）。
 *
 * 约定：**CSS 只准用这里注入的变量**（`--color-*` / `--radius-*` / `--control-height` 等），
 * 不允许写死颜色或尺寸常量——否则换主题时会出现「一半变了、一半没变」。
 */
import { theme as antdTheme } from "antd";
import type { ThemeConfig } from "antd";

import { dark } from "./dark";
import { enterprise } from "./enterprise";
import { paint } from "./paint";
import type { ThemeTokens } from "./types";

export type { ThemeTokens } from "./types";

/** 主题注册表：key = ThemeTokens.id */
export const THEMES: Record<string, ThemeTokens> = {
  [enterprise.id]: enterprise,
  [paint.id]: paint,
  [dark.id]: dark,
};

/** 主题顺序（切换菜单按此排列）：企业风在前，旧绘本风在后。 */
export const THEME_LIST: ThemeTokens[] = [enterprise, paint, dark];

export const DEFAULT_THEME_ID = enterprise.id;

const STORAGE_KEY = "dmkwords.admin.theme";

/** 读上次选的主题 id（非法/不可用时回落默认）；纯函数式，不碰 DOM。 */
export function resolveThemeId(id?: string | null): string {
  const candidate = id ?? safeRead();
  return candidate && THEMES[candidate] ? candidate : DEFAULT_THEME_ID;
}

function safeRead(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // 隐私模式/禁用存储：不阻断启动，回落默认主题
    return null;
  }
}

/** 取主题对象（缺省 = 默认主题）。 */
export function getTheme(id?: string | null): ThemeTokens {
  // resolveThemeId 已保证 id 在注册表内；`?? enterprise` 只是满足 noUncheckedIndexedAccess 的收窄
  return THEMES[resolveThemeId(id)] ?? enterprise;
}

/**
 * 令牌 → CSS 变量表。
 *
 * 命名口径：
 *  - 颜色 `--color-*`，形状 `--radius-*` / `--border-width`，阴影 `--shadow-*`
 *  - 密度 `--control-height*` / `--table-*` / `--header-height` / `--sider-width`
 *  - 字体 `--font-*` / `--font-size*`，图表 `--chart-1..6`
 */
export function cssVars(t: ThemeTokens): Record<string, string> {
  const { color: c, shape: s, shadow: sh, density: d, font: f, motion: m } = t;
  const vars: Record<string, string> = {
    // 品牌 / 语义色
    "--color-primary": c.primary,
    "--color-primary-hover": c.primaryHover,
    "--color-primary-active": c.primaryActive,
    "--color-primary-soft": c.primarySoft,
    "--color-on-primary": c.onPrimary,
    "--color-success": c.success,
    "--color-success-soft": c.successSoft,
    "--color-warning": c.warning,
    "--color-warning-soft": c.warningSoft,
    "--color-danger": c.danger,
    "--color-danger-hover": c.dangerHover,
    "--color-danger-soft": c.dangerSoft,
    "--color-info": c.info,
    "--color-info-soft": c.infoSoft,
    "--color-link": c.link,
    // 中性色
    "--color-bg-layout": c.bgLayout,
    "--color-bg-container": c.bgContainer,
    "--color-bg-elevated": c.bgElevated,
    "--color-bg-subtle": c.bgSubtle,
    "--color-bg-hover": c.bgHover,
    "--color-bg-selected": c.bgSelected,
    "--color-border": c.border,
    "--color-border-strong": c.borderStrong,
    "--color-divider": c.divider,
    "--color-text": c.text,
    "--color-text-secondary": c.textSecondary,
    "--color-text-tertiary": c.textTertiary,
    "--color-text-quaternary": c.textQuaternary,
    "--color-text-inverse": c.textInverse,
    // 骨架（侧栏 / Header / 遮罩）
    "--color-sider-bg": c.siderBg,
    "--color-sider-border": c.siderBorder,
    "--color-sider-text": c.siderText,
    "--color-sider-text-active": c.siderTextActive,
    "--color-header-bg": c.headerBg,
    "--color-mask": c.mask,
    // 形状
    "--radius-sm": `${s.radiusSM}px`,
    "--radius-base": `${s.radius}px`,
    "--radius-lg": `${s.radiusLG}px`,
    "--radius-xl": `${s.radiusXL}px`,
    "--border-width": `${s.borderWidth}px`,
    // 阴影
    "--shadow-sm": sh.sm,
    "--shadow-base": sh.base,
    "--shadow-lg": sh.lg,
    "--shadow-modal": sh.modal,
    "--shadow-card": sh.card,
    // 密度
    "--control-height": `${d.controlHeight}px`,
    "--control-height-sm": `${d.controlHeightSM}px`,
    "--control-height-lg": `${d.controlHeightLG}px`,
    "--header-height": `${d.headerHeight}px`,
    "--sider-width": `${d.siderWidth}px`,
    "--content-padding": `${d.contentPadding}px`,
    "--card-padding": `${d.cardPadding}px`,
    "--table-header-height": `${d.tableHeaderHeight}px`,
    "--table-row-height": `${d.tableRowHeight}px`,
    // 字体
    "--font-body": f.body,
    "--font-heading": f.heading,
    "--font-mono": f.mono,
    "--font-size": `${d.fontSize}px`,
    "--font-size-sm": `${d.fontSizeSM}px`,
    "--font-size-lg": `${d.fontSizeLG}px`,
    "--font-weight-regular": String(f.weightRegular),
    "--font-weight-medium": String(f.weightMedium),
    "--font-weight-strong": String(f.weightStrong),
    "--letter-spacing-heading": f.letterSpacingHeading,
    // 动效
    "--motion-duration": m.duration,
    "--motion-easing": m.easing,
  };
  c.chart.forEach((value, i) => {
    vars[`--chart-${i + 1}`] = value;
  });
  return vars;
}

/**
 * 应用主题：写 `<html>` 的 CSS 变量 + `data-theme` 属性，并记住选择。
 * 幂等；可重复调用（将来的切换入口只需调它）。
 */
export function applyTheme(id?: string | null): ThemeTokens {
  const theme = getTheme(id);
  const root = document.documentElement;
  for (const [name, value] of Object.entries(cssVars(theme))) {
    root.style.setProperty(name, value);
  }
  root.setAttribute("data-theme", theme.id);
  // 原生控件（滚动条/输入框/日期选择）跟随主题明暗
  root.style.colorScheme = theme.scheme;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme.id);
  } catch {
    // 存不进去不影响本次渲染
  }
  return theme;
}

/**
 * 令牌 → antd ThemeConfig。**不许在这里写死颜色**：全部从令牌取，
 * 保证 antd 组件与自研 CSS 是同一套值（docs/31 §七 记的「双份颜色漂移」风险）。
 */
export function buildAntdTheme(t: ThemeTokens): ThemeConfig {
  const { color: c, shape: s, shadow: sh, density: d, font: f } = t;
  return {
    // 明暗算法交给 antd：深色主题下表格/下拉/弹窗的原生 token 必须走 darkAlgorithm，
    // 否则会出现"卡片是深的、弹窗是白的"这种半深半浅
    algorithm: t.scheme === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: c.primary,
      colorInfo: c.info,
      colorSuccess: c.success,
      colorWarning: c.warning,
      colorError: c.danger,
      colorLink: c.link,
      colorBgLayout: c.bgLayout,
      colorBgContainer: c.bgContainer,
      colorBgElevated: c.bgElevated,
      colorText: c.text,
      colorTextHeading: c.text,
      colorTextSecondary: c.textSecondary,
      colorTextTertiary: c.textTertiary,
      colorTextQuaternary: c.textQuaternary,
      colorBorder: c.border,
      colorBorderSecondary: c.divider,
      colorSplit: c.divider,
      colorFillAlter: c.bgSubtle,
      colorFillSecondary: c.bgHover,
      colorFillTertiary: c.bgSubtle,
      borderRadius: s.radius,
      borderRadiusSM: s.radiusSM,
      borderRadiusLG: s.radiusLG,
      borderRadiusXS: s.radiusSM,
      lineWidth: s.borderWidth,
      lineWidthBold: s.borderWidth + 1,
      controlHeight: d.controlHeight,
      controlHeightSM: d.controlHeightSM,
      controlHeightLG: d.controlHeightLG,
      fontSize: d.fontSize,
      fontSizeSM: d.fontSizeSM,
      fontSizeLG: d.fontSizeLG,
      fontSizeHeading4: 18,
      fontSizeHeading5: 16,
      fontWeightStrong: d.fontWeightStrong,
      fontFamily: f.body,
      fontFamilyCode: f.mono,
      controlOutline: "rgba(51, 112, 255, 0.12)",
      controlOutlineWidth: 2,
      boxShadow: sh.base,
      boxShadowSecondary: sh.lg,
      boxShadowTertiary: sh.card,
      wireframe: false,
      motionDurationMid: "0.15s",
    },
    components: {
      Layout: {
        headerBg: c.headerBg,
        headerHeight: d.headerHeight,
        headerPadding: `0 ${d.contentPadding + 4}px`,
        bodyBg: c.bgLayout,
        siderBg: c.siderBg,
        headerColor: c.text,
        triggerBg: c.bgSubtle,
        triggerColor: c.textSecondary,
      },
      Menu: {
        itemColor: c.siderText,
        itemHoverColor: c.text,
        itemHoverBg: c.bgHover,
        itemSelectedColor: c.siderTextActive,
        itemSelectedBg: c.primarySoft,
        itemHeight: 36,
        itemMarginInline: 8,
        itemMarginBlock: 2,
        itemBorderRadius: s.radius,
        iconSize: 16,
        iconMarginInlineEnd: 10,
        activeBarWidth: 0,
        activeBarBorderWidth: 0,
        subMenuItemBorderRadius: s.radius,
        groupTitleColor: c.textTertiary,
        groupTitleFontSize: d.fontSizeSM,
        collapsedIconSize: 16,
        collapsedWidth: 56,
      },
      Button: {
        fontWeight: f.weightMedium,
        // 阴影跟主题走：企业风是极轻投影，绘本风是硬偏移阴影
        primaryShadow: sh.base,
        defaultShadow: sh.sm,
        dangerShadow: sh.base,
        defaultBg: c.bgContainer,
        defaultColor: c.text,
        defaultBorderColor: c.border,
        primaryColor: c.onPrimary,
        borderRadius: s.buttonRadius,
        borderRadiusSM: s.buttonRadius,
        borderRadiusLG: s.buttonRadius,
        paddingInline: 15,
      },
      Table: {
        headerBg: c.bgSubtle,
        headerColor: c.text,
        headerSplitColor: "transparent",
        headerBorderRadius: 0,
        borderColor: c.border,
        rowHoverBg: c.bgHover,
        rowSelectedBg: c.primarySoft,
        rowSelectedHoverBg: c.primarySoft,
        cellPaddingBlock: 10,
        cellPaddingInline: 12,
        cellPaddingBlockMD: 9,
        cellPaddingInlineMD: 12,
        cellPaddingBlockSM: 7,
        cellPaddingInlineSM: 10,
        fontSize: d.fontSize,
        footerBg: c.bgSubtle,
        stickyScrollBarBg: c.borderStrong,
      },
      Card: {
        headerBg: "transparent",
        headerFontSize: d.fontSize,
        headerHeight: 44,
        paddingLG: d.cardPadding,
        colorBorderSecondary: c.border,
      },
      Modal: {
        headerBg: c.bgElevated,
        contentBg: c.bgElevated,
        titleColor: c.text,
        titleFontSize: 16,
        footerBg: "transparent",
        borderRadiusLG: s.radiusLG,
      },
      Drawer: {
        colorBgElevated: c.bgElevated,
        footerPaddingBlock: 12,
        footerPaddingInline: 20,
      },
      Tabs: {
        inkBarColor: c.primary,
        itemColor: c.textSecondary,
        itemSelectedColor: c.text,
        itemHoverColor: c.text,
        horizontalItemPadding: "8px 0",
        horizontalItemGutter: 24,
        horizontalMargin: "0 0 12px 0",
        titleFontSize: d.fontSize,
        cardBg: c.bgSubtle,
      },
      Tag: {
        defaultBg: c.bgSubtle,
        defaultColor: c.textSecondary,
        borderRadiusSM: s.radiusSM,
        fontSizeSM: d.fontSizeSM,
      },
      Input: {
        hoverBorderColor: c.borderStrong,
        activeBorderColor: c.primary,
        activeShadow: "0 0 0 2px rgba(51, 112, 255, 0.12)",
        paddingInline: 11,
      },
      InputNumber: {
        hoverBorderColor: c.borderStrong,
        activeBorderColor: c.primary,
        activeShadow: "0 0 0 2px rgba(51, 112, 255, 0.12)",
      },
      Select: {
        optionSelectedBg: c.primarySoft,
        optionSelectedColor: c.text,
        optionActiveBg: c.bgHover,
        optionHeight: 30,
        selectorBg: c.bgContainer,
      },
      DatePicker: {
        hoverBorderColor: c.borderStrong,
        activeBorderColor: c.primary,
        activeShadow: "0 0 0 2px rgba(51, 112, 255, 0.12)",
        cellHoverBg: c.bgHover,
        cellActiveWithRangeBg: c.primarySoft,
      },
      Form: {
        labelColor: c.text,
        labelFontSize: d.fontSizeSM + 1,
        labelHeight: 26,
        itemMarginBottom: 16,
        verticalLabelPadding: "0 0 4px",
      },
      Pagination: {
        itemActiveBg: c.primary,
        itemBg: "transparent",
        itemSize: 28,
        itemSizeSM: 24,
        colorPrimary: c.primary,
      },
      Segmented: {
        trackBg: c.bgSubtle,
        trackPadding: 2,
        itemColor: c.textSecondary,
        itemHoverColor: c.text,
        itemHoverBg: c.bgHover,
        itemSelectedBg: c.bgContainer,
        itemSelectedColor: c.text,
      },
      Tooltip: {
        colorBgSpotlight: "rgba(31, 35, 41, 0.92)",
        borderRadius: s.radius,
      },
      Dropdown: {
        paddingBlock: 4,
        controlItemBgHover: c.bgHover,
        borderRadiusLG: s.radiusLG,
      },
      Descriptions: {
        labelBg: c.bgSubtle,
        titleMarginBottom: 8,
        itemPaddingBottom: 8,
      },
      Statistic: {
        contentFontSize: 20,
      },
      Alert: {
        borderRadiusLG: s.radius,
      },
      Divider: {
        colorSplit: c.divider,
      },
      Empty: {
        colorTextDescription: c.textTertiary,
      },
      Typography: {
        titleMarginTop: 0,
        titleMarginBottom: "0.5em",
      },
      Upload: {
        actionsColor: c.primary,
      },
      Badge: {
        dotSize: 6,
      },
      Steps: {
        dotSize: 8,
      },
      Popover: {
        titleMinWidth: 0,
      },
      Radio: {
        buttonSolidCheckedBg: c.primary,
      },
      Switch: {
        colorPrimary: c.primary,
        colorPrimaryHover: c.primaryHover,
      },
      Progress: {
        defaultColor: c.primary,
      },
      Spin: {
        colorPrimary: c.primary,
      },
    },
  };
}
