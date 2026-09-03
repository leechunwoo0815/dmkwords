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
    // F-L5/T34：加载未完成/失败（passport null）时分享兜底，防 null-deref
    if (!p || !p.child_name) {
      return { title: '少儿英语阅读馆', path: '/pages/index/index' }
    }
    return {
      title: `${p.child_name} 已经有效阅读 ${p.words_total} 词啦！`,
      path: '/pages/index/index',
    }
  },
})
