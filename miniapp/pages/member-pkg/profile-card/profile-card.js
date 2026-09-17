// pages/member-pkg/profile-card/profile-card.js — 阅读护照（WM8）
const api = require('../../../utils/api')

const DEFAULT_AVATAR = '/icons/avatars/cat_sun.png'

Page({
  data: {
    childName: '',
    passport: null,
    avatarUrl: DEFAULT_AVATAR,
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
      // 2026-09-17：护照头像此前是写死的 emoji（🧒/📚），孩子换头像后这里不跟着变
      // （用户实测「头像没同步」）。/api/miniapp/passport 本来就回 avatar，
      // 口径与「我的」「阅读名片」两页一致，不新增接口。
      this.setData({
        passport: p,
        avatarUrl: p.avatar ? `/icons/avatars/${p.avatar}.png` : DEFAULT_AVATAR,
      })
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

  // WM14-A：我的成就（历史成就补晒入口）
  goMyCards() {
    if (!this._childId) return
    const name = this.data.childName || (this.data.passport ? this.data.passport.child_name : '')
    wx.navigateTo({
      url: `/pages/circle/share?child_id=${this._childId}&child_name=${encodeURIComponent(name || '')}`,
    })
  },
})
