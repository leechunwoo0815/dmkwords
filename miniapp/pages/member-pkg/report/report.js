// pages/member-pkg/report/report.js — 周报/月报（WM8/C1）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

Page({
  data: {
    childName: '',
    kind: 'weekly',
    kindLabel: '周报',
    report: null,
    imageUrl: '',
    loadError: '',  // P2-45：失败态（原先失败静默 → 永久"生成中"）
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    // P3（第一百一十三批）：统一解析（原 `Number(undefined)` → NaN → 早退 → 永久「生成中」）
    this._childId = session.resolveChildId(options)
  },

  onShow() { this.load() },

  onRetryLoad() { this.load() },

  switchKind(e) {
    const kind = e.currentTarget.dataset.kind
    if (kind === this.data.kind) return
    this.setData({ kind, kindLabel: kind === 'weekly' ? '周报' : '月报' })
    this.load()
  },

  /** 点报告图 → 原生预览（双指放大看清小字 / 长按保存）；docs/15 §十九 放大出口红线 */
  onPreviewReport() {
    if (!this.data.imageUrl) return
    wx.previewImage({ urls: [this.data.imageUrl], current: this.data.imageUrl })
  },

  async load() {
    if (!this._childId) {
      this.setData({ noChild: true, loadError: '', report: null })
      return
    }
    // P2-45：切换周/月报失败时**清空旧数据**（否则月报 tab 挂着周报的数字与图）
    this.setData({ loadError: '', report: null, imageUrl: '', noChild: false })
    try {
      const r = await api.report(this.data.kind, this._childId)
      const imgUrl = api.reportImageUrl(this.data.kind, this._childId)
      this.setData({ report: r, imageUrl: imgUrl })
    } catch (e) {
      // 原先 catch 空转 → 页面永久显示"生成中，请稍后……"（家长以为系统在算）
      this.setData({ loadError: (e && e.message) || '加载失败' })
    }
  },
})
