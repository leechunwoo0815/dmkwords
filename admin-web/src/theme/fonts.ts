/**
 * 按主题懒加载字体（第一百六十四批补做）
 *
 * 企业风只用系统字体 → **不下载任何字体文件**；只有切到「绘本风」时才拉
 * ZCOOL KuaiLe（手绘标题）+ Nunito（正文拉丁）。这样默认路径零字体开销，
 * 又能在切到旧皮肤时真正还原手绘体（而不是回退微软雅黑）。
 *
 * 失败不影响使用：CSS 里字体栈已带 `PingFang SC / Microsoft YaHei` 兜底。
 */
let started = false;

export function loadPaintFonts(): void {
  if (started) return;
  started = true;
  void Promise.all([
    import("@fontsource/zcool-kuaile/chinese-simplified-400.css"),
    import("@fontsource/zcool-kuaile/latin-400.css"),
    import("@fontsource/nunito/400.css"),
    import("@fontsource/nunito/600.css"),
    import("@fontsource/nunito/700.css"),
    import("@fontsource/nunito/800.css"),
  ]).catch(() => {
    // 字体加载失败只影响观感，不阻断页面（回退系统字体）
  });
}
