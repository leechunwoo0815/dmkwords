// pages/member-pkg/observation-report/observation-report.js — 观察期评估报告（WM10）
// 2026-09-21 重排（用户报障「看不清/太简陋」）：竖版整页展示 + 原生缩放预览 + 老师评语 + 往期折叠。
// 展示口径：docs/15 §十九 报告类图片展示规范（整页 mode=widthFix / 放大出口必须走 wx.previewImage）。
const api = require('../../../utils/api')

/** 后端给的是 `str(datetime)`（"2026-09-21 15:04:05.123456"）——只取年月日，别原样糊给家长看。 */
function fmtDate(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return String(s || '')
  return `${Number(m[1])}年${Number(m[2])}月${Number(m[3])}日`
}

Page({
  data: {
    childName: '',
    reports: [], // 全部期次（上传时间倒序）
    latest: null, // 最新一期（整页展开）
    history: [], // 往期（默认收起）
    historyOpen: false,
    loading: true,
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const rows = await api.observationReports(this._childId)
      const list = (rows || []).map((r) => ({
        ...r,
        dateText: fmtDate(r.created_at),
        imageUrls: (r.images || []).map((img) => api.observationImageUrl(img)),
      }))
      // 期次口径（docs/15 §十九）：按**上传先后**编号，最早 = 第 1 期；后端 id 倒序 → 倒着编
      const total = list.length
      const withIssue = list.map((r, i) => ({ ...r, issue: total - i }))
      this.setData({
        reports: withIssue,
        latest: withIssue[0] || null,
        history: withIssue.slice(1),
      })
    } catch (e) { /* request.js 已 toast */ }
    finally { this.setData({ loading: false }) }
  },

  /** 最新一期：点第 index 页 → 原生预览（双指放大 / 左右翻页 / 长按保存） */
  onPreviewLatest(e) {
    this._preview(this.data.latest, Number(e.currentTarget.dataset.index || 0))
  },

  /** 往期：点某期的第 index 页 → 原生预览，urls 给整期，进预览后可左右翻 */
  onPreviewHistory(e) {
    const { report, index } = e.currentTarget.dataset
    const rep = this.data.history.find((r) => String(r.id) === String(report))
    this._preview(rep, Number(index || 0))
  },

  _preview(report, index) {
    if (!report || !report.imageUrls || !report.imageUrls.length) return
    wx.previewImage({
      current: report.imageUrls[index] || report.imageUrls[0],
      urls: report.imageUrls,
    })
  },

  toggleHistory() {
    this.setData({ historyOpen: !this.data.historyOpen })
  },
})
