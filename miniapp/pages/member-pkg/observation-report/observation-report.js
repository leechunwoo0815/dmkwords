// pages/member-pkg/observation-report/observation-report.js — 观察期评估报告（WM10）
const api = require('../../../utils/api')

Page({
  data: {
    childName: '',
    reports: [],
    loading: true,
    preview: '',
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const reports = await api.observationReports(this._childId)
      this.setData({
        reports: (reports || []).map((r) => ({
          ...r,
          imageUrls: (r.images || []).map((img) => api.observationImageUrl(img)),
        })),
      })
    } catch (e) { /* toast 已弹 */ }
    finally { this.setData({ loading: false }) }
  },

  onPreview(e) {
    this.setData({ preview: e.currentTarget.dataset.src })
  },

  onClosePreview() { this.setData({ preview: '' }) },
})
