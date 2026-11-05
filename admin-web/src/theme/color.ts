// admin-web/src/theme/color.ts — 颜色派生纯函数（171 批从 theme/index.ts 抽出）
//
// P2-7：焦点光圈等半透明色必须从主题主色派生，不能三套主题共用飞书蓝。
// 抽成纯函数便于单测（tests/themeColor.test.ts）。

/** #RRGGBB → rgba(...)；非 6 位 hex（未来可能用 rgb()/hsl()）原样退回，不猜。 */
export function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(color);
  if (!m) return color;
  const n = Number.parseInt(m[1] ?? "", 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
