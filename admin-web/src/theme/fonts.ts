/**
 * 按主题懒加载字体（第一百六十四批补做）
 *
 * 企业风只用系统字体 → **不下载任何字体文件**；只有切到「绘本风」时才拉
 * ZCOOL KuaiLe（手绘标题）+ Nunito（正文拉丁）。这样默认路径零字体开销，
 * 又能在切到旧皮肤时真正还原手绘体（而不是回退微软雅黑）。
 *
 * 失败不影响使用：CSS 里字体栈已带 `PingFang SC / Microsoft YaHei` 兜底，
 * Fontsource 的 CSS 均带 `font-display: swap`（加载期先显示系统字体）。
 *
 * 只加载实际用到的三档字重（400/500/600）：700/800 原先一并拉取但主题已统一
 * 用 600（避免 Windows 合成加粗发虚），170 批去掉，减少弱网首切体积。
 */
let started = false;

export function loadPaintFonts(): void {
  if (started) return;
  started = true;
  void Promise.all([
    import("@fontsource/zcool-kuaile/chinese-simplified-400.css"),
    import("@fontsource/zcool-kuaile/latin-400.css"),
    import("@fontsource/nunito/400.css"),
    import("@fontsource/nunito/500.css"),
    import("@fontsource/nunito/600.css"),
  ]).catch(() => {
    // 字体加载失败只影响观感，不阻断页面（回退系统字体）
  });
}
