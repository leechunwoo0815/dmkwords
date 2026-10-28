/** 时间显示统一口径（B8，2026-10-28）：后端下发 ISO（带 `T`），管理端一律显示 `YYYY-MM-DD HH:mm`。 */

export function fmtDateTime(value?: string | null): string {
  return value ? value.replace("T", " ").slice(0, 16) : "—";
}

export function fmtDate(value?: string | null): string {
  return value ? value.replace("T", " ").slice(0, 10) : "—";
}
