// pages/member-pkg/profile-card/profile-card.js — 阅读护照（WM8）
const api = require('../../../utils/api')

Page({
  data: {
    childName: '',
    passport: null,
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
  },

  onShow() { this.load() },

  async load() {
    if (!this._childId) return
    try {
      const p = await api.passport(this._childId)
      this.setData({ passport: p })
    } catch (e) { /* request.js 已 toast */ }
  },

  onShareAppMessage() {
    const p = this.data.passport
    return {
      title: `${p.child_name} 已经有效阅读 ${p.words_total} 词啦！`,
      path: '/pages/index/index',
    }
  },
})
