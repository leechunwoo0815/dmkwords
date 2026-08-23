// pages/member-pkg/report/report.js — 周报/月报（WM8/C1）
const api = require('../../../utils/api')

Page({
  data: {
    childName: '',
    kind: 'weekly',
    kindLabel: '周报',
    report: null,
    imageUrl: '',
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
  },

  onShow() { this.load() },

  switchKind(e) {
    const kind = e.currentTarget.dataset.kind
    if (kind === this.data.kind) return
    this.setData({ kind, kindLabel: kind === 'weekly' ? '周报' : '月报' })
    this.load()
  },

  async load() {
    if (!this._childId) return
    try {
      const r = await api.report(this.data.kind, this._childId)
      const imgUrl = api.reportImageUrl(this.data.kind, this._childId)
      this.setData({ report: r, imageUrl: imgUrl })
    } catch (e) { /* request.js 已 toast */ }
  },
})
